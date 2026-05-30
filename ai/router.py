"""AI Router — BULLETPROOF routing with multi-phase fallback. Bot ALWAYS responds.

ARCHITECTURE v8.0 (ported from ai-mega-bot):
  - 9+ providers with proper fallback chain
  - API-key providers FIRST (fast, reliable)
  - FREE providers as fallbacks (Pollinations, Chutes, Blackbox, HuggingFace)
  - NEVER raises exceptions to caller — ALWAYS returns AIResponse
  - 30s timeouts (not 12-15s like before)
  - Circuit breaker: skip providers that failed recently
  - Cache last working provider for faster retry
  - Fallback responses as LAST resort — bot ALWAYS responds
"""
import logging
import asyncio
import time
import random
from typing import Any, Dict, List, Optional

from ai.providers.base import AIResponse, ProviderError
from ai.providers import ALL_PROVIDERS
from ai.voice import transcribe_voice_ogg
from bot.config import (
    OPENROUTER_API_KEY, GROQ_API_KEY, CEREBRAS_API_KEY,
    SAMBANOVA_API_KEY, MISTRAL_API_KEY,
)

logger = logging.getLogger(__name__)

# Fallback responses — used when ALL AI providers fail
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

# Provider chain: API-key providers FIRST (reliable, fast),
# then free providers as fallbacks
PROVIDER_CHAIN = [
    "groq", "cerebras", "sambanova", "openrouter", "mistral",
    "chutes", "pollinations", "blackbox", "huggingface",
]

# Map env vars to provider configs
PROVIDER_KEYS = {
    "groq": GROQ_API_KEY,
    "cerebras": CEREBRAS_API_KEY,
    "openrouter": OPENROUTER_API_KEY,
    "sambanova": SAMBANOVA_API_KEY,
    "mistral": MISTRAL_API_KEY,
}


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
        """Initialize all available providers from ALL_PROVIDERS registry."""
        for name, provider_cls in ALL_PROVIDERS.items():
            try:
                # Get API key for this provider (if needed)
                api_key = PROVIDER_KEYS.get(name, "")

                # Provider-specific initialization
                if name in ("pollinations", "chutes", "blackbox", "huggingface"):
                    # Free providers — no key needed, longer timeout
                    provider = provider_cls(timeout=30.0)
                elif api_key:
                    # API-key providers
                    provider = provider_cls(api_key=api_key, timeout=30.0)
                else:
                    logger.info(f"Provider: {name} — SKIPPED (no API key)")
                    continue

                if provider.is_available():
                    await provider.init()
                    self.providers[name] = provider
                    logger.info(f"Provider: {name} ✓")
                else:
                    logger.warning(f"Provider: {name} — not available")

            except Exception as exc:
                logger.error(f"Failed to init provider {name}: {exc}")

        # Build chain with only available providers
        self._chain = [p for p in PROVIDER_CHAIN if p in self.providers]
        if not self._chain:
            self._chain = list(self.providers.keys())

        # Vision providers (only Chutes supports multimodal images)
        if "chutes" in self.providers:
            self._vision_providers.append("chutes")

        logger.info(f"AI chain ({len(self._chain)}): {' → '.join(self._chain)}")

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
        """Get provider chain, prioritizing last working provider."""
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

        # ── PHASE 2: Retry top providers one more time ──
        logger.info("All providers failed on first try, retrying top 3...")
        await asyncio.sleep(1)

        for provider_name in chain[:3]:
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

        # ── PHASE 3: FALLBACK — bot ALWAYS responds ──
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
