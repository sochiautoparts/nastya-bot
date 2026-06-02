"""AI Router v41.0 — POLLINATIONS PRIMARY + LOCAL FALLBACK!

АРХИТЕКТУРА v41:
  ЧАТ (пользовательские сообщения — ПРИОРИТЕТ):
    1. PollinationsProvider (gpt-oss-20b) — PRIMARY
       - Cloud API, быстрый (5-15 сек), умный
       - reasoning_effort='low' для чата, 'medium' для сложных
       - API ключ для повышенных лимитов
       - 429 кулдаун: 60 сек после rate limit
    2. LlamaCppProvider (Qwen3-4B) — LOCAL FALLBACK
       - Когда Pollinations недоступен (429, timeout, etc.)
       - stop=["<think"] — блокирует thinking mode Qwen3
       - /no_think для отключения thinking
    3. Static fallback — бот ВСЕГДА отвечает

  ФОН (новости, канал — БЕЗ AI!):
    - Новости: RSS-парсер + шаблонные комментарии (news.py)
    - Канал: шаблонные посты, опросы, факты (channel.py)
    - AI НЕ вызывается для фоновых задач!

  Ключевые преимущества v41:
    - БЫСТРО: Pollinations ответы за 5-15 сек вместо 20-89!
    - УМНО: gpt-oss-20b с reasoning — лучше понимает контекст
    - НАДЁЖНО: локальная модель как fallback если облако упало
    - НЕ ОБРЕЗАНО: max_tokens=512 для Pollinations (было 200)
"""

import logging
import asyncio
import random
import time
import re
from typing import Any, Dict, List, Optional

from ai.providers.base import AIResponse, ProviderError
from ai.providers.pollinations_provider import PollinationsProvider, REASONING_CHAT, REASONING_COMPLEX
from ai.providers.llama_cpp_provider import LlamaCppProvider
from ai.voice import transcribe_voice_ogg
from bot.config import (
    MODEL_PATH, MODEL_N_CTX, MODEL_N_THREADS,
    MODEL_MAX_TOKENS, MODEL_HISTORY_LIMIT,
    POLLINATIONS_API_KEY,
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
    """AI Router v41.0 — Pollinations PRIMARY + Local FALLBACK.

    Chat: Pollinations → LlamaCpp → static fallback.
    Background: NO AI — RSS + templates!
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

    async def init(self) -> None:
        """Initialize providers: Pollinations PRIMARY + LlamaCpp FALLBACK."""
        # ── 1. Pollinations — PRIMARY ──
        try:
            self._pollinations = PollinationsProvider(
                api_key=POLLINATIONS_API_KEY,
                timeout=45.0,
            )
            await self._pollinations.init()
            logger.info("PollinationsProvider initialized as PRIMARY for chat")
        except Exception as e:
            logger.warning(f"PollinationsProvider init failed: {e}")
            self._pollinations = None

        # ── 2. LlamaCpp — LOCAL FALLBACK ──
        if MODEL_PATH:
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
            logger.info("No MODEL_PATH set — running without local model (cloud only)")

        # Log status
        pollinations_status = "active" if self._pollinations and self._pollinations.is_available() else "unavailable"
        local_status = "active" if self._local and self._local.is_available() else "unavailable"
        model_name = self._local._model_name if self._local and self._local._loaded else "none"

        logger.info(
            f"AI Router v41.0 initialized: "
            f"pollinations={pollinations_status} (PRIMARY), "
            f"local={local_status} (FALLBACK, model={model_name}), "
            f"news=RSS+templates (no AI), "
            f"max_tokens={MODEL_MAX_TOKENS}, history={MODEL_HISTORY_LIMIT}"
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
        """Route chat: Pollinations → Local → static fallback."""
        self._total_requests += 1
        priority = kwargs.get("priority", "high")

        if priority == "high":
            return await self._route_chat(prompt, system_prompt, messages, **kwargs)
        else:
            return await self._route_background(prompt, system_prompt, messages, **kwargs)

    async def _route_chat(self, prompt: str, system_prompt: str,
                          messages: Optional[List[Dict]], **kwargs) -> AIResponse:
        """Chat route: Pollinations → Local → static fallback."""

        # ── 1. Pollinations — PRIMARY ──
        if self._pollinations and self._pollinations.is_available():
            try:
                # Use 'medium' reasoning for complex queries
                reasoning = REASONING_COMPLEX if len(prompt) > 200 else REASONING_CHAT

                result = await self._pollinations.generate(
                    prompt,
                    system_prompt=system_prompt,
                    messages=messages,
                    max_tokens=512,  # Pollinations can handle longer responses
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
                    logger.warning("Pollinations rate-limited! Falling back to local model.")
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
                    max_tokens=256,
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

        # Strip think tags (Qwen3, gpt-oss-20b reasoning)
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
        }
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
        }
        return status
