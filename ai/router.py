"""AI Router v25.0 — Production Cluster Edition.

АРХИТЕКТУРА: Локальный Ollama + Pollinations fallback!
  - PRIMARY провайдер: OllamaClusterProvider (локальный inference)
  - FALLBACK провайдер: PollinationsProvider (только текст, бесплатный)
  - Модели: phi4-mini:3.8b (text), qwen3-vl:2b (vision)
  - Кэширование повторяющихся запросов
  - НИКАКИХ каскадных ошибок через 12 провайдеров
  - НИКАКИХ таймаутов на 260+ секунд
  - Vision РАБОТАЕТ — image_base64 корректно передаётся
  - Pollinations ТОЛЬКО для текста — vision всегда через Ollama

Изменения v25.0 vs v23.0:
  - ДОБАВЛЕН PollinationsProvider как fallback (бесплатный, без API ключа)
  - Если Ollama недоступен для текста — Pollinations берёт на себя
  - Vision запросы ВСЕГДА через Ollama (Pollinations НЕ используется для vision)
  - Модель текста: phi4-mini:3.8b (вместо qwen3:1.7b)
"""
import logging
import random
import time
import re
import hashlib
from typing import Any, Dict, List, Optional

from ai.providers.base import AIResponse, ProviderError
from ai.providers.ollama_cluster_provider import OllamaClusterProvider
from ai.providers.pollinations_provider import PollinationsProvider
from ai.voice import transcribe_voice_ogg
from bot.config import OLLAMA_BASE_URL, CACHE_TTL_TEXT, CACHE_MAX_MEMORY

logger = logging.getLogger(__name__)

# Fallback — если даже Ollama упал
FALLBACK_RESPONSES = [
    "Ммм... Настя задумалась. Повтори? 🤔",
    "Ой, Настя отвлеклась... Что ты сказал? 😅",
    "Блин, Настя задумалась о вечном... Ещё раз? 💅",
    "Настя не расслышала... Говори ещё! 😏",
    "Ой, мысли улетели! Повтори для Насти? 💭",
]


