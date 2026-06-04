"""AI Router v54.0 — CLOUD-ONLY POLLINATIONS + LOCAL FALLBACK (optional) + VISION + INLINE + HALLUCINATED LINK FIX!

АРХИТЕКТУРА v54:
  ЧАТ (пользовательские сообщения — ПРИОРИТЕТ):
    1. PollinationsProvider v14 (23-MODEL LOAD BALANCING!)
       - gen.pollinations.ai/v1/chat/completions — OpenAI-compatible
       - 23 chat models: openai, mistral, gpt-5.4-mini, deepseek,
         mistral-4, gemma, llama-scout, openai-fast, gpt-5.5,
         deepseek-pro, gemini, claude-fast, mistral-large,
         llama-maverick, qwen-vision-pro, kimi, kimi-k2.6,
         nova-fast, glm, minimax, grok-4.3, qwen-large, gemini-3.5-flash
       - REMOVED: grok (500), grok-large (500), qwen-vision (error)
       - Automatic failover: if one model fails (429/timeout), next one picks up
       - Weighted round-robin for fair load distribution across models
       - Reasoning: openai-large (GPT-5.4) for complex questions
       - Vision: openai + 12 vision-capable backups
       - Per-model health tracking with cooldown on failures
    2. LlamaCppProvider (Qwen3-4B) — LOCAL FALLBACK (OPTIONAL!)
       - Only loaded when ENABLE_LOCAL_MODEL=true
       - Только когда ВСЕ модели Pollinations недоступны
       - stop=["<think"] — блокирует thinking mode Qwen3
    3. Static fallback — бот ВСЕГДА отвечает

  INLINE MODE:
    - Настя отвечает в любом чате через @asnastya_bot!
    - AI-generated responses in inline mode

  ФОН (новости, канал — LOW PRIORITY AI!):
    - Новости: RSS-парсер + AI-комментарии для канала!
    - sochiautoparts.ru/rss.xml — PRIMARY auto news source!
    - Канал: AI-посты на основе новостей, опросы, факты

  VISION (фото-понимание + поиск по фото):
    - Pollinations vision API — Настя ВИДИТ фото!
    - 13 vision-capable моделей

  URL UNDERSTANDING:
    - Настя читает ссылки и понимает контекст!

  HALLUCINATED LINK FIX v53:
    - FORCE web search when user asks for products/services/links
    - AI-hallucinated commercial URLs are detected and REMOVED
    - Only real URLs from actual search results are kept
"""

import logging
import asyncio
import random
import time
import re
from typing import Any, Dict, List, Optional

from ai.providers.base import AIResponse, ProviderError
from ai.providers.pollinations_provider import (
    PollinationsProvider, REASONING_CHAT, REASONING_COMPLEX,
    MODEL_REASONING, MODEL_VISION, CHAT_MODELS,
)
try:
    from ai.providers.llama_cpp_provider import LlamaCppProvider
    _LLAMA_CPP_AVAILABLE = True
except ImportError:
    LlamaCppProvider = None
    _LLAMA_CPP_AVAILABLE = False
from ai.voice import transcribe_voice_ogg
from bot.config import (
    MODEL_PATH, MODEL_N_CTX, MODEL_N_THREADS,
    MODEL_MAX_TOKENS, MODEL_HISTORY_LIMIT,
    POLLINATIONS_API_KEY, POLLINATIONS_MAX_TOKENS,
    ENABLE_LOCAL_MODEL,
)

logger = logging.getLogger(__name__)

FALLBACK_RESPONSES = [
    "Ммм... Настя задумалась. Повтори? 🤔",
    "Ой, Настя отвлеклась... Что ты сказал? 😅",
    "Блин, Настя задумалась о вечном... Ещё раз? 💅",
    "Настя не расслышала... Говори ещё! 😏",
    "Ой, мысли улетели! Повтори для Насти? 💭",
]


