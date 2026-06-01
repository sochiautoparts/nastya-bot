"""AI Router v36.0 — LLAMA-CPP-PYTHON NATIVE!

АРХИТЕКТУРА v36: Полный переход на llama-cpp-python!

  ЧАТ (пользовательские сообщения — ПРИОРИТЕТ):
    1. LlamaCppProvider (Qwen3-4B-Instruct GGUF — ПРЯМАЯ загрузка в память!)
    2. PollinationsProvider (fallback — если локальная модель упала)
    3. Static fallback — бот ВСЕГДА отвечает

  ФОН (новости, канал — БЕЗ AI!):
    - Новости: RSS-парсер + шаблонные комментарии (news.py)
    - Канал: шаблонные посты, опросы, факты (channel.py)
    - AI НЕ вызывается для фоновых задач!

  Ключевые преимущества llama-cpp-python:
    - Нет Ollama HTTP-сервера — модель в процессе, нулевая задержка
    - AVX2/AVX512 векторизация — в 2-3x быстрее на CPU
    - Меньше памяти — нет overhead на Ollama
    - Полный контроль над параметрами — точная настройка
    - Проще деплой — pip install вместо отдельного сервера
"""

import logging
import asyncio
import random
import time
import re
from typing import Any, Dict, List, Optional

from ai.providers.base import AIResponse, ProviderError
from ai.providers.llama_cpp_provider import LlamaCppProvider
from ai.providers.pollinations_provider import PollinationsProvider
from ai.voice import transcribe_voice_ogg
from bot.config import MODEL_PATH, MODEL_N_CTX, MODEL_N_THREADS, MODEL_MAX_TOKENS

logger = logging.getLogger(__name__)

# Fallback — если даже все провайдеры упали
FALLBACK_RESPONSES = [
    "Ммм... Настя задумалась. Повтори? 🤔",
    "Ой, Настя отвлеклась... Что ты сказал? 😅",
    "Блин, Настя задумалась о вечном... Ещё раз? 💅",
    "Настя не расслышала... Говори ещё! 😏",
    "Ой, мысли улетели! Повтори для Насти? 💭",
]


