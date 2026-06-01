"""AI Router v35.0 — RSS-FIRST, NO AI for news!

АРХИТЕКТУРА v35: RSS для новостей, AI ТОЛЬКО для чата!

  ЧАТ (пользовательские сообщения — приоритет СКОРОСТЬ):
    1. OllamaClusterProvider (автоопределяет лучшую модель)
    2. PollinationsProvider (fallback — если Ollama недоступен)
    3. Static fallback — бот ВСЕГДА отвечает

  ФОН (новости, канал — БЕЗ AI!):
    - Новости: RSS-парсер + шаблонные комментарии
    - Канал: шаблонные посты, опросы, факты
    - AI НЕ вызывается для фоновых задач!

  Ключевые изменения v35:
    - Новости через RSS + JSON кэш + шаблоны (news.py)
    - AI используется ТОЛЬКО для пользовательского чата
    - Ollama полностью свободен от фоновой нагрузки
    - Кулдаун Pollinations после 429 (5 минут)
"""
import logging
import asyncio
import random
import time
import re
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
    """Центральный AI-маршрутизатор — v35.0 RSS-FIRST.

    Чат: Ollama → Pollinations (если не на кулдауне) → static fallback.
    Фон: НЕ использует AI — RSS + шаблоны!

    Кулдаун Pollinations: после 429 не пробуем 5 минут.
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
        # Ollama — PRIMARY для ВСЕГО (автоопределяет лучшую модель!)
        self.provider = OllamaClusterProvider(
            timeout=120.0,
            base_url=OLLAMA_BASE_URL,
            pollinations_fallback=None,
        )
        await self.provider.init()

        # Pollinations — FALLBACK (не primary!)
        try:
            self._pollinations = PollinationsProvider(timeout=25.0)
            await self._pollinations.init()
            logger.info("PollinationsProvider initialized as FALLBACK for chat")
        except Exception as e:
            logger.warning(f"PollinationsProvider init failed: {e}")
            self._pollinations = None

        # Логируем статус
        pollinations_status = "active" if self._pollinations else "unavailable"
        primary_model = self.provider.get_stats().get("primary_model", "none")
        reserve_model = self.provider.get_stats().get("reserve_model", "none")
        logger.info(
            f"AI Router v35.0 (RSS-FIRST) initialized: "
            f"chat_primary=ollama({primary_model}), "
            f"reserve=ollama({reserve_model}), "
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
        """Маршрутизация чата — Ollama → Pollinations → static fallback."""
        self._total_requests += 1
        priority = kwargs.get("priority", "high")

        if priority == "high":
            return await self._route_chat(prompt, system_prompt, messages, **kwargs)
        else:
            return await self._route_background(prompt, system_prompt, messages, **kwargs)

    async def _route_chat(self, prompt: str, system_prompt: str,
                          messages: Optional[List[Dict]], **kwargs) -> AIResponse:
        """Маршрут для чата: Ollama → Pollinations → static fallback."""
        # ── 1. Ollama (PRIMARY для чата) ──
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
                            metadata=result.metadata,
                        )
            except ProviderError as e:
                logger.warning(f"Ollama chat error: {e}")
            except Exception as e:
                logger.error(f"Unexpected Ollama chat error: {e}")

        # ── 2. Pollinations (FALLBACK для чата) — с кулдауном ──
        if self._pollinations and not self.provider.is_pollinations_on_cooldown():
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
                    cooldown_until = time.time() + 300
                    self.provider.set_pollinations_429_cooldown(cooldown_until)
                    logger.warning(f"Pollinations rate-limited (429)! Cooldown until {cooldown_until:.0f}")
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
        """Маршрут для фона: Ollama → Pollinations → skip."""
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
