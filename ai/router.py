"""AI Router v32.0 — HYBRID EDITION.

АРХИТЕКТУРА: Pollinations (облако) для чата + Ollama (локально) для фона!

  ЧАТ (пользовательские сообщения — приоритет СКОРОСТЬ):
    1. PollinationsProvider (GPT-4o-mini — быстрый, умный, бесплатный)
    2. OllamaClusterProvider (локальный fallback при rate-limit)
    3. Static fallback — бот ВСЕГДА отвечает

  ФОН (новости, канал — приоритет НАДЁЖНОСТЬ):
    1. OllamaClusterProvider (локальный, без rate-limit, скорость не критична)
    2. PollinationsProvider (облачный fallback)

  Почему Pollinations для чата:
    - GPT-4o-mini в 10x умнее чем Vikhr-1B
    - Отвечает за 2-5 сек вместо 15-90 сек на CPU
    - Бесплатный, без API ключа
    - Rate limit (429) бывает редко при 1-2 запросах/мин

  Почему Ollama для фона:
    - Бесплатный, без rate-limit
    - Новости/канал не требуют мгновенного ответа
    - 15-30 сек на комментарий — приемлемо
"""
import logging
import asyncio
import random
import time
import re
import hashlib
from typing import Any, Dict, List, Optional

from ai.providers.base import AIResponse, ProviderError
from ai.providers.ollama_cluster_provider import OllamaClusterProvider
from ai.providers.pollinations_provider import PollinationsProvider
from ai.voice import transcribe_voice_ogg
from bot.config import OLLAMA_BASE_URL

logger = logging.getLogger(__name__)

# Fallback — если даже Pollinations + Ollama упали
FALLBACK_RESPONSES = [
    "Ммм... Настя задумалась. Повтори? 🤔",
    "Ой, Настя отвлеклась... Что ты сказал? 😅",
    "Блин, Настя задумалась о вечном... Ещё раз? 💅",
    "Настя не расслышала... Говори ещё! 😏",
    "Ой, мысли улетели! Повтори для Насти? 💭",
]


class AIRouter:
    """Центральный AI-маршрутизатор — v32.0 HYBRID.

    Чат: Pollinations → Ollama → static fallback.
    Фон: Ollama → Pollinations → skip.
    """

    def __init__(self, db=None):
        self.provider: Optional[OllamaClusterProvider] = None
        self._pollinations: Optional[PollinationsProvider] = None
        self._db = db
        self._total_requests: int = 0
        self._total_fallbacks: int = 0
        self._pollinations_requests: int = 0
        self._ollama_requests: int = 0

    async def init(self) -> None:
        """Инициализация провайдеров."""
        # Pollinations — PRIMARY для чата
        try:
            self._pollinations = PollinationsProvider(timeout=25.0)
            await self._pollinations.init()
            logger.info("PollinationsProvider initialized as PRIMARY for chat")
        except Exception as e:
            logger.warning(f"PollinationsProvider init failed: {e}")
            self._pollinations = None

        # Ollama — PRIMARY для фона, FALLBACK для чата
        self.provider = OllamaClusterProvider(
            timeout=120.0,
            base_url=OLLAMA_BASE_URL,
            pollinations_fallback=self._pollinations,
        )
        await self.provider.init()

        pollinations_status = "active" if self._pollinations else "unavailable"
        logger.info(
            f"AI Router v32.0 (HYBRID) initialized: "
            f"chat_primary=pollinations, "
            f"bg_primary=ollama, "
            f"ollama={self.provider.get_stats()['primary_model']}, "
            f"pollinations={pollinations_status}"
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
        """Маршрутизация чата — Pollinations → Ollama → static fallback.

        Priority 'high' = пользовательский чат (Pollinations first).
        Priority 'low' = фоновые задачи (Ollama first).
        """
        self._total_requests += 1
        priority = kwargs.get("priority", "high")

        if priority == "high":
            return await self._route_chat(prompt, system_prompt, messages, **kwargs)
        else:
            return await self._route_background(prompt, system_prompt, messages, **kwargs)

    async def _route_chat(self, prompt: str, system_prompt: str,
                          messages: Optional[List[Dict]], **kwargs) -> AIResponse:
        """Маршрут для чата: Pollinations → Ollama → static fallback.

        Pollinations отвечает за 2-5 секунд (GPT-4o-mini).
        Ollama — резерв при rate-limit (15-30 сек на CPU).
        """
        # ── 1. Pollinations (PRIMARY для чата) ──
        if self._pollinations:
            try:
                result = await self._pollinations.generate(
                    prompt,
                    system_prompt=system_prompt,
                    messages=messages,
                    model_key="default",  # GPT-4o-mini
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
                            metadata=result.metadata,
                        )
            except ProviderError as e:
                err_str = str(e)
                if "429" in err_str:
                    logger.warning(f"Pollinations rate-limited (429), falling back to Ollama...")
                else:
                    logger.warning(f"Pollinations chat error: {e}")
            except Exception as e:
                logger.warning(f"Pollinations unexpected error: {e}")

        # ── 2. Ollama (FALLBACK для чата) ──
        if self.provider:
            try:
                gen_kwargs = {"priority": "high"}
                result = await self.provider.generate(
                    prompt,
                    system_prompt=system_prompt,
                    messages=messages,
                    **gen_kwargs,
                )
                if result and result.text:
                    cleaned = self.clean_ai_response(result.text)
                    if cleaned:
                        self._ollama_requests += 1
                        return AIResponse(
                            text=cleaned,
                            provider=result.provider,
                            model=result.model,
                            tokens_used=result.tokens_used,
                            metadata={**result.metadata, "fallback": "ollama"},
                        )
            except ProviderError as e:
                logger.warning(f"Ollama chat fallback error: {e}")
            except Exception as e:
                logger.error(f"Unexpected Ollama chat error: {e}")

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
        """Маршрут для фона: Ollama → Pollinations → skip.

        Ollama — бесплатный, без rate-limit.
        Pollinations — резерв.
        Если оба не работают — молча пропускаем (фон не критичен).
        """
        # ── 1. Ollama (PRIMARY для фона) ──
        if self.provider:
            try:
                gen_kwargs = {"priority": "low"}
                result = await self.provider.generate(
                    prompt,
                    system_prompt=system_prompt,
                    messages=messages,
                    **gen_kwargs,
                )
                if result and result.text:
                    cleaned = self.clean_ai_response(result.text)
                    if cleaned:
                        self._ollama_requests += 1
                        return AIResponse(
                            text=cleaned,
                            provider=result.provider,
                            model=result.model,
                            tokens_used=result.tokens_used,
                            metadata=result.metadata,
                        )
            except ProviderError as e:
                logger.warning(f"Ollama background error: {e}")
            except Exception as e:
                logger.error(f"Unexpected Ollama background error: {e}")

        # ── 2. Pollinations (FALLBACK для фона) ──
        if self._pollinations:
            try:
                result = await self._pollinations.generate(
                    prompt,
                    system_prompt=system_prompt,
                    messages=messages,
                    model_key="fast",  # Mistral — быстрее для фона
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
            status["ollama_cluster"] = {
                "available": True,
                "healthy": self.provider.is_available(),
                **stats,
            }
        status["pollinations"] = {
            "available": self._pollinations is not None,
        }
        status["_stats"] = {
            "total_requests": self._total_requests,
            "total_fallbacks": self._total_fallbacks,
            "pollinations_requests": self._pollinations_requests,
            "ollama_requests": self._ollama_requests,
        }
        return status
