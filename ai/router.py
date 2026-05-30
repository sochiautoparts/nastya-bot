"""AI Router — BULLETPROOF routing. Bot ALWAYS responds, NEVER shows errors.

ARCHITECTURE v7.0:
  - API-key providers FIRST (reliable, fast)
  - Multiple FREE providers as fallbacks (Pollinations, DuckDuckGo, Blackbox)
  - NEVER raises exceptions to caller — ALWAYS returns AIResponse
  - Short timeouts (10-15s) per provider — move on fast
  - Circuit breaker: skip providers that failed recently
  - Fallback responses as LAST resort — bot ALWAYS responds
"""
import logging
import asyncio
import time
import random
import httpx
from typing import Any, Dict, List, Optional

from ai.providers.base import AIResponse, ProviderError
from ai.providers.pollinations_provider import PollinationsProvider
from ai.providers.chutes_provider import ChutesProvider
from ai.providers.openai_compat_provider import OpenAICompatProvider
from ai.voice import transcribe_voice_ogg
from bot.config import (
    PROVIDER_CHAIN, PROVIDER_TIMEOUTS,
    OPENROUTER_API_KEY, GROQ_API_KEY, CEREBRAS_API_KEY,
    SAMBANOVA_API_KEY, MISTRAL_API_KEY,
)

logger = logging.getLogger(__name__)


# Fallback responses — used when ALL AI providers fail
# Bot ALWAYS responds, never leaves user hanging!
FALLBACK_RESPONSES = [
    "Ммм... Настя задумалась. Повтори? 🤔",
    "Ой, Настя отвлеклась... Что ты сказал? 😅",
    "Блин, Настя задумалась о вечном... Ещё раз? 💅",
    "Настя не расслышала... Говори ещё! 😏",
    "Ой, Настя на секунду улетела в мечты! Повтори? ✨",
    "А? Настя думала о шопинге... Что хотела сказать? 👜",
    "Эээ... Настя прослушала. Ещё разок? 😜",
    "Ой, мысли улетели! Повтори для Насти? 💭",
    "Хм? Настя где-то там витала... Повтори? 🌸",
    "А? Настя считала звёздочки... Что? ⭐",
]

# (name, base_url, model, api_key)
PROVIDER_CONFIGS = [
    ("groq", "https://api.groq.com", "llama-3.3-70b-versatile", GROQ_API_KEY),
    ("cerebras", "https://api.cerebras.ai", "llama-4-scout-17b-16e-instruct", CEREBRAS_API_KEY),
    ("openrouter", "https://openrouter.ai", "google/gemma-4-31b-it:free", OPENROUTER_API_KEY),
    ("sambanova", "https://api.sambanova.ai", "Meta-Llama-3.3-70B-Instruct", SAMBANOVA_API_KEY),
    ("mistral", "https://api.mistral.ai", "mistral-small-latest", MISTRAL_API_KEY),
]


async def _try_duckduckgo(prompt: str, system_prompt: str = "") -> Optional[AIResponse]:
    """DuckDuckGo AI Chat — FREE, no API key, often works.
    Uses the DuckDuckGo AI Chat API which provides free access to various models."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            # DuckDuckGo AI chat endpoint
            payload = {
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": system_prompt or "Ты Настя — капризная девушка. Отвечай 1-3 предложения."},
                    {"role": "user", "content": prompt},
                ],
            }
            response = await client.post(
                "https://ai-chat.duckduckgo.com/chat",
                json=payload,
                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
            )
            if response.status_code == 200:
                # DuckDuckGo returns streaming NDJSON — get the last line
                text = response.text.strip()
                if text:
                    # Try to parse as NDJSON
                    lines = [l for l in text.split('\n') if l.strip()]
                    if lines:
                        import json
                        last = lines[-1]
                        try:
                            data = json.loads(last)
                            if data.get("message"):
                                return AIResponse(
                                    text=data["message"],
                                    provider="duckduckgo",
                                    model="gpt-4o-mini",
                                    tokens_used=0,
                                )
                        except Exception:
                            pass
    except Exception as e:
        logger.debug(f"DuckDuckGo AI failed: {e}")
    return None


async def _try_blackbox(prompt: str, system_prompt: str = "") -> Optional[AIResponse]:
    """Blackbox AI — FREE, no API key. Fast and reliable."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            payload = {
                "messages": messages,
                "agentMode": {},
                "trendingAgentMode": {},
                "isMicMode": False,
                "maxTokens": 256,
                "isChromeExt": False,
                "githubToken": None,
            }
            response = await client.post(
                "https://www.blackbox.ai/api/chat",
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                },
            )
            if response.status_code == 200:
                text = response.text.strip()
                # Blackbox sometimes returns metadata at the end, strip it
                if "$~~~$" in text:
                    text = text.split("$~~~$")[0].strip()
                if text and len(text) > 2:
                    return AIResponse(
                        text=text,
                        provider="blackbox",
                        model="blackbox-free",
                        tokens_used=0,
                    )
    except Exception as e:
        logger.debug(f"Blackbox AI failed: {e}")
    return None


