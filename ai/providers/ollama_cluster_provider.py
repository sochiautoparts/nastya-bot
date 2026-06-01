"""OllamaClusterProvider v32.0 — OPTIMIZED for background + chat fallback.

Architecture v32.0 (HYBRID):
  - Ollama = PRIMARY для фоновых задач (новости, канал)
  - Ollama = FALLBACK для чата (когда Pollinations rate-limited)
  - РАЗДЕЛЬНЫЕ семафоры: чат (высокий приоритет) и фон (низкий приоритет)
  - Сокращённые таймауты: 30с primary, 60с reserve
  - История 4 сообщения (было 6)
  - num_predict=150 (было 100 — слишком коротко, ответы обрезались)
  - Pollinations fallback ВНЕ семафора Ollama

МОДЕЛИ:
  - lakomoor/vikhr-llama-3.2-1b-instruct:1b — 1B, быстрый на CPU
  - qwen3:4b-instruct — 4B, умный, медленный на CPU

Важно: На GitHub Actions (2 CPU, 7GB RAM) реалистичен только ОДИН
параллельный запрос к Ollama. Но чат и фон НЕ должны блокировать друг друга.
"""

import logging
import asyncio
import re
import time
import hashlib
from typing import Any, Dict, List, Optional

import httpx

from ai.providers.base import AIResponse, BaseProvider, ProviderError

logger = logging.getLogger(__name__)

# ── Модели ──────────────────────────────────────────────────
PRIMARY_MODEL = "lakomoor/vikhr-llama-3.2-1b-instruct:1b"
RESERVE_MODEL = "qwen3:4b-instruct"

# Таймауты — v32: СОКРАЩЕНЫ! Нет смысла ждать 90/120с если Pollinations отвечает за 3с
# Vikhr-1B на CPU обычно отвечает за 5-15с, 30с — щедрый лимит
PRIMARY_TIMEOUT_SECONDS = 30.0   # Vikhr-1B (быстрая)
RESERVE_TIMEOUT_SECONDS = 60.0   # Qwen3-4B (медленная на CPU)

# Request priorities
PRIORITY_HIGH = "high"    # User chat
PRIORITY_LOW = "low"      # Background tasks (news, channel)


