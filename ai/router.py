"""AI Router — BULLETPROOF routing with aggressive retries.

Design principles:
  - NEVER give up: try every provider multiple times
  - Fallback response if EVERYTHING fails — bot ALWAYS responds
  - Smart retry: if provider fails, wait briefly and retry
  - Circuit breaker: skip providers that are definitely down
"""
import logging
import asyncio
import time
from typing import Any, Dict, List, Optional

from ai.providers.base import AIResponse, ProviderError
from ai.providers.chutes_provider import ChutesProvider
from ai.providers.pollinations_provider import PollinationsProvider
from ai.providers.openai_compat_provider import OpenAICompatProvider
from ai.voice import transcribe_voice_ogg
from bot.config import (
    PROVIDER_CHAIN, PROVIDER_TIMEOUTS,
    OPENROUTER_API_KEY, GROQ_API_KEY, CEREBRAS_API_KEY,
    SAMBANOVA_API_KEY, MISTRAL_API_KEY,
)

logger = logging.getLogger(__name__)


class AllProvidersExhaustedError(Exception):
    pass


# Fallback responses — used when ALL AI providers fail
# Bot ALWAYS responds, never leaves user hanging!
FALLBACK_RESPONSES = [
    "Ммм... Настя задумалась. Повтори? 🤔",
    "Ой, Настя отвлеклась... Что ты сказал? 😅",
    "Блин, Настя задумалась о вечном... Ещё раз? 💅",
    "Настя не расслышала... Говори ещё! 😏",
    "Ой, Настя на секунду улетела в мечты! Повтори? ✨",
    "А? Настя думала о шопинге... Что хотела сказать? 👜",
]

# (name, base_url, model, api_key)
PROVIDER_CONFIGS = [
    ("groq", "https://api.groq.com", "llama-3.3-70b-versatile", GROQ_API_KEY),
    ("openrouter", "https://openrouter.ai", "google/gemma-4-31b-it:free", OPENROUTER_API_KEY),
    ("cerebras", "https://api.cerebras.ai", "llama-4-scout-17b-16e-instruct", CEREBRAS_API_KEY),
    ("sambanova", "https://api.sambanova.ai", "Meta-Llama-3.3-70B-Instruct", SAMBANOVA_API_KEY),
    ("mistral", "https://api.mistral.ai", "mistral-small-latest", MISTRAL_API_KEY),
]