class AIRouter:
    """AI Router v55.0 — 30-MODEL Pollinations + Vision + Image Gen + Local Fallback.

    Chat: Pollinations (30 models, load balanced) → LlamaCpp (if enabled) → static fallback.
    Inline: Pollinations (fast response for @asnastya_bot).
    Vision: Pollinations vision API (14 vision-capable models).
    Image Gen: Pollinations image API (flux model).
    Background: AI-powered news posts for channel (low priority).
    """

    def __init__(self, db=None):
        self._pollinations: Optional[PollinationsProvider] = None
        self._local: Optional[LlamaCppProvider] = None
        self._db = db
        self._total_requests: int = 0
        self._total_fallbacks: int = 0
        self._pollinations_requests: int = 0
        self._local_requests: int = 0
        self._local_fallback_count: int = 0
        self._vision_requests: int = 0

    async def init(self) -> None:
        """Initialize providers: Pollinations MULTI-MODEL + LlamaCpp FALLBACK."""
        # ── 1. Pollinations — PRIMARY (multi-model) ──
        try:
            self._pollinations = PollinationsProvider(
                api_key=POLLINATIONS_API_KEY,
                timeout=45.0,
            )
            await self._pollinations.init()
            model_names = [m[0] for m in CHAT_MODELS]
            logger.info(
                f"PollinationsProvider initialized as PRIMARY "
                f"({len(CHAT_MODELS)} models: {', '.join(model_names)})"
            )
        except Exception as e:
            logger.warning(f"PollinationsProvider init failed: {e}")
            self._pollinations = None

        # ── 2. LlamaCpp — LOCAL FALLBACK (only if enabled AND available!) ──
        if ENABLE_LOCAL_MODEL and MODEL_PATH and _LLAMA_CPP_AVAILABLE and LlamaCppProvider is not None:
            try:
                self._local = LlamaCppProvider(
                    model_path=MODEL_PATH,
                    timeout=65.0,
                    model_config={
                        "n_ctx": MODEL_N_CTX,
                        "n_threads": MODEL_N_THREADS,
                        "n_gpu_layers": 0,
                        "verbose": False,
                        "use_mmap": True,
                        "use_mlock": False,
                    },
                    gen_config={
                        "max_tokens": min(MODEL_MAX_TOKENS, 256),
                        "temperature": 0.82,
                        "top_p": 0.92,
                        "top_k": 50,
                        "repeat_penalty": 1.12,
                    },
                )
                await self._local.init()
                logger.info("LlamaCppProvider initialized as LOCAL FALLBACK")
            except Exception as e:
                logger.warning(f"LlamaCppProvider init failed: {e}")
                self._local = None
        else:
            if not _LLAMA_CPP_AVAILABLE:
                logger.info("llama-cpp-python not installed — running cloud-only (install with: pip install llama-cpp-python)")
            elif ENABLE_LOCAL_MODEL:
                logger.info("ENABLE_LOCAL_MODEL=true but no MODEL_PATH — running cloud-only")
            else:
                logger.info("Local model DISABLED (ENABLE_LOCAL_MODEL not set) — running cloud-only")

        # Log status
        pollinations_status = "active" if self._pollinations and self._pollinations.is_available() else "unavailable"
        local_status = "not_installed" if not _LLAMA_CPP_AVAILABLE else ("disabled" if not ENABLE_LOCAL_MODEL else ("active" if self._local and self._local.is_available() else "unavailable"))
        model_name = self._local._model_name if self._local and self._local._loaded else "none"

        logger.info(
            f"AI Router v55.0 initialized: "
            f"pollinations={pollinations_status} (PRIMARY, {len(CHAT_MODELS)} models, vision=yes, image_gen=yes), "
            f"local={local_status} (FALLBACK, model={model_name}, ENABLE_LOCAL_MODEL={ENABLE_LOCAL_MODEL}), "
            f"news=AI+RSS (no templates!), "
            f"max_tokens={POLLINATIONS_MAX_TOKENS}(cloud)/256(local), history={MODEL_HISTORY_LIMIT}"
        )

    async def close(self) -> None:
        """Close all providers."""
        if self._pollinations:
            try:
                await self._pollinations.close()
            except Exception:
                pass
        if self._local:
            try:
                await self._local.close()
            except Exception:
                pass

    async def chat(self, prompt: str, system_prompt: str = "",
                   messages: Optional[List[Dict]] = None, **kwargs) -> AIResponse:
        """Route chat: Pollinations (multi-model) → Local → static fallback."""
        self._total_requests += 1
        priority = kwargs.get("priority", "high")

        if priority == "high":
            return await self._route_chat(prompt, system_prompt, messages, **kwargs)
        else:
            return await self._route_background(prompt, system_prompt, messages, **kwargs)

    async def vision(self, prompt: str, image_data: bytes,
                     image_format: str = "jpeg", system_prompt: str = "",
                     **kwargs) -> AIResponse:
        """Route vision request: Pollinations vision (multi-model) → fallback."""
        self._total_requests += 1
        self._vision_requests += 1

        # ── 1. Pollinations Vision — PRIMARY ──
        if self._pollinations and self._pollinations.is_available():
            try:
                result = await self._pollinations.generate_vision(
                    prompt=prompt,
                    image_data=image_data,
                    image_format=image_format,
                    system_prompt=system_prompt,
                    max_tokens=600,
                    temperature=0.85,
                )
                if result and result.text:
                    cleaned = self.clean_ai_response(result.text)
                    if cleaned:
                        return AIResponse(
                            text=cleaned,
                            provider=result.provider,
                            model=result.model,
                            tokens_used=result.tokens_used,
                            metadata={**result.metadata, "role": "vision_primary"},
                        )
            except ProviderError as e:
                logger.warning(f"Pollinations vision error: {e}")
            except Exception as e:
                logger.warning(f"Pollinations vision unexpected error: {e}")

        # ── 2. No fallback for vision — local model can't see images ──
        self._total_fallbacks += 1
        logger.warning("Vision failed — Pollinations unavailable, local model can't see images")
        return AIResponse(
            text="Ой, Настя не может разглядеть фотку... Попробуй ещё раз? 📸💅",
            provider="fallback",
            model="none",
            tokens_used=0,
            metadata={"role": "vision_fallback"},
        )

    async def _route_chat(self, prompt: str, system_prompt: str,
                          messages: Optional[List[Dict]], **kwargs) -> AIResponse:
        """Chat route: Pollinations (multi-model) → Local → static fallback."""

        # ── 1. Pollinations — PRIMARY (multi-model load balancing!) ──
        if self._pollinations and self._pollinations.is_available():
            try:
                # Use reasoning for complex queries
                reasoning = REASONING_COMPLEX if len(prompt) > 300 else REASONING_CHAT

                result = await self._pollinations.generate(
                    prompt,
                    system_prompt=system_prompt,
                    messages=messages,
                    max_tokens=POLLINATIONS_MAX_TOKENS,
                    reasoning_effort=reasoning,
                )
                if result and result.text:
                    cleaned = self.clean_ai_response(result.text)
                    if cleaned:
                        self._pollinations_requests += 1
                        return AIResponse(
                            text=cleaned,
                            provider=result.provider,
                            model=result.model,
                            tokens_used=result.tokens_used,
                            metadata={**result.metadata, "role": "primary"},
                        )
            except ProviderError as e:
                err_str = str(e)
                if "429" in err_str:
                    logger.warning("Pollinations rate-limited (all models)! Falling back to local model.")
                elif "All models failed" in err_str:
                    logger.warning("All Pollinations models failed! Falling back to local model.")
                else:
                    logger.warning(f"Pollinations chat error: {e}")
            except Exception as e:
                logger.warning(f"Pollinations unexpected error: {e}")

        # ── 2. LlamaCpp — LOCAL FALLBACK ──
        if self._local and self._local.is_available():
            try:
                result = await self._local.generate(
                    prompt,
                    system_prompt=system_prompt,
                    messages=messages,
                    history_limit=MODEL_HISTORY_LIMIT,
                )
                if result and result.text:
                    cleaned = self.clean_ai_response(result.text)
                    if cleaned:
                        self._local_requests += 1
                        self._local_fallback_count += 1
                        return AIResponse(
                            text=cleaned,
                            provider=result.provider,
                            model=result.model,
                            tokens_used=result.tokens_used,
                            metadata={**result.metadata, "role": "fallback"},
                        )
            except ProviderError as e:
                logger.warning(f"Local model chat error: {e}")
            except Exception as e:
                logger.error(f"Unexpected local model error: {e}")

        # ── 3. Static fallback — bot ALWAYS responds ──
        self._total_fallbacks += 1
        logger.error("All AI providers unavailable! Using static fallback.")
        return AIResponse(
            text=self.get_fallback_response(),
            provider="fallback",
            model="none",
            tokens_used=0,
        )

    async def _route_background(self, prompt: str, system_prompt: str,
                                messages: Optional[List[Dict]], **kwargs) -> AIResponse:
        """Background route: Pollinations → Local → skip."""
        # ── 1. Pollinations (cheaper for background) ──
        if self._pollinations and self._pollinations.is_available():
            try:
                result = await self._pollinations.generate(
                    prompt,
                    system_prompt=system_prompt,
                    messages=messages,
                    max_tokens=300,
                    reasoning_effort=REASONING_CHAT,
                )
                if result and result.text:
                    cleaned = self.clean_ai_response(result.text)
                    if cleaned:
                        self._pollinations_requests += 1
                        return AIResponse(
                            text=cleaned,
                            provider=result.provider,
                            model=result.model,
                            tokens_used=result.tokens_used,
                            metadata={**result.metadata, "role": "bg_primary"},
                        )
            except ProviderError as e:
                logger.warning(f"Pollinations bg error: {e}")
            except Exception as e:
                logger.warning(f"Pollinations bg unexpected: {e}")

        # ── 2. Local model (fallback) ──
        if self._local and self._local.is_available():
            try:
                result = await self._local.generate(
                    prompt,
                    system_prompt=system_prompt,
                    messages=messages,
                )
                if result and result.text:
                    cleaned = self.clean_ai_response(result.text)
                    if cleaned:
                        self._local_requests += 1
                        return AIResponse(
                            text=cleaned,
                            provider=result.provider,
                            model=result.model,
                            tokens_used=result.tokens_used,
                            metadata={**result.metadata, "role": "bg_fallback"},
                        )
            except ProviderError as e:
                logger.warning(f"Local bg error: {e}")
            except Exception as e:
                logger.error(f"Unexpected local bg error: {e}")

        # ── 3. Background failed — not critical ──
        self._total_fallbacks += 1
        logger.warning("Background task failed (all providers unavailable). Skipping.")
        return AIResponse(
            text="",
            provider="none",
            model="none",
            tokens_used=0,
            metadata={"skipped": True},
        )

    async def transcribe_voice(self, ogg_bytes: bytes) -> Optional[str]:
        """Transcribe voice message."""
        return await transcribe_voice_ogg(ogg_bytes)

    async def generate_image(self, prompt: str, size: str = "1024x1024") -> Optional[bytes]:
        """Generate an image using Pollinations image API.

        Returns image bytes or None on failure.
        """
        if self._pollinations and self._pollinations.is_available():
            try:
                return await self._pollinations.generate_image(prompt, size=size)
            except Exception as e:
                logger.warning(f"Image generation error: {e}")
        return None

    @staticmethod
    def clean_ai_response(text: str) -> str:
        """Aggressively clean AI response artifacts."""
        if not text:
            return ""

        # Strip SSE artifacts
        sse_patterns = [
            r'data:\s*\{"type"\s*:\s*"start"\s*\}\s*',
            r'data:\s*\{"type"\s*:\s*"error"[^}]*\}\s*',
            r'data:\s*\[DONE\]\s*',
            r'data:\s*\{[^}]*"errorText"[^}]*\}\s*',
        ]
        for pattern in sse_patterns:
            text = re.sub(pattern, '', text, flags=re.IGNORECASE)

        # Strip think tags (Qwen3, reasoning models)
        text = re.sub(r'<think\b[^>]*>.*?</think\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<thinking\b[^>]*>.*?</thinking\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'</?think[^>]*>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'</?thinking[^>]*>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'<think\b[^>]*$', '', text, flags=re.IGNORECASE)

        # Strip /no_think prefix
        text = re.sub(r'^/no_think\s*', '', text)

        # Strip AI disclaimers
        text = re.sub(r'(?:As an AI|Как AI|Как искусственный интеллект)[^.]*\.', '', text, flags=re.IGNORECASE)

        # Strip prefixes
        for prefix in ["Настя:", "Nastya:", "НАСТЯ:", "Assistant:", "Ответ Насти:"]:
            if text.startswith(prefix):
                text = text[len(prefix):].strip()

        # Strip quotes
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        if text.startswith("'") and text.endswith("'"):
            text = text[1:-1]

        text = text.strip("*").strip()

        # Strip markdown
        text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
        text = re.sub(r'\*([^*]+)\*', r'\1', text)
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*[-•]\s+', '', text, flags=re.MULTILINE)

        # v51: Link protection moved to chat.py _clean_response()
        # and _remove_hallucinated_urls()
        # The old code here was replacing all non-whitelisted URLs with the channel link,
        # which broke product/service links. Now hallucinated commercial URLs are
        # detected and removed in _remove_hallucinated_urls().

        # Clean up whitespace
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = text.strip()

        return text

    def get_fallback_response(self) -> str:
        return random.choice(FALLBACK_RESPONSES)

    def get_status(self) -> Dict[str, Any]:
        status = {}
        status["pollinations"] = {
            "available": self._pollinations is not None and self._pollinations.is_available(),
            "role": "PRIMARY",
            "models": len(CHAT_MODELS),
            "vision": True,
        }
        # Add model stats if available
        if self._pollinations:
            try:
                status["pollinations"]["model_stats"] = self._pollinations.get_model_stats()
            except Exception:
                pass
        if self._local:
            stats = self._local.get_stats()
            status["local"] = {
                "available": self._local.is_available(),
                "role": "FALLBACK",
                **stats,
            }
        else:
            status["local"] = {"available": False, "role": "FALLBACK", "model_name": "none"}
        status["_stats"] = {
            "total_requests": self._total_requests,
            "total_fallbacks": self._total_fallbacks,
            "pollinations_requests": self._pollinations_requests,
            "local_requests": self._local_requests,
            "local_fallback_count": self._local_fallback_count,
            "vision_requests": self._vision_requests,
        }
        return status