class OllamaClusterProvider(BaseProvider):
    """Провайдер для Ollama — v32.0 HYBRID.

    v32.0 CHANGES:
    - Раздельные семафоры: _chat_sem (1) + _bg_sem (1)
    - Сокращённые таймауты: 30с/60с
    - num_predict=150 (было 100 — ответы обрезались)
    - История 4 сообщения (было 6)
    - Pollinations fallback ВНЕ семафора Ollama
    """

    name: str = "ollama_cluster"
    supports_streaming: bool = False
    supports_vision: bool = False

    def __init__(self, api_key: str = "", timeout: float = 120.0, base_url: str = "",
                 pollinations_fallback=None):
        super().__init__(api_key="", timeout=timeout)
        self.ollama_url = "http://localhost:11434"
        self._active_url: Optional[str] = None
        self._primary_model: Optional[str] = None
        self._reserve_model: Optional[str] = None
        self._warm: bool = False
        self._installed_models: List[str] = []
        # Раздельные семафоры для чата и фона
        self._chat_sem = asyncio.Semaphore(1)  # Чат — высокий приоритет
        self._bg_sem = asyncio.Semaphore(1)    # Фон — низкий приоритет
        # Per-user message dedup
        self._user_last_msg: Dict[int, float] = {}
        self._request_count: int = 0
        self._error_count: int = 0
        self._primary_requests: int = 0
        self._reserve_requests: int = 0
        self._last_health_check: float = 0
        self._last_health_status: bool = True
        self._HEALTH_CACHE_TTL: int = 300
        # Pollinations fallback
        self._pollinations_fallback = pollinations_fallback

    async def init(self) -> None:
        """Инициализация: автоопределение Ollama."""
        # Пробуем прямой Ollama
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
                resp = await client.get(f"{self.ollama_url}/api/tags")
                if resp.status_code == 200:
                    self._active_url = self.ollama_url
                    data = resp.json()
                    self._installed_models = [m.get("name", "").lower() for m in data.get("models", [])]
                    logger.info(f"Ollama detected at {self.ollama_url}. Models: {self._installed_models}")
        except Exception:
            logger.warning("Ollama not available at startup — will try later")

        if not self._active_url:
            self._active_url = self.ollama_url
            logger.warning(f"Defaulting to {self._active_url} — may not be available yet")

        # Создаём постоянный HTTP клиент
        self._client = httpx.AsyncClient(
            base_url=self._active_url,
            timeout=httpx.Timeout(self.timeout, connect=10.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            headers={"Content-Type": "application/json"},
        )

        # Определяем модели
        self._detect_models()

        logger.info(
            f"OllamaClusterProvider v32.0: url={self._active_url} | "
            f"primary={self._primary_model} | reserve={self._reserve_model}"
        )

    def _detect_models(self) -> None:
        """Определить доступные текстовые модели."""
        self._primary_model = PRIMARY_MODEL
        self._reserve_model = RESERVE_MODEL
        if not self._is_model_installed(PRIMARY_MODEL):
            logger.warning(f"Primary model {PRIMARY_MODEL} not found in installed models")
        if not self._is_model_installed(RESERVE_MODEL):
            logger.warning(f"Reserve model {RESERVE_MODEL} not found in installed models")
        logger.info(f"Models: primary={self._primary_model}, reserve={self._reserve_model}")
        logger.info(f"Installed models: {self._installed_models}")

    def _is_model_installed(self, model_name: str) -> bool:
        """Проверить, установлена ли модель."""
        prefix = model_name.split(":")[0].split("/")[-1]
        return any(prefix in m for m in self._installed_models)

    def is_available(self) -> bool:
        """Всегда пробуем — ошибки обрабатываются в generate()."""
        return True

    async def health_check(self) -> bool:
        """Проверка здоровья Ollama — с кэшированием."""
        now = time.time()
        if now - self._last_health_check < self._HEALTH_CACHE_TTL:
            return self._last_health_status
        try:
            if not self._client:
                self._last_health_status = False
            else:
                resp = await self._client.get("/api/tags", timeout=5.0)
                self._last_health_status = resp.status_code == 200
        except Exception:
            self._last_health_status = False
        self._last_health_check = now
        return self._last_health_status

    async def _warm_up(self) -> None:
        """Прогрев основной модели — загрузка в память."""
        if self._warm or not self._primary_model:
            return
        try:
            logger.info(f"Warming up primary model: {self._primary_model}...")
            resp = await self._client.post(
                "/api/chat",
                json={
                    "model": self._primary_model,
                    "messages": [{"role": "user", "content": "привет"}],
                    "stream": False,
                    "options": {"num_predict": 5},
                    "think": False,
                },
                timeout=httpx.Timeout(120.0, connect=10.0),
            )
            if resp.status_code == 200:
                self._warm = True
                logger.info("Primary model warmed up successfully!")
            else:
                logger.warning(f"Warm-up failed: HTTP {resp.status_code}")
        except Exception as e:
            logger.warning(f"Warm-up error: {e}")

    @staticmethod
    def _strip_think_tags(text: str) -> str:
        """Удалить <think/> блоки из ответа (Qwen3 thinking mode)."""
        text = re.sub(r'<think\b[^>]*>.*?</think\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<thinking\b[^>]*>.*?</thinking\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'</?think[^>]*>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'</?thinking[^>]*>', '', text, flags=re.IGNORECASE)
        return text.strip()

    def _build_messages(
        self,
        prompt: str,
        system_prompt: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Построить массив сообщений для Ollama API."""
        result: List[Dict[str, Any]] = []

        if system_prompt:
            result.append({"role": "system", "content": system_prompt})

        if messages:
            for msg in messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role in ("user", "assistant", "system") and content:
                    result.append({"role": role, "content": content})

        last_is_current = (
            messages and len(messages) > 0
            and messages[-1].get("role") == "user"
            and messages[-1].get("content") == prompt
        )

        if not last_is_current:
            result.append({"role": "user", "content": prompt})

        # Слияние подряд идущих одинаковых ролей
        merged: List[Dict[str, Any]] = []
        for msg in result:
            if merged and merged[-1].get("role") == msg.get("role"):
                prev = merged[-1].get("content", "")
                new = msg.get("content", "")
                merged[-1]["content"] = f"{prev}\n{new}"
            else:
                merged.append(msg)
        return merged

    async def generate(self, prompt: str, **kwargs) -> AIResponse:
        """Генерация ответа через локальный Ollama.

        v32: Раздельные семафоры для чата и фона.
        Pollinations fallback ВНЕ семафора Ollama.
        """
        if not self._client:
            await self.init()

        # Прогрев при первом запросе
        if not self._warm:
            await self._warm_up()

        system_prompt = kwargs.get("system_prompt", "")
        temperature = kwargs.get("temperature", 0.8)
        max_tokens = kwargs.get("max_tokens", 200)
        messages_history = kwargs.get("messages")
        priority = kwargs.get("priority", PRIORITY_HIGH)

        # Ограничение истории — v32: 4 сообщения для скорости
        if messages_history and len(messages_history) > 4:
            logger.info(f"Trimming history: {len(messages_history)} -> 4")
            messages_history = messages_history[-4:]

        # Выбираем семафор по приоритету
        sem = self._chat_sem if priority == PRIORITY_HIGH else self._bg_sem

        async with sem:
            # Попробовать основную модель, затем резервную
            models_to_try = [
                (self._primary_model, PRIMARY_TIMEOUT_SECONDS, "primary"),
                (self._reserve_model, RESERVE_TIMEOUT_SECONDS, "reserve"),
            ]

            last_error = None
            for model, timeout, model_type in models_to_try:
                if not model:
                    continue

                ollama_messages = self._build_messages(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    messages=messages_history,
                )

                # v32: Оптимизированные параметры для CPU
                options = {
                    "temperature": temperature,
                    "num_predict": min(max_tokens, 150),  # v32: 150 (было 100)
                    "num_ctx": 2048,
                }
                payload = {
                    "model": model,
                    "messages": ollama_messages,
                    "stream": False,
                    "options": options,
                    "think": False,  # Всегда выключаем thinking для скорости
                }

                try:
                    self._request_count += 1
                    if model_type == "primary":
                        self._primary_requests += 1
                    else:
                        self._reserve_requests += 1

                    logger.info(
                        f"OllamaCluster: TEXT request -> {model} "
                        f"({model_type}, timeout={timeout}s, priority={priority})"
                    )

                    response = await self._client.post(
                        "/api/chat",
                        json=payload,
                        timeout=httpx.Timeout(timeout, connect=10.0),
                    )
                    response.raise_for_status()
                    data = response.json()

                    text = ""
                    if isinstance(data, dict):
                        msg = data.get("message", {})
                        text = msg.get("content", "") if isinstance(msg, dict) else ""

                    if not text:
                        last_error = ProviderError(self.name, f"Empty response from {model}", retryable=True)
                        logger.warning(f"Empty response from {model}, trying next...")
                        continue

                    # Очистка think-тегов
                    text = self._strip_think_tags(text)
                    if not text:
                        last_error = ProviderError(self.name, f"Empty after cleaning from {model}", retryable=True)
                        continue

                    return AIResponse(
                        text=text,
                        provider=self.name,
                        model=f"ollama:{model}",
                        tokens_used=0,
                        metadata={
                            "local": True,
                            "model_type": model_type,
                            "cluster_url": self._active_url,
                        },
                    )

                except httpx.ConnectError:
                    last_error = ProviderError(self.name, "Ollama server not reachable", retryable=True)
                    continue
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    if status == 404:
                        logger.warning(f"Model {model} not found. Skipping.")
                        last_error = ProviderError(self.name, f"Model {model} not found", retryable=True)
                        continue
                    last_error = ProviderError(
                        self.name,
                        f"HTTP {status}: {exc.response.text[:200]}",
                        retryable=status in (429, 500, 502, 503, 504),
                    )
                    continue
                except httpx.TimeoutException:
                    self._error_count += 1
                    logger.warning(
                        f"TIMEOUT for {model} ({timeout}s, {model_type}). "
                        f"Trying next model..."
                    )
                    last_error = ProviderError(
                        self.name,
                        f"Timeout for {model} ({timeout}s)",
                        retryable=True,
                    )
                    continue
                except Exception as exc:
                    self._error_count += 1
                    last_error = ProviderError(self.name, f"Error with {model}: {exc}", retryable=True)
                    continue

        # Обе Ollama модели не ответили
        # НЕ вызываем Pollinations внутри семафора — освобождаем семафор сначала!
        if self._pollinations_fallback:
            logger.warning("Both Ollama models failed! Trying Pollinations fallback (outside semaphore)...")
            fallback_result = await self._try_pollinations_fallback(
                prompt, system_prompt, messages_history
            )
            if fallback_result:
                return fallback_result

        if last_error:
            raise last_error
        raise ProviderError(self.name, "All models failed (Ollama + Pollinations)", retryable=True)

    async def _try_pollinations_fallback(
        self, prompt: str, system_prompt: str = "",
        messages: Optional[List[Dict]] = None
    ) -> Optional[AIResponse]:
        """Try Pollinations as fallback when both Ollama models fail.
        
        v32: Вызывается ВНЕ семафора Ollama!
        """
        if not self._pollinations_fallback:
            return None
        try:
            result = await self._pollinations_fallback.generate(
                prompt,
                system_prompt=system_prompt,
                messages=messages,
            )
            if result and result.text:
                cleaned = self._strip_think_tags(result.text)
                if cleaned:
                    logger.info("Pollinations text fallback SUCCESS!")
                    return AIResponse(
                        text=cleaned,
                        provider="pollinations_fallback",
                        model=result.model,
                        tokens_used=0,
                        metadata={"fallback": True},
                    )
        except Exception as e:
            logger.warning(f"Pollinations fallback failed: {e}")
        return None

    async def generate_with_context(self, messages: List[Dict[str, str]],
                                     image: Optional[str] = None,
                                     video: Optional[str] = None) -> str:
        """Генерация с учётом истории диалога (для совместимости).

        v32: image/video параметры игнорированы — бот текстовый!
        """
        if not messages:
            return "Привет! О чём хочешь поболтать?"

        model = self._primary_model or PRIMARY_MODEL

        ollama_messages = []
        for msg in messages[-4:]:  # v32: Только 4 последних сообщения
            ollama_messages.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", ""),
            })

        payload = {
            "model": model,
            "messages": ollama_messages,
            "stream": False,
            "think": False,
            "options": {
                "temperature": 0.7,
                "num_ctx": 2048,
                "num_predict": 150,
            },
        }

        try:
            async with self._client.post(
                "/api/chat",
                json=payload,
                timeout=httpx.Timeout(PRIMARY_TIMEOUT_SECONDS, connect=10.0),
            ) as resp:
                if resp.status_code == 200:
                    result = resp.json()
                    text = result.get("message", {}).get("content", "")
                    return self._strip_think_tags(text) if text else "Настя задумалась... Повтори?"
                return f"Ошибка: {resp.status_code}"
        except asyncio.TimeoutError:
            return "Превышено время ожидания ответа от модели"
        except Exception as e:
            return f"Техническая ошибка: {str(e)[:100]}"

    def get_stats(self) -> Dict[str, Any]:
        """Статистика провайдера."""
        return {
            "active_url": self._active_url,
            "primary_model": self._primary_model,
            "reserve_model": self._reserve_model,
            "warm": self._warm,
            "request_count": self._request_count,
            "primary_requests": self._primary_requests,
            "reserve_requests": self._reserve_requests,
            "error_count": self._error_count,
            "installed_models": self._installed_models,
            "pollinations_fallback": self._pollinations_fallback is not None,
        }