async def _try_huggingface_free(prompt: str, system_prompt: str = "") -> Optional[AIResponse]:
    """HuggingFace free inference API — no key needed for many models."""
    try:
        full_prompt = ""
        if system_prompt:
            full_prompt = f"{system_prompt}\n\n"
        full_prompt += prompt

        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            response = await client.post(
                "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.3",
                json={"inputs": full_prompt, "parameters": {"max_new_tokens": 200, "temperature": 0.8}},
                headers={"Content-Type": "application/json"},
            )
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list) and data:
                    text = data[0].get("generated_text", "")
                    # Extract only the new part after the prompt
                    if full_prompt in text:
                        text = text[len(full_prompt):].strip()
                    if text and len(text) > 2:
                        return AIResponse(
                            text=text,
                            provider="huggingface-free",
                            model="mistral-7b-instruct",
                            tokens_used=0,
                        )
    except Exception as e:
        logger.debug(f"HuggingFace free failed: {e}")
    return None


class AIRouter:
    def __init__(self):
        self.providers: Dict[str, Any] = {}
        self._chain: List[str] = []
        self._vision_providers: List[str] = []
        # Circuit breaker
        self._fail_counts: Dict[str, int] = {}
        self._last_fail: Dict[str, float] = {}
        # Cache last working provider
        self._last_good_provider: Optional[str] = None
        # Stats
        self._total_requests: int = 0
        self._total_fallbacks: int = 0

    async def init(self) -> None:
        # API-key providers FIRST — they're the most reliable
        for name, base_url, model, api_key in PROVIDER_CONFIGS:
            if not api_key:
                logger.info(f"Provider: {name} — SKIPPED (no API key)")
                continue
            provider = OpenAICompatProvider(
                name=name, api_key=api_key, base_url=base_url,
                model=model, timeout=PROVIDER_TIMEOUTS.get("text", 15.0),
            )
            if provider.is_available():
                try:
                    await provider.init()
                    self.providers[name] = provider
                    logger.info(f"Provider: {name} ({model}) ✓")
                except Exception as exc:
                    logger.error(f"Failed to init {name}: {exc}")

        # Pollinations — free but unreliable
        pollinations = PollinationsProvider(timeout=PROVIDER_TIMEOUTS.get("pollinations", 12.0))
        await pollinations.init()
        self.providers["pollinations"] = pollinations
        logger.info("Provider: pollinations (FREE)")

        # Chutes — free, vision support
        chutes = ChutesProvider(timeout=PROVIDER_TIMEOUTS.get("text", 15.0))
        await chutes.init()
        self.providers["chutes"] = chutes
        self._vision_providers.append("chutes")
        logger.info("Provider: chutes (FREE)")

        # Build chain
        self._chain = [p for p in PROVIDER_CHAIN if p in self.providers]
        if not self._chain:
            self._chain = list(self.providers.keys())
        logger.info(f"AI chain ({len(self._chain)}): {' → '.join(self._chain)}")
        logger.info(f"Also available as extra fallbacks: duckduckgo, blackbox, huggingface-free")

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
        if fail_count >= 3 and time.time() - last_fail < 120:
            return False
        return True

    def _mark_success(self, name: str) -> None:
        self._fail_counts[name] = 0
        self._last_good_provider = name

    def _mark_failure(self, name: str) -> None:
        self._fail_counts[name] = self._fail_counts.get(name, 0) + 1
        self._last_fail[name] = time.time()

    def _get_ordered_chain(self) -> List[str]:
        chain = [p for p in self._chain if p in self.providers and self._is_provider_healthy(p)]
        if self._last_good_provider and self._last_good_provider in chain:
            chain.remove(self._last_good_provider)
            chain.insert(0, self._last_good_provider)
        if not chain:
            logger.warning("All providers circuit-broken! Resetting...")
            for name in self._chain:
                self._fail_counts[name] = 0
            chain = [p for p in self._chain if p in self.providers]
        return chain

    async def chat(self, prompt: str, system_prompt: str = "",
                   messages: Optional[List[Dict]] = None, **kwargs) -> AIResponse:
        """Route text chat. NEVER raises exceptions. ALWAYS returns a response."""
        image_base64 = kwargs.pop("image_base64", None)
        self._total_requests += 1

        # If image, try vision providers first
        if image_base64:
            for vp_name in self._vision_providers:
                provider = self.providers.get(vp_name)
                if not provider or not self._is_provider_healthy(vp_name):
                    continue
                try:
                    result = await provider.generate(
                        prompt, system_prompt=system_prompt,
                        messages=messages, image_base64=image_base64, **kwargs,
                    )
                    if result and result.text:
                        self._mark_success(vp_name)
                        return result
                except Exception as e:
                    logger.warning(f"Vision provider {vp_name} failed: {e}")
                    self._mark_failure(vp_name)

        # ── PHASE 1: Try configured providers (API-key + free) ──
        chain = self._get_ordered_chain()

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
                logger.warning(f"Provider {provider_name} returned empty")
            except ProviderError as e:
                self._mark_failure(provider_name)
                logger.warning(f"Provider {provider_name} failed: {e}")
            except Exception as e:
                self._mark_failure(provider_name)
                logger.warning(f"Error from {provider_name}: {e}")

        # ── PHASE 2: Try extra free providers (DuckDuckGo, Blackbox, HuggingFace) ──
        logger.info("Configured providers failed, trying extra free providers...")

        # Try DuckDuckGo
        try:
            result = await _try_duckduckgo(prompt, system_prompt)
            if result and result.text:
                logger.info("DuckDuckGo succeeded!")
                return result
        except Exception:
            pass

        # Try Blackbox
        try:
            result = await _try_blackbox(prompt, system_prompt)
            if result and result.text:
                logger.info("Blackbox succeeded!")
                return result
        except Exception:
            pass

        # Try HuggingFace free
        try:
            result = await _try_huggingface_free(prompt, system_prompt)
            if result and result.text:
                logger.info("HuggingFace free succeeded!")
                return result
        except Exception:
            pass

        # ── PHASE 3: Retry configured providers one more time ──
        logger.info("Extra providers failed, retrying configured providers...")
        await asyncio.sleep(1)

        for provider_name in chain[:3]:  # Only try top 3
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
            except Exception:
                pass

        # ── PHASE 4: FALLBACK — bot ALWAYS responds ──
        self._total_fallbacks += 1
        logger.error("ALL providers failed! Using fallback response.")

        return AIResponse(
            text=self.get_fallback_response(),
            provider="fallback",
            model="none",
            tokens_used=0,
        )

    async def chat_with_image(self, prompt: str, image_base64: str,
                              system_prompt: str = "", **kwargs) -> AIResponse:
        return await self.chat(prompt=prompt, system_prompt=system_prompt,
                               image_base64=image_base64, **kwargs)

    async def transcribe_voice(self, ogg_bytes: bytes) -> Optional[str]:
        return await transcribe_voice_ogg(ogg_bytes)

    def get_fallback_response(self) -> str:
        return random.choice(FALLBACK_RESPONSES)