class AIRouter:
    def __init__(self):
        self.providers: Dict[str, Any] = {}
        self._chain: List[str] = []
        self._vision_providers: List[str] = []
        # Circuit breaker: track failures per provider
        self._fail_counts: Dict[str, int] = {}
        self._last_fail: Dict[str, float] = {}
        # Cache last working provider
        self._last_good_provider: Optional[str] = None

    async def init(self) -> None:
        # Pollinations — PRIMARY, free, fast
        pollinations = PollinationsProvider(timeout=PROVIDER_TIMEOUTS.get("text", 30.0))
        await pollinations.init()
        self.providers["pollinations"] = pollinations
        logger.info("Provider: pollinations (GPT-4o-mini, FREE, PRIMARY)")

        # Chutes — second, free, has vision
        chutes = ChutesProvider(timeout=PROVIDER_TIMEOUTS.get("text", 30.0))
        await chutes.init()
        self.providers["chutes"] = chutes
        self._vision_providers.append("chutes")
        logger.info("Provider: chutes (DeepSeek V3 + VL2, FREE)")

        # OpenAI-compatible providers with API keys
        for name, base_url, model, api_key in PROVIDER_CONFIGS:
            if not api_key:
                continue
            provider = OpenAICompatProvider(
                name=name, api_key=api_key, base_url=base_url,
                model=model, timeout=PROVIDER_TIMEOUTS.get("text", 30.0),
            )
            if provider.is_available():
                try:
                    await provider.init()
                    self.providers[name] = provider
                    logger.info(f"Provider: {name} ({model})")
                except Exception as exc:
                    logger.error(f"Failed to init {name}: {exc}")

        # Build chain
        self._chain = [p for p in PROVIDER_CHAIN if p in self.providers]
        if not self._chain:
            self._chain = ["pollinations", "chutes"]
        logger.info(f"AI chain: {' -> '.join(self._chain)}")

    async def close(self) -> None:
        for p in self.providers.values():
            try:
                await p.close()
            except Exception:
                pass

    def _is_provider_healthy(self, name: str) -> bool:
        """Check if provider should be tried (circuit breaker)."""
        fail_count = self._fail_counts.get(name, 0)
        last_fail = self._last_fail.get(name, 0)
        # If failed more than 5 times in a row and less than 5 min since last fail — skip
        if fail_count >= 5 and time.time() - last_fail < 300:
            return False
        return True

    def _mark_success(self, name: str) -> None:
        """Reset failure count on success."""
        self._fail_counts[name] = 0
        self._last_good_provider = name

    def _mark_failure(self, name: str) -> None:
        """Increment failure count."""
        self._fail_counts[name] = self._fail_counts.get(name, 0) + 1
        self._last_fail[name] = time.time()

    def _get_ordered_chain(self) -> List[str]:
        """Get provider chain ordered by last success."""
        chain = [p for p in self._chain if p in self.providers and self._is_provider_healthy(p)]
        # If we have a last good provider, try it first
        if self._last_good_provider and self._last_good_provider in chain:
            chain.remove(self._last_good_provider)
            chain.insert(0, self._last_good_provider)
        # If chain is empty (all circuit-broken), reset and try all
        if not chain:
            logger.warning("All providers circuit-broken! Resetting...")
            for name in self._chain:
                self._fail_counts[name] = 0
            chain = [p for p in self._chain if p in self.providers]
        return chain

    async def chat(self, prompt: str, system_prompt: str = "",
                   messages: Optional[List[Dict]] = None, **kwargs) -> AIResponse:
        """Route text chat with AGGRESSIVE retries. NEVER gives up."""
        image_base64 = kwargs.pop("image_base64", None)

        # If image provided, try vision providers first
        if image_base64:
            for vp_name in self._vision_providers:
                provider = self.providers.get(vp_name)
                if not provider:
                    continue
                for attempt in range(2):
                    try:
                        result = await provider.generate(
                            prompt, system_prompt=system_prompt,
                            messages=messages, image_base64=image_base64, **kwargs,
                        )
                        if result and result.text:
                            self._mark_success(vp_name)
                            return result
                    except Exception as e:
                        logger.warning(f"Vision provider {vp_name} attempt {attempt+1} failed: {e}")
                        if attempt == 0:
                            await asyncio.sleep(1)

        # Regular text chain — try ALL providers, multiple rounds
        chain = self._get_ordered_chain()
        last_error = None

        for round_num in range(3):  # 3 full rounds through the chain
            if round_num > 0:
                logger.info(f"Retry round {round_num + 1}...")
                await asyncio.sleep(2 * round_num)  # Progressive backoff

            for provider_name in chain:
                if not self._is_provider_healthy(provider_name):
                    continue
                provider = self.providers.get(provider_name)
                if not provider:
                    continue

                try:
                    result = await provider.generate(
                        prompt, system_prompt=system_prompt,
                        messages=messages, **kwargs,
                    )
                    if result and result.text:
                        self._mark_success(provider_name)
                        return result
                    logger.warning(f"Provider {provider_name} returned empty, trying next")
                except ProviderError as e:
                    last_error = e
                    self._mark_failure(provider_name)
                    logger.warning(f"Provider {provider_name} failed: {e}")
                    if not e.retryable:
                        continue
                except Exception as e:
                    last_error = e
                    self._mark_failure(provider_name)
                    logger.error(f"Error from {provider_name}: {e}")

                # Brief pause between providers
                await asyncio.sleep(0.5)

        # ALL providers failed — raise error, let handler use fallback
        raise AllProvidersExhaustedError(str(last_error))

    async def chat_with_image(self, prompt: str, image_base64: str,
                              system_prompt: str = "", **kwargs) -> AIResponse:
        """Chat with image understanding."""
        return await self.chat(prompt=prompt, system_prompt=system_prompt,
                               image_base64=image_base64, **kwargs)

    async def transcribe_voice(self, ogg_bytes: bytes) -> Optional[str]:
        """Transcribe voice message."""
        return await transcribe_voice_ogg(ogg_bytes)

    def get_fallback_response(self) -> str:
        """Get a fallback response when ALL AI fails. Bot ALWAYS responds!"""
        import random
        return random.choice(FALLBACK_RESPONSES)