class AIRouter:
    """Центральный AI-маршрутизатор — v36.0 LLAMA-CPP-PYTHON.

    Чат: LlamaCppProvider → Pollinations → static fallback.
    Фон: НЕ использует AI — RSS + шаблоны!

    Pollinations кулдаун: после 429 не пробуем 5 минут.
    """

    def __init__(self, db=None):
        self.provider: Optional[LlamaCppProvider] = None
        self._pollinations: Optional[PollinationsProvider] = None
        self._db = db
        self._total_requests: int = 0
        self._total_fallbacks: int = 0
        self._pollinations_requests: int = 0
        self._llama_requests: int = 0
        # Pollinations 429 cooldown
        self._pollinations_429_until: float = 0
        self._POLLINATIONS_429_COOLDOWN: float = 300.0

    async def init(self) -> None:
        """Инициализация провайдеров."""
        # LlamaCppProvider — PRIMARY для чата
        self.provider = LlamaCppProvider(
            model_path=MODEL_PATH,
            timeout=60.0,
            model_config={
                "n_ctx": MODEL_N_CTX,
                "n_threads": MODEL_N_THREADS,
                "n_gpu_layers": 0,
                "verbose": False,
                "use_mmap": True,
                "use_mlock": False,
            },
            gen_config={
                "max_tokens": MODEL_MAX_TOKENS,
                "temperature": 0.85,
                "top_p": 0.9,
                "top_k": 40,
                "repeat_penalty": 1.15,
            },
        )
        await self.provider.init()

        # Pollinations — FALLBACK
        try:
            self._pollinations = PollinationsProvider(timeout=25.0)
            await self._pollinations.init()
            logger.info("PollinationsProvider initialized as FALLBACK for chat")
        except Exception as e:
            logger.warning(f"PollinationsProvider init failed: {e}")
            self._pollinations = None

        # Логируем статус
        pollinations_status = "active" if self._pollinations else "unavailable"
        model_name = MODEL_PATH.split("/")[-1] if MODEL_PATH else "none"
        logger.info(
            f"AI Router v36.0 (LLAMA-CPP-PYTHON) initialized: "
            f"chat_primary=llama_cpp({model_name}), "
            f"pollinations={pollinations_status} (fallback only), "
            f"news=RSS+templates (no AI)"
        )

    async def close(self) -> None:
        """Закрыть провайдеры."""
        if self.provider:
            try:
                await self.provider.close()
            except Exception:
                pass
        if self._pollinations:
            try:
                await self._pollinations.close()
            except Exception:
                pass

    async def chat(self, prompt: str, system_prompt: str = "",
                   messages: Optional[List[Dict]] = None, **kwargs) -> AIResponse:
        """Маршрутизация чата — LlamaCpp → Pollinations → static fallback."""
        self._total_requests += 1
        priority = kwargs.get("priority", "high")

        if priority == "high":
            return await self._route_chat(prompt, system_prompt, messages, **kwargs)
        else:
            return await self._route_background(prompt, system_prompt, messages, **kwargs)

    async def _route_chat(self, prompt: str, system_prompt: str,
                          messages: Optional[List[Dict]], **kwargs) -> AIResponse:
        """Маршрут для чата: LlamaCpp → Pollinations → static fallback."""
        # ── 1. LlamaCppProvider (PRIMARY) ──
        if self.provider and self.provider.is_available():
            try:
                result = await self.provider.generate(
                    prompt,
                    system_prompt=system_prompt,
                    messages=messages,
                    **kwargs,
                )
                if result and result.text:
                    cleaned = self.clean_ai_response(result.text)
                    if cleaned:
                        self._llama_requests += 1
                        return AIResponse(
                            text=cleaned,
                            provider=result.provider,
                            model=result.model,
                            tokens_used=result.tokens_used,
                            metadata=result.metadata,
                        )
            except ProviderError as e:
                logger.warning(f"LlamaCpp chat error: {e}")
            except Exception as e:
                logger.error(f"Unexpected LlamaCpp chat error: {e}")

        # ── 2. Pollinations (FALLBACK) — с кулдауном ──
        if self._pollinations and not self.is_pollinations_on_cooldown():
            try:
                result = await self._pollinations.generate(
                    prompt,
                    system_prompt=system_prompt,
                    messages=messages,
                    model_key="default",
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
                            metadata={**result.metadata, "fallback": "pollinations"},
                        )
            except ProviderError as e:
                err_str = str(e)
                if "429" in err_str:
                    self._pollinations_429_until = time.time() + self._POLLINATIONS_429_COOLDOWN
                    logger.warning(f"Pollinations rate-limited (429)! Cooldown for {self._POLLINATIONS_429_COOLDOWN}s")
                else:
                    logger.warning(f"Pollinations chat error: {e}")
            except Exception as e:
                logger.warning(f"Pollinations unexpected error: {e}")

        # ── 3. Static fallback — бот ВСЕГДА отвечает ──
        self._total_fallbacks += 1
        logger.error("All providers unavailable! Using static fallback response.")
        return AIResponse(
            text=self.get_fallback_response(),
            provider="fallback",
            model="none",
            tokens_used=0,
        )

    async def _route_background(self, prompt: str, system_prompt: str,
                                messages: Optional[List[Dict]], **kwargs) -> AIResponse:
        """Маршрут для фона: LlamaCpp → Pollinations → skip."""
        # ── 1. LlamaCppProvider (PRIMARY) ──
        if self.provider and self.provider.is_available():
            try:
                result = await self.provider.generate(
                    prompt,
                    system_prompt=system_prompt,
                    messages=messages,
                    **kwargs,
                )
                if result and result.text:
                    cleaned = self.clean_ai_response(result.text)
                    if cleaned:
                        self._llama_requests += 1
                        return AIResponse(
                            text=cleaned,
                            provider=result.provider,
                            model=result.model,
                            tokens_used=result.tokens_used,
                            metadata=result.metadata,
                        )
            except ProviderError as e:
                logger.warning(f"LlamaCpp background error: {e}")
            except Exception as e:
                logger.error(f"Unexpected LlamaCpp background error: {e}")

        # ── 2. Pollinations (FALLBACK) ──
        if self._pollinations and not self.is_pollinations_on_cooldown():
            try:
                result = await self._pollinations.generate(
                    prompt,
                    system_prompt=system_prompt,
                    messages=messages,
                    model_key="fast",
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
                            metadata={**result.metadata, "fallback": "pollinations"},
                        )
            except ProviderError as e:
                logger.warning(f"Pollinations background fallback error: {e}")
            except Exception as e:
                logger.error(f"Pollinations background unexpected error: {e}")

        # ── 3. Фоновая задача провалена — не критично ──
        self._total_fallbacks += 1
        logger.warning("Background task failed (all providers unavailable). Skipping.")
        return AIResponse(
            text="",
            provider="none",
            model="none",
            tokens_used=0,
            metadata={"skipped": True},
        )

    def is_pollinations_on_cooldown(self) -> bool:
        """Проверить, на кулдауне ли Pollinations."""
        return time.time() < self._pollinations_429_until

    async def transcribe_voice(self, ogg_bytes: bytes) -> Optional[str]:
        """Транскрипция голосового сообщения."""
        return await transcribe_voice_ogg(ogg_bytes)

    @staticmethod
    def clean_ai_response(text: str) -> str:
        """Агрессивная очистка AI-ответов от артефактов."""
        if not text:
            return ""

        # Strip SSE/streaming artifacts
        sse_patterns = [
            r'data:\s*\{"type"\s*:\s*"start"\s*\}\s*',
            r'data:\s*\{"type"\s*:\s*"error"[^}]*\}\s*',
            r'data:\s*\[DONE\]\s*',
            r'data:\s*\{[^}]*"errorText"[^}]*\}\s*',
        ]
        for pattern in sse_patterns:
            text = re.sub(pattern, '', text, flags=re.IGNORECASE)

        # Strip think tags (Qwen3, DeepSeek)
        text = re.sub(r'<think\b[^>]*>.*?</think\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<thinking\b[^>]*>.*?</thinking\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'</?think[^>]*>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'</?thinking[^>]*>', '', text, flags=re.IGNORECASE)

        # Strip /no_think prefix (наша команда)
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

        # Clean up whitespace
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = text.strip()

        return text

    def get_fallback_response(self) -> str:
        return random.choice(FALLBACK_RESPONSES)

    def get_status(self) -> Dict[str, Any]:
        """Статус маршрутизатора."""
        status = {}
        if self.provider:
            stats = self.provider.get_stats()
            status["llama_cpp"] = {
                "available": self.provider.is_available(),
                **stats,
            }
        status["pollinations"] = {
            "available": self._pollinations is not None,
            "on_cooldown": self.is_pollinations_on_cooldown(),
        }
        status["_stats"] = {
            "total_requests": self._total_requests,
            "total_fallbacks": self._total_fallbacks,
            "pollinations_requests": self._pollinations_requests,
            "llama_requests": self._llama_requests,
        }
        return status
