"""AI Router — BULLETPROOF routing with multi-phase fallback + caching.

Architecture (v4.1 — DeepSeek API as PRIORITY #1):
  - DeepSeek API FIRST (DeepSeek V3 — best Russian quality, direct API)
  - Chutes SECOND (DeepSeek V3 — free, always available, excellent Russian)
  - GitHub Models THIRD (DeepSeek V3/R1 via GitHub, reliable, has vision)
  - Cloudflare FOURTH (DeepSeek R1 distill, Llama models, has vision)
  - Pollinations fifth (free, always available, BUT leaks ads — cleaned)
  - Other API-key providers as additional fallbacks
  - NEVER raises exceptions to caller — ALWAYS returns AIResponse
  - 30s timeouts with proper connect timeouts
  - Circuit breaker: skip providers that failed recently
  - Cache last working provider for faster retry
  - AI Response caching (from ai-mega-bot)
  - Fallback responses as LAST resort — bot ALWAYS responds
  - NO "голова разболелась" error messages EVER
  - Aggressive response cleaning: strips ads, markdown, artifacts

CRITICAL v4.1: DeepSeek API (api.deepseek.com) as PRIMARY — best quality
  for Russian conversation, excellent context memory, natural style.
  Chutes provides free DeepSeek V3 access as backup.
  GitHub Models also supports DeepSeek V3.
  Aggressive response cleaning strips all AI artifacts/ads.
  image_base64 is NOT popped from kwargs — providers read it but
  don't consume it, so fallback providers can still access it.
"""
import logging
import asyncio
import hashlib
import json
import re
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
    GH_MODELS_TOKEN, GH_TOKEN_SECRET, GITHUB_TOKEN, GH_PAT_TOKEN,
    DEEPSEEK_API_KEY,
    CACHE_TTL_TEXT, CACHE_MAX_MEMORY,
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

# Provider chain v4.1: DeepSeek API FIRST (best Russian, direct),
# then free DeepSeek via Chutes, then GitHub Models, Cloudflare, etc.
PROVIDER_CHAIN = [
    # DeepSeek API — PRIORITY #1, best Russian quality
    "deepseek",
    # DeepSeek V3 via Chutes — FREE, excellent Russian, always available
    "chutes",
    # GitHub Models — DeepSeek V3 / R1 via GitHub, reliable
    "github_models",
    # Cloudflare — DeepSeek R1 distill, Llama models, free with creds
    "cloudflare",
    # Pollinations — always free but leaks ads (cleaned)
    "pollinations",
    # Other API-key providers as additional fallbacks
    "sambanova", "cerebras", "mistral", "openrouter", "gemini",
]

# Map env vars to provider configs
# GitHub Models: try GH_MODELS_TOKEN first, then GH_PAT_TOKEN (PAT with 'models' permission),
# then GH_TOKEN_SECRET, then auto-generated GITHUB_TOKEN
PROVIDER_KEYS = {
    "deepseek": DEEPSEEK_API_KEY,
    "cloudflare": CLOUDFLARE_API_TOKEN,
    "github_models": GH_MODELS_TOKEN or GH_PAT_TOKEN or GH_TOKEN_SECRET or GITHUB_TOKEN,
    "cerebras": CEREBRAS_API_KEY,
    "openrouter": OPENROUTER_API_KEY,
    "sambanova": SAMBANOVA_API_KEY,
    "mistral": MISTRAL_API_KEY,
    "gemini": GEMINI_API_KEY,
}


class AICache:
    """Simple in-memory LRU cache for AI responses."""

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
        while len(self._cache) > self._max_size:
            oldest_key = next(iter(self._cache))
            del self._cache[oldest_key]

    def clear(self) -> None:
        self._cache.clear()


