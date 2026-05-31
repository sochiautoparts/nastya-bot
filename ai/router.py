"""AI Router — BULLETPROOF routing with multi-phase fallback + caching.

Architecture (v2.2 — Cloudflare-first + GH_MODELS_TOKEN):
  - 9 providers with proper fallback chain (NO Grok)
  - CLOUDFLARE FIRST (free, fast, reliable, has vision)
  - GitHub Models second (free, uses GH_MODELS_TOKEN, GPT-4o-mini)
  - Pollinations + Chutes as always-free fallbacks (moved up!)
  - Other API-key providers as additional fallbacks
  - NEVER raises exceptions to caller — ALWAYS returns AIResponse
  - 30s timeouts with 10s connect timeout for Cloudflare
  - Circuit breaker: skip providers that failed recently
  - Cache last working provider for faster retry
  - AI Response caching (from ai-mega-bot)
  - Fallback responses as LAST resort — bot ALWAYS responds
  - NO "голова разболелась" error messages EVER
"""
import logging
import asyncio
import hashlib
import json
import time
import random
from typing import Any, Dict, List, Optional

from ai.providers.base import AIResponse, ProviderError
from ai.providers import ALL_PROVIDERS
from ai.voice import transcribe_voice_ogg
from bot.config import (
    OPENROUTER_API_KEY, CEREBRAS_API_KEY,
    SAMBANOVA_API_KEY, MISTRAL_API_KEY, GEMINI_API_KEY,
    CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID,
    GH_MODELS_TOKEN, CACHE_TTL_TEXT, CACHE_MAX_MEMORY,
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

# Provider chain v2.2: Cloudflare FIRST (free + reliable + vision),
# then GitHub Models (free + GPT-4o-mini), then Pollinations (always free),
# then Chutes (always free), then other key-based providers — NO Grok!
PROVIDER_CHAIN = [
    "cloudflare", "github_models", "pollinations", "chutes",
    "sambanova", "cerebras", "mistral", "openrouter", "gemini",
]

# Map env vars to provider configs — NO Grok!
PROVIDER_KEYS = {
    "cloudflare": CLOUDFLARE_API_TOKEN,
    "github_models": GH_MODELS_TOKEN,
    "cerebras": CEREBRAS_API_KEY,
    "openrouter": OPENROUTER_API_KEY,
    "sambanova": SAMBANOVA_API_KEY,
    "mistral": MISTRAL_API_KEY,
    "gemini": GEMINI_API_KEY,
}


class AICache:
    """Simple in-memory LRU cache for AI responses (from ai-mega-bot)."""

    def __init__(self, max_size: int = CACHE_MAX_MEMORY, ttl: int = CACHE_TTL_TEXT):
        self._cache: Dict[str, Dict] = {}
        self._max_size = max_size
        self._ttl = ttl

    def _make_key(self, prompt: str, system_prompt: str = "") -> str:
        data = f"{system_prompt}:{prompt}"
        return hashlib.sha256(data.encode()).hexdigest()[:32]

    def get(self, prompt: str, system_prompt: str = "") -> Optional[str]:
        key = self._make_key(prompt, system_prompt)
        entry = self._cache.get(key)
        if entry and time.time() - entry["ts"] < self._ttl:
            return entry["text"]
        if key in self._cache:
            del self._cache[key]
        return None

    def put(self, prompt: str, system_prompt: str, text: str) -> None:
        key = self._make_key(prompt, system_prompt)
        self._cache[key] = {"text": text, "ts": time.time()}
        # Evict oldest if over limit
        while len(self._cache) > self._max_size:
            oldest_key = next(iter(self._cache))
            del self._cache[oldest_key]

    def clear(self) -> None:
        self._cache.clear()


class AIRouter:
    """Central AI request router — NEVER crashes, ALWAYS responds.

    Based on ai-mega-bot's proven AIRouter pattern with Nastya-specific
    enhancements: circuit breaker, provider caching, response caching,
    and in-character fallback responses.

    v2.1: Cloudflare-first chain + GitHub Models.
    """

    def __init__(self, db=None):
        self.providers: Dict[str, Any] = {}
        self._chain: List[str] = []
        self._vision_providers: List[str] = []
        # Circuit breaker
        self._fail_counts: Dict[str, int] = {}
        self._last_fail: Dict[str, float] = {}
        # Cache last working provider
        self._last_good_provider: Optional[str] = None
        # Response cache (in-memory LRU)
        self._cache = AICache()
        # DB for persistent cache
        self._db = db
        # Stats
        self._total_requests: int = 0
        self._total_fallbacks: int = 0
        self._cache_hits: int = 0

    async def init(self) -> None:
        """Initialize all available providers from ALL_PROVIDERS registry."""
        for name, provider_cls in ALL_PROVIDERS.items():
            try:
                api_key = PROVIDER_KEYS.get(name, "")

                if name in ("pollinations", "chutes"):
                    provider = provider_cls(timeout=30.0)
                elif name == "cloudflare":
                    provider = provider_cls(
                        api_key=api_key, timeout=30.0,
                        account_id=CLOUDFLARE_ACCOUNT_ID,
                    )
                elif api_key:
                    provider = provider_cls(api_key=api_key, timeout=30.0)
                else:
                    logger.info(f"Provider: {name} — SKIPPED (no API key)")
                    continue

                if provider.is_available():
                    await provider.init()
                    self.providers[name] = provider
                    vision = " (vision)" if getattr(provider, 'supports_vision', False) else ""
                    logger.info(f"Provider: {name} ✓{vision}")
                else:
                    logger.warning(f"Provider: {name} — not available (no key)")

            except Exception as exc:
                logger.error(f"Failed to init provider {name}: {exc}")

        # Build chain with only available providers
        self._chain = [p for p in PROVIDER_CHAIN if p in self.providers]
        if not self._chain:
            self._chain = list(self.providers.keys())

        # Vision providers
        for name, provider in self.providers.items():
            if getattr(provider, 'supports_vision', False):
                self._vision_providers.append(name)

        logger.info(f"AI chain ({len(self._chain)}): {' → '.join(self._chain)}")
        if self._vision_providers:
            logger.info(f"Vision providers: {', '.join(self._vision_providers)}")

    async def close(self) -> None:
        """Shutdown all providers."""
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
        """Route text chat. NEVER raises exceptions. ALWAYS returns a response.

        Multi-phase routing with caching:
        1. Check memory cache (fast)
        2. Check DB cache (if available)
        3. Try providers with circuit breaker
        4. Retry top providers
        5. Try Pollinations with simpler prompt
        6. Fallback response (in-character)
        """
        image_base64 = kwargs.pop("image_base64", None)
        self._total_requests += 1

        # ── PHASE 0: Check memory cache (only for no-history requests) ──
        if not messages and not image_base64:
            cached = self._cache.get(prompt, system_prompt)
            if cached:
                self._cache_hits += 1
                logger.debug(f"Cache hit for: {prompt[:50]}...")
                return AIResponse(
                    text=cached,
                    provider="cache",
                    model="none",
                    tokens_used=0,
                    metadata={"from_cache": True},
                )

        # ── PHASE 0.5: Check DB cache (if available) ──
        if not messages and not image_base64 and self._db:
            try:
                cache_key = hashlib.sha256(f"{system_prompt}:{prompt}".encode()).hexdigest()[:32]
                cached_db = await self._db.cache_get(cache_key, max_age=CACHE_TTL_TEXT)
                if cached_db:
                    self._cache_hits += 1
                    text = cached_db.get("text", "")
                    if text:
                        # Promote to memory cache
                        self._cache.put(prompt, system_prompt, text)
                        return AIResponse(
                            text=text,
                            provider="db_cache",
                            model="none",
                            tokens_used=0,
                            metadata={"from_cache": True},
                        )
            except Exception:
                pass

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

        # ── PHASE 1: Try configured providers ──
        chain = self._get_ordered_chain()

        for provider_name in chain:
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

                    # Cache the response (only for no-history requests)
                    if not messages and not image_base64:
                        self._cache.put(prompt, system_prompt, result.text)
                        if self._db:
                            try:
                                cache_key = hashlib.sha256(f"{system_prompt}:{prompt}".encode()).hexdigest()[:32]
                                await self._db.cache_put(cache_key, "text", {"text": result.text})
                            except Exception:
                                pass

                    return result
                logger.warning(f"Provider {provider_name} returned empty")
            except ProviderError as e:
                self._mark_failure(provider_name)
                logger.warning(f"Provider {provider_name} failed: {e}")
                if not e.retryable:
                    continue
            except Exception as e:
                self._mark_failure(provider_name)
                logger.warning(f"Error from {provider_name}: {e}")

        # ── PHASE 2: Retry top providers ──
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

        # ── PHASE 3: Try Pollinations with simpler prompt ──
        if "pollinations" in self.providers:
            try:
                provider = self.providers["pollinations"]
                result = await provider.generate(
                    prompt, system_prompt=system_prompt,
                    messages=None,  # Simplified
                )
                if result and result.text:
                    self._mark_success("pollinations")
                    return result
            except Exception as e:
                logger.error(f"Even Pollinations failed: {e}")

        # ── PHASE 4: Try Chutes as last resort ──
        if "chutes" in self.providers:
            try:
                provider = self.providers["chutes"]
                result = await provider.generate(
                    prompt, system_prompt=system_prompt,
                    messages=None,
                )
                if result and result.text:
                    self._mark_success("chutes")
                    return result
            except Exception as e:
                logger.error(f"Even Chutes failed: {e}")

        # ── PHASE 5: FALLBACK — bot ALWAYS responds ──
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

    def get_status(self) -> Dict[str, Any]:
        """Get status of all providers for admin commands."""
        status = {}
        for name in self._chain:
            provider = self.providers.get(name)
            status[name] = {
                "available": provider is not None,
                "healthy": self._is_provider_healthy(name),
                "fail_count": self._fail_counts.get(name, 0),
                "vision": getattr(provider, 'supports_vision', False) if provider else False,
            }
        status["_stats"] = {
            "total_requests": self._total_requests,
            "total_fallbacks": self._total_fallbacks,
            "cache_hits": self._cache_hits,
            "last_good_provider": self._last_good_provider,
        }
        return status