class AICache:
    """Простой in-memory LRU кэш для AI ответов."""

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
    """Центральный AI-маршрутизатор — v25.0 Production Cluster.

    Primary: OllamaClusterProvider (локальный inference).
    Fallback: PollinationsProvider (только текст, бесплатный).
    Vision: ВСЕГДА через Ollama.
    """

    def __init__(self, db=None):
        self.provider: Optional[OllamaClusterProvider] = None
        self._pollinations: Optional[PollinationsProvider] = None
        self._cache = AICache()
        self._db = db
        self._total_requests: int = 0
        self._total_fallbacks: int = 0
        self._cache_hits: int = 0

    async def init(self) -> None:
        """Инициализация провайдеров — OllamaClusterProvider + Pollinations fallback."""
        self.provider = OllamaClusterProvider(
            timeout=180.0,
            base_url=OLLAMA_BASE_URL,
        )
        await self.provider.init()

        # Try to initialize PollinationsProvider as text fallback
        # Don't fail if it doesn't work — it's optional
        try:
            self._pollinations = PollinationsProvider(timeout=30.0)
            await self._pollinations.init()
            logger.info("PollinationsProvider initialized as text fallback")
        except Exception as e:
            logger.warning(f"PollinationsProvider init failed (non-critical): {e}")
            self._pollinations = None

        stats = self.provider.get_stats()
        pollinations_status = "active" if self._pollinations else "unavailable"
        logger.info(
            f"AI Router v25.0 initialized: "
            f"url={stats['active_url']}, "
            f"text_model={stats['text_model']}, "
            f"vision_model={stats['vision_model']}, "
            f"vision={stats['vision_available']}, "
            f"pollinations_fallback={pollinations_status}"
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
        """Маршрутизация текстового/vision чата.

        ЕДИНСТВЕННЫЙ путь: OllamaClusterProvider.
        Если провайдер недоступен — fallback-ответ.
        
        v24.0: Добавлен параметр priority (high/low) для приоритизации
        пользовательских запросов над фоновыми задачами.
        """
        # Извлекаем image_base64 из kwargs (критически важно!)
        image_base64 = kwargs.pop("image_base64", None)
        self._total_requests += 1

        # Проверка кэша (только без истории и без изображения)
        has_conversation = bool(messages and len(messages) > 0)
        if not has_conversation and not image_base64:
            cached = self._cache.get(prompt, system_prompt)
            if cached:
                self._cache_hits += 1
                return AIResponse(
                    text=cached, provider="cache", model="none",
                    tokens_used=0, metadata={"from_cache": True},
                )

        # Проверка DB кэша
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

        # ── ОСНОВНОЙ ПУТЬ: OllamaClusterProvider ──
        if self.provider:
            try:
                # Передаём image_base64 корректно — как именованный аргумент
                gen_kwargs = {}
                if image_base64:
                    gen_kwargs["image_base64"] = image_base64
                # v25.0: Pass priority to provider (high for user chat, low for background)
                gen_kwargs["priority"] = kwargs.get("priority", "high")

                result = await self.provider.generate(
                    prompt,
                    system_prompt=system_prompt,
                    messages=messages,
                    **gen_kwargs,
                )

                if result and result.text:
                    cleaned = self.clean_ai_response(result.text)
                    if cleaned:
                        # Кэшируем
                        if not has_conversation and not image_base64:
                            self._cache.put(prompt, system_prompt, cleaned)
                            if self._db:
                                try:
                                    cache_key = hashlib.sha256(f"{system_prompt}:{prompt}".encode()).hexdigest()[:32]
                                    await self._db.cache_put(cache_key, "text", {"text": cleaned})
                                except Exception:
                                    pass
                        return AIResponse(
                            text=cleaned,
                            provider=result.provider,
                            model=result.model,
                            tokens_used=result.tokens_used,
                            metadata=result.metadata,
                        )
            except ProviderError as e:
                logger.warning(f"OllamaClusterProvider error: {e}")
            except Exception as e:
                logger.error(f"Unexpected AI error: {e}")

        # ── FALLBACK 1: PollinationsProvider (text only!) ──
        # Only use Pollinations for text requests, NEVER for vision
        if self._pollinations and not image_base64:
            try:
                logger.info("Trying PollinationsProvider as text fallback...")
                result = await self._pollinations.generate(
                    prompt,
                    system_prompt=system_prompt,
                    messages=messages,
                )
                if result and result.text:
                    cleaned = self.clean_ai_response(result.text)
                    if cleaned:
                        # Кэшируем
                        if not has_conversation:
                            self._cache.put(prompt, system_prompt, cleaned)
                        return AIResponse(
                            text=cleaned,
                            provider=result.provider,
                            model=result.model,
                            tokens_used=result.tokens_used,
                            metadata={**result.metadata, "fallback": True},
                        )
            except ProviderError as e:
                logger.warning(f"PollinationsProvider fallback error: {e}")
            except Exception as e:
                logger.error(f"PollinationsProvider fallback unexpected error: {e}")

        # ── FALLBACK 2 — бот ВСЕГДА отвечает ──
        self._total_fallbacks += 1
        logger.error("All providers unavailable! Using static fallback response.")
        return AIResponse(
            text=self.get_fallback_response(),
            provider="fallback",
            model="none",
            tokens_used=0,
        )

    async def chat_with_image(self, prompt: str, image_base64: str,
                              system_prompt: str = "", **kwargs) -> AIResponse:
        """Vision-запрос с изображением.

        v25.0: ПЕРЕДАЁТ image_base64 КОРРЕКТНО — через kwargs в chat(),
        который делает kwargs.pop("image_base64"), а затем передаёт
        как именованный аргумент в provider.generate().

        Vision ВСЕГДА через Ollama — Pollinations НЕ используется.
        """
        logger.info(
            f"chat_with_image: prompt={prompt[:50]}, "
            f"img_size={len(image_base64)} chars"
        )
        # Ограничиваем историю для vision
        messages = kwargs.get("messages")
        if messages and len(messages) > 10:
            logger.info(f"Trimming messages from {len(messages)} to 10 for vision")
            kwargs["messages"] = messages[-10:]

        return await self.chat(
            prompt=prompt,
            system_prompt=system_prompt,
            image_base64=image_base64,
            **kwargs,
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

        # Strip think tags (Qwen3)
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
        status["pollinations_fallback"] = {
            "available": self._pollinations is not None,
        }
        status["_stats"] = {
            "total_requests": self._total_requests,
            "total_fallbacks": self._total_fallbacks,
            "cache_hits": self._cache_hits,
        }
        return status
