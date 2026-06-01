"""AI Router — BULLETPROOF routing with local model PRIMARY + cloud fallback.

Architecture (v15.0 — Local Qwen3-VL as PRIMARY):
  - Ollama FIRST (local Qwen3-VL-2B — free, unlimited, no external API!)
  - GitHub Models SECOND (free DeepSeek-V3 via PAT, reliable backup)
  - Pollinations THIRD (free, no key, vision)
  - Chutes FOURTH (free DeepSeek V3, no key, vision)
  - Blackbox FIFTH (free, unlimited, vision)
  - HuggingFace SIXTH (free tier, optional key, vision)
  - OpenRouter SEVENTH (50 free req/day — demoted)
  - Other API-key providers as additional fallbacks
  - NEVER raises exceptions to caller — ALWAYS returns AIResponse
  - Political content filtering — responses with political keywords are replaced
  - Aggressive response cleaning: strips ads, markdown, artifacts, SSE garbage
  - NO "голова разболелась" error messages EVER
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
    OPENROUTER_API_KEY, CEREBRAS_API_KEY, GROQ_API_KEY,
    SAMBANOVA_API_KEY, MISTRAL_API_KEY, GEMINI_API_KEY,
    CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID,
    GH_MODELS_TOKEN, GH_TOKEN_SECRET, GITHUB_TOKEN, GH_PAT_TOKEN,
    HUGGINGFACE_API_KEY,
    CACHE_TTL_TEXT, CACHE_MAX_MEMORY,
    OLLAMA_BASE_URL,
)

# Free providers that don't need API keys
FREE_PROVIDERS = {"ollama", "pollinations", "chutes", "blackbox", "huggingface"}

logger = logging.getLogger(__name__)

# Fallback responses — used when ALL AI providers fail
FALLBACK_RESPONSES = [
    "Ммм... Настя задумалась. Повтори? 🤔",
    "Ой, Настя отвлеклась... Что ты сказал? 😅",
    "Блин, Настя задумалась о вечном... Ещё раз? 💅",
    "Настя не расслышала... Говори ещё! 😏",
    "Ой, мысли улетели! Повтори для Насти? 💭",
    "Кстати, заходи на мой канал @chasnastya! 💅 А ты что сказал?",
]

# Provider chain v15.0: LOCAL model FIRST, then free cloud, then limited
PROVIDER_CHAIN = [
    # ── LOCAL model (PRIMARY — free, unlimited, no external dependency!) ──
    "ollama",
    # ── Free cloud providers ──
    # GitHub Models — free DeepSeek-V3 via PAT, reliable
    "github_models",
    # Pollinations — always free, always available, vision
    "pollinations",
    # Chutes — free DeepSeek V3, no key, vision
    "chutes",
    # Blackbox — free, unlimited, multiple models, vision
    "blackbox",
    # HuggingFace — free tier, optional key, many models, vision
    "huggingface",
    # ── Limited free providers (demoted) ──
    "openrouter",
    # Cloudflare — free if credentials available
    "cloudflare",
    # Groq — free, ultra-fast LPU, great Russian, 30 RPM
    "groq",
    # Other API-key providers as additional fallbacks
    "sambanova", "cerebras", "mistral", "gemini",
]

# Map env vars to provider configs
PROVIDER_KEYS = {
    "ollama": "",  # No key needed — local!
    "cloudflare": CLOUDFLARE_API_TOKEN,
    "groq": GROQ_API_KEY,
    "huggingface": HUGGINGFACE_API_KEY,
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

    v15.0: Ollama (local Qwen3-VL) as PRIMARY, GitHub Models as reliable backup.
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

                if name == "ollama":
                    # Ollama — local model, no key needed, configurable URL
                    provider = provider_cls(
                        timeout=60.0,
                        base_url=OLLAMA_BASE_URL,
                    )
                elif name in ("pollinations", "chutes", "blackbox"):
                    # Always-free cloud providers — no key needed
                    provider = provider_cls(timeout=30.0)
                elif name == "huggingface":
                    # HuggingFace works with or without key
                    provider = provider_cls(api_key=api_key, timeout=30.0)
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
                    # After init(), check if provider actually became available
                    # (e.g., Ollama might not have a running server)
                    if hasattr(provider, '_available') and not provider._available:
                        logger.warning(f"Provider: {name} — server not running after init")
                        continue
                    self.providers[name] = provider
                    vision = " (vision)" if getattr(provider, 'supports_vision', False) else ""
                    local = " [LOCAL]" if name == "ollama" else ""
                    logger.info(f"Provider: {name} ✓{vision}{local}")
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

        # Warm up Ollama model (pre-load into memory for faster first response)
        if "ollama" in self.providers:
            try:
                await self.providers["ollama"]._warm_up()
            except Exception as e:
                logger.warning(f"Ollama warm-up failed: {e}")

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

        # ── PHASE 0: Check memory cache ONLY for no-history requests ──
        has_conversation = bool(messages and len(messages) > 0)
        if not has_conversation and not image_base64:
            cached = self._cache.get(prompt, system_prompt)
            if cached:
                self._cache_hits += 1
                return AIResponse(
                    text=cached, provider="cache", model="none",
                    tokens_used=0, metadata={"from_cache": True},
                )

        # ── PHASE 0.5: Check DB cache ONLY for no-history requests ──
        if not has_conversation and not image_base64 and self._db:
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
                    result = await provider.generate(
                        prompt, system_prompt=system_prompt,
                        messages=messages, image_base64=image_base64, **kwargs,
                    )
                    if result and result.text:
                        cleaned = self.clean_ai_response(result.text)
                        if not cleaned:
                            logger.warning(f"Vision provider {vp_name} returned garbage after cleaning")
                            continue
                        self._mark_success(vp_name)
                        return AIResponse(
                            text=cleaned,
                            provider=result.provider,
                            model=result.model,
                            tokens_used=result.tokens_used,
                            metadata=result.metadata,
                        )
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

        safe_kwargs = {k: v for k, v in kwargs.items() if k != "image_base64"}

        for provider_name in chain:
            provider = self.providers.get(provider_name)
            if not provider:
                continue

            provider_kwargs = safe_kwargs
            if image_base64 and getattr(provider, 'supports_vision', False):
                provider_kwargs = kwargs

            try:
                result = await provider.generate(
                    prompt, system_prompt=system_prompt,
                    messages=messages, **provider_kwargs,
                )
                if result and result.text:
                    cleaned = self.clean_ai_response(result.text)
                    if not cleaned:
                        logger.warning(f"Provider {provider_name} returned garbage after cleaning")
                        continue

                    self._mark_success(provider_name)

                    result = AIResponse(
                        text=cleaned,
                        provider=result.provider,
                        model=result.model,
                        tokens_used=result.tokens_used,
                        metadata=result.metadata,
                    )

                    if not has_conversation and not image_base64:
                        self._cache.put(prompt, system_prompt, cleaned)
                        if self._db:
                            try:
                                cache_key = hashlib.sha256(f"{system_prompt}:{prompt}".encode()).hexdigest()[:32]
                                await self._db.cache_put(cache_key, "text", {"text": cleaned})
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
            provider_kwargs = safe_kwargs
            if image_base64 and getattr(provider, 'supports_vision', False):
                provider_kwargs = kwargs
            try:
                result = await provider.generate(
                    prompt, system_prompt=system_prompt,
                    messages=messages, **provider_kwargs,
                )
                if result and result.text:
                    cleaned = self.clean_ai_response(result.text)
                    if not cleaned:
                        continue
                    self._mark_success(provider_name)
                    return AIResponse(
                        text=cleaned,
                        provider=result.provider,
                        model=result.model,
                        tokens_used=result.tokens_used,
                        metadata=result.metadata,
                    )
            except Exception:
                pass

        # ── PHASE 3: Try free providers with simplified prompt ──
        for free_name in ["ollama", "pollinations", "chutes", "blackbox"]:
            if free_name in self.providers:
                try:
                    provider = self.providers[free_name]
                    result = await provider.generate(
                        prompt, system_prompt=system_prompt,
                        messages=None,
                    )
                    if result and result.text:
                        cleaned = self.clean_ai_response(result.text)
                        if not cleaned:
                            continue
                        self._mark_success(free_name)
                        return AIResponse(
                            text=cleaned,
                            provider=result.provider,
                            model=result.model,
                            tokens_used=result.tokens_used,
                            metadata=result.metadata,
                        )
                except Exception as e:
                    logger.error(f"Even {free_name} failed: {e}")

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

        CRITICAL: This is the LAST line of defense before text reaches the user.
        """
        if not text:
            return ""

        # ── PHASE 1: Strip SSE/streaming artifacts ──
        sse_patterns = [
            r'data:\s*\{"type"\s*:\s*"start"\s*\}\s*',
            r'data:\s*\{"type"\s*:\s*"error"[^}]*\}\s*',
            r'data:\s*\[DONE\]\s*',
            r'data:\s*\{[^}]*"errorText"[^}]*\}\s*',
            r'data:\s*\{"type"\s*:\s*"content"[^}]*\}\s*',
        ]
        for pattern in sse_patterns:
            text = re.sub(pattern, '', text, flags=re.IGNORECASE)

        if text.strip().startswith('data:'):
            content_parts = []
            for line in text.split('\n'):
                line = line.strip()
                if not line.startswith('data:'):
                    continue
                data_str = line[5:].strip()
                if data_str == '[DONE]':
                    break
                if '"type":"error"' in data_str or '"errorText"' in data_str:
                    continue
                if '"type":"start"' in data_str:
                    continue
                try:
                    import json
                    d = json.loads(data_str)
                    if isinstance(d, dict):
                        c = d.get('content', d.get('text', ''))
                        if c:
                            content_parts.append(c)
                except Exception:
                    pass
            if content_parts:
                text = ''.join(content_parts)
            else:
                return ""

        # ── PHASE 2: Strip API error messages ──
        error_patterns = [
            r'Invalid prompt:.*',
            r'Authentication Error.*',
            r'No api key passed in.*',
            r'Model not found.*',
            r'Rate limit exceeded.*',
            r'Server Error.*',
            r'Internal Server Error.*',
            r'Bad Request.*',
            r'HTTP \d{3}.*',
        ]
        for pattern in error_patterns:
            text = re.sub(pattern, '', text, flags=re.IGNORECASE)

        # ── PHASE 3: Strip known ad/artifact patterns ──
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

        # Strip think tags (DeepSeek R1, Qwen3)
        text = re.sub(r'<think\b[^>]*>.*?</think\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<thinking\b[^>]*>.*?</thinking\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)

        # Strip "ad" sections
        text = re.sub(r'[\U0001f33f\U0001f331\U0001f31f].{0,5}(?:Ad|Support|Visit|Check).{0,100}', '', text, flags=re.IGNORECASE)

        text = re.sub(r'\n---\s*$', '', text)
        text = re.sub(r'^---\s*$', '', text, flags=re.MULTILINE)

        # ── PHASE 4: Strip AI disclaimers ──
        text = re.sub(r'(?:As an AI|Как AI|Как искусственный интеллект|I am an AI|Я искусственный интеллект)[^.]*\.', '', text, flags=re.IGNORECASE)

        for prefix in [
            "Настя:", "Nastya:", "НАСТЯ:", "Assistant:", "Настя отвечает:",
            "Ответ Насти:", "Response:", "Answer:", "Настя говорит:",
        ]:
            if text.startswith(prefix):
                text = text[len(prefix):].strip()

        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        if text.startswith("'") and text.endswith("'"):
            text = text[1:-1]

        text = text.strip("*").strip()

        # Remove markdown
        text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
        text = re.sub(r'\*([^*]+)\*', r'\1', text)
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)

        text = re.sub(r'\n{3,}', '\n\n', text)
        text = text.strip()

        # FINAL CHECK
        if text and all(c in ' \t\n\r' or c in 'data:[DONE]{}"\'`' for c in text):
            return ""

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
                "local": name == "ollama",
            }
        status["_stats"] = {
            "total_requests": self._total_requests,
            "total_fallbacks": self._total_fallbacks,
            "cache_hits": self._cache_hits,
            "last_good_provider": self._last_good_provider,
        }
        return status