class AIRouter:
    """Central AI request router — NEVER crashes, ALWAYS responds.

    v2.3: Pollinations-first for maximum reliability.
    Critical fix: image_base64 is no longer popped from kwargs.
    """

    def __init__(self, db=None):
        self.providers: Dict[str, Any] = {}
        self._chain: List[str] = []
        self._vision_providers: List[str] = []
        self._fail_counts: Dict[str, int] = {}
        self._last_fail: Dict[str, float] = {}
        self._last_good_provider: Optional[str] = None
        self._cache = AICache()
        self._db = db
        self._total_requests: int = 0
        self._total_fallbacks: int = 0
        self._cache_hits: int = 0

    async def init(self) -> None:
        """Initialize all available providers."""
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
                    logger.warning(f"Provider: {name} — not available")

            except Exception as exc:
                logger.error(f"Failed to init provider {name}: {exc}")

        self._chain = [p for p in PROVIDER_CHAIN if p in self.providers]
        if not self._chain:
            self._chain = list(self.providers.keys())

        for name, provider in self.providers.items():
            if getattr(provider, 'supports_vision', False):
                self._vision_providers.append(name)

        logger.info(f"AI chain ({len(self._chain)}): {' → '.join(self._chain)}")
        if self._vision_providers:
            logger.info(f"Vision providers: {', '.join(self._vision_providers)}")

    async def close(self) -> None:
        for p in self.providers.values():
            try:
                await p.close()
            except Exception:
                pass

    def _is_provider_healthy(self, name: str) -> bool:
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
        image_base64 = kwargs.get("image_base64")
        self._total_requests += 1

        # ── PHASE 0: Check memory cache (only for no-history requests) ──
        if not messages and not image_base64:
            cached = self._cache.get(prompt, system_prompt)
            if cached:
                self._cache_hits += 1
                return AIResponse(
                    text=cached, provider="cache", model="none",
                    tokens_used=0, metadata={"from_cache": True},
                )

        # ── PHASE 0.5: Check DB cache ──
        if not messages and not image_base64 and self._db:
            try:
                cache_key = hashlib.sha256(f"{system_prompt}:{prompt}".encode()).hexdigest()[:32]
                cached_db = await self._db.cache_get(cache_key, max_age=CACHE_TTL_TEXT)
                if cached_db:
                    text = cached_db.get("text", "")
                    if text:
                        self._cache_hits += 1
                        self._cache.put(prompt, system_prompt, text)
                        return AIResponse(
                            text=text, provider="db_cache", model="none",
                            tokens_used=0, metadata={"from_cache": True},
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
                    # NOTE: image_base64 is passed as kwarg, NOT consumed
                    result = await provider.generate(
                        prompt, system_prompt=system_prompt,
                        messages=messages, image_base64=image_base64, **kwargs,
                    )
                    if result and result.text:
                        self._mark_success(vp_name)
                        return result
                except ProviderError as e:
                    self._mark_failure(vp_name)
                    logger.warning(f"Vision provider {vp_name} failed: {e}")
                    if not e.retryable:
                        continue
                except Exception as e:
                    self._mark_failure(vp_name)
                    logger.warning(f"Vision provider {vp_name} error: {e}")

        # ── PHASE 1: Try configured providers ──
        chain = self._get_ordered_chain()

        for provider_name in chain:
            provider = self.providers.get(provider_name)
            if not provider:
                continue

            try:
                # NOTE: image_base64 is passed as kwarg, NOT consumed (popped)
                result = await provider.generate(
                    prompt, system_prompt=system_prompt,
                    messages=messages, **kwargs,
                )
                if result and result.text:
                    self._mark_success(provider_name)

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

        # ── PHASE 3: Try Pollinations with simplified prompt ──
        if "pollinations" in self.providers:
            try:
                provider = self.providers["pollinations"]
                result = await provider.generate(
                    prompt, system_prompt=system_prompt,
                    messages=None,
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

    @staticmethod
    def clean_ai_response(text: str) -> str:
        """Aggressively strip AI artifacts, ads, and garbage from responses.

        Pollinations leaks ads like 'Support Pollinations.AI', '🌸 Ad 🌸', etc.
        Other providers may add markdown, self-references, or other junk.
        This is the LAST line of defense before text reaches the user.
        """
        if not text:
            return ""

        # ── Strip known ad/artifact patterns ──
        # Pollinations ads
        ad_patterns = [
            r'Support Pollinations\.AI.*',
            r'🌸\s*Ad\s*🌸.*',
            r'Powered by Pollinations\.AI.*',
            r'Pollinations\.AI free text APIs.*',
            r'Support our mission.*',
            r'keep AI accessible for everyone.*',
            r'---\s*\n\s*\*\*Support Pollinations',
            r'---\s*\n\s*🌸\s*\*\*Ad\*\*',
            r'Visit Pollinations\.AI.*',
            r'pollinations\.ai.*',
            r'🌸.*Ad.*🌸.*',
            r'free.*API.*access.*',
            r'open source.*AI.*',
            r'Check out Pollinations.*',
            r'Learn more about Pollinations.*',
            r'Pollinations\.AI —.*',
            r'powered by.*Pollinations.*',
            r'Support free AI.*',
            r'🌻.*',
            r'🌱.*Support.*',
        ]
        for pattern in ad_patterns:
            text = re.sub(pattern, '', text, flags=re.IGNORECASE | re.DOTALL)

        # Strip DeepSeek R1 "think" tags
        text = re.sub(r'<think\b[^>]*>.*?</think\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<thinking\b[^>]*>.*?</thinking\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)

        # Strip "ad" sections with various emoji markers
        text = re.sub(r'[\U0001f33f\U0001f331\U0001f31f].{0,5}(?:Ad|Support|Visit|Check).{0,100}', '', text, flags=re.IGNORECASE)

        # Remove separator lines with ads after them
        text = re.sub(r'\n---\s*$', '', text)
        text = re.sub(r'^---\s*$', '', text, flags=re.MULTILINE)

        # Strip "As an AI" type disclaimers
        text = re.sub(r'(?:As an AI|Как AI|Как искусственный интеллект|I am an AI|Я искусственный интеллект)[^.]*\.', '', text, flags=re.IGNORECASE)

        # Strip model self-references
        for prefix in [
            "Настя:", "Nastya:", "НАСТЯ:", "Assistant:", "Настя отвечает:",
            "Ответ Насти:", "Response:", "Answer:", "Настя говорит:",
        ]:
            if text.startswith(prefix):
                text = text[len(prefix):].strip()

        # Remove surrounding quotes
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        if text.startswith("'") and text.endswith("'"):
            text = text[1:-1]

        # Strip leading/trailing asterisks (markdown bold)
        text = text.strip("*").strip()

        # Remove markdown formatting
        text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
        text = re.sub(r'\*([^*]+)\*', r'\1', text)
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\d+\.\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^[-•]\s+', '', text, flags=re.MULTILINE)

        # Clean up multiple newlines
        text = re.sub(r'\n{3,}', '\n\n', text)

        # Remove trailing/leading whitespace
        text = text.strip()

        return text

    def get_fallback_response(self) -> str:
        return random.choice(FALLBACK_RESPONSES)

    def get_status(self) -> Dict[str, Any]:
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
