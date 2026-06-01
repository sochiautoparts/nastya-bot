"""OllamaClusterProvider — единый провайдер для Ollama.

Architecture v31.0 (CPU-OPTIMIZED):
  - Единая точка входа: прямой Ollama (:11434)
  - ПЕРЕСТАВЛЕНЫ МОДЕЛИ: Vikhr-1B (основная, БЫСТРАЯ) + Qwen3-4B (резерв)
  - ВЕТВЛЕНИЕ: если основная модель таймаутит/ошибается → автоматически резервная
  - Семафор текста: 1 (1 параллельный запрос — CPU не тянет больше)
  - Таймаут основной модели (Vikhr-1B): 90с (реалистично для CPU)
  - Таймаут резервной модели (Qwen3-4B): 120с (медленная на CPU)
  - Pollinations fallback при недоступности обеих Ollama моделей
  - Per-user дедупликация — отбрасываем старые сообщения от того же юзера
  - НЕТ ОБРАБОТКИ ФОТО — бот чисто текстовый!
  - num_ctx=2048 — оптимально для CPU (было 4096 — слишком много)
  - max_tokens=100 — Настя говорит коротко (было 400 — слишком много)
  - История 6 сообщений (было 12 — слишком много для CPU)
  - Локальный inference — БЕЗ внешних API (кроме Pollinations fallback)

МОДЕЛИ (ПЕРЕСТАВЛЕНЫ!):
  - lakomoor/vikhr-llama-3.2-1b-instruct — 1B params, 0.8GB, русский оптимизирован
    ОСНОВНАЯ: на CPU отвечает за 5-15 секунд вместо 45+ у Qwen3
  - qwen3:4b-instruct — 4.02B params, 2.5GB, 32K контекст, 119 языков
    РЕЗЕРВНАЯ: слишком медленная на 2 CPU как основная

ВАЖНО: На GitHub Actions (2 CPU, 7GB RAM) реалистичен только ОДИН экземпляр
Ollama. Поэтому провайдер поддерживает прямой Ollama с автоматическим откатом
на резервную модель и Pollinations.
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
# v31: ПЕРЕСТАВЛЕНЫ! Vikhr-1B теперь ОСНОВНАЯ — она в 5-10x быстрее на CPU
# Qwen3-4B слишком медленная для 2 CPU ядер как основная модель
# ВАЖНО: Тег :1b ОБЯЗАТЕЛЕН! Без него Ollama выдаёт "file does not exist"
PRIMARY_MODEL = "lakomoor/vikhr-llama-3.2-1b-instruct:1b"
# Резервная модель — Qwen3-4B: умная, но медленная на CPU
RESERVE_MODEL = "qwen3:4b-instruct"

# Таймауты — v31: УВЕЛИЧЕНЫ для реальной работы на CPU
# Vikhr-1B на CPU обычно отвечает за 5-15с, но при нагрузке может дойти до 90с
PRIMARY_TIMEOUT_SECONDS = 90.0   # Vikhr-1B (основная, быстрая)
RESERVE_TIMEOUT_SECONDS = 120.0  # Qwen3-4B (резерв, медленная на CPU)

# Request priorities
PRIORITY_HIGH = "high"    # User chat
PRIORITY_LOW = "low"      # Background tasks (news, channel)


class ResponseCache:
    """Простой in-memory LRU кэш для AI ответов."""

    def __init__(self, maxsize: int = 200, ttl: int = 1800):
        self._cache: Dict[str, Dict] = {}
        self._maxsize = maxsize
        self._ttl = ttl

    def _get_key(self, prompt: str, model: str = "") -> str:
        content = f"{model}:{prompt}"
        return hashlib.md5(content.encode()).hexdigest()[:32]

    def get(self, prompt: str, model: str = "") -> Optional[str]:
        key = self._get_key(prompt, model)
        entry = self._cache.get(key)
        if entry and time.time() - entry["ts"] < self._ttl:
            return entry["text"]
        if key in self._cache:
            del self._cache[key]
        return None

    def set(self, prompt: str, response: str, model: str = "") -> None:
        key = self._get_key(prompt, model)
        self._cache[key] = {"text": response, "ts": time.time()}
        while len(self._cache) > self._maxsize:
            oldest_key = next(iter(self._cache))
            del self._cache[oldest_key]

    def clear(self) -> None:
        self._cache.clear()


class OllamaClusterProvider(BaseProvider):
    """Провайдер для Ollama — v31.0 CPU-OPTIMIZED.

    v31.0 CRITICAL CHANGES vs v30.0:
    - ПЕРЕСТАВЛЕНЫ МОДЕЛИ: Vikhr-1B = primary, Qwen3-4B = reserve
    - num_ctx=2048 (было 4096 — слишком много для CPU)
    - max_tokens=100 (было 400 — Настя говорит коротко)
    - История 6 сообщений (было 12 — меньше контекста = быстрее)
    - Таймауты увеличены: 90с primary, 120с reserve
    """

    name: str = "ollama_cluster"
    supports_streaming: bool = False
    supports_vision: bool = False  # v28: НЕТ VISION!

    def __init__(self, api_key: str = "", timeout: float = 360.0, base_url: str = "",
                 pollinations_fallback=None):
        super().__init__(api_key="", timeout=timeout)
        # Приоритет: OLOL proxy -> прямой Ollama
        self.cluster_url = base_url or "http://localhost:8000"
        self.ollama_url = "http://localhost:11434"
        self._active_url: Optional[str] = None
        self._primary_model: Optional[str] = None
        self._reserve_model: Optional[str] = None
        self._warm: bool = False
        self._installed_models: List[str] = []
        self._cache = ResponseCache(maxsize=200, ttl=1800)
        # Текстовый семафор — только 1 параллельный запрос на 2 CPU
        # v29: Уменьшен с 2 до 1 — CPU не справляется с 2 параллельными
        self._text_semaphore = asyncio.Semaphore(1)
        # Per-user message dedup
        self._user_last_msg: Dict[int, float] = {}
        self._request_count: int = 0
        self._error_count: int = 0
        self._primary_requests: int = 0
        self._reserve_requests: int = 0
        self._last_health_check: float = 0
        self._last_health_status: bool = True
        self._HEALTH_CACHE_TTL: int = 300
        # Track pending high-priority requests
        self._high_priority_pending: int = 0
        self._low_priority_lock = asyncio.Lock()
        # Pollinations fallback
        self._pollinations_fallback = pollinations_fallback

    async def init(self) -> None:
        """Инициализация: автоопределение Ollama."""
        # Сначала пробуем OLOL Proxy (порт 8000)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
                resp = await client.get(f"{self.cluster_url}/api/tags")
                if resp.status_code == 200:
                    self._active_url = self.cluster_url
                    data = resp.json()
                    self._installed_models = [m.get("name", "").lower() for m in data.get("models", [])]
                    logger.info(f"OLOL Proxy detected at {self.cluster_url}. Models: {self._installed_models}")
        except Exception:
            logger.info("OLOL Proxy not available, trying direct Ollama...")

        # Если OLOL не доступен — пробуем прямой Ollama
        if not self._active_url:
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
                    resp = await client.get(f"{self.ollama_url}/api/tags")
                    if resp.status_code == 200:
                        self._active_url = self.ollama_url
                        data = resp.json()
                        self._installed_models = [m.get("name", "").lower() for m in data.get("models", [])]
                        logger.info(f"Direct Ollama detected at {self.ollama_url}. Models: {self._installed_models}")
            except Exception:
                logger.warning("Neither OLOL Proxy nor direct Ollama available!")

        # Если ничего не найдено — всё равно пробуем
        if not self._active_url:
            self._active_url = self.ollama_url
            logger.warning(f"Defaulting to {self._active_url} — may not be available yet")

        # Создаём постоянный HTTP клиент
        self._client = httpx.AsyncClient(
            base_url=self._active_url,
            timeout=httpx.Timeout(self.timeout, connect=15.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            headers={"Content-Type": "application/json"},
        )

        # Определяем модели
        self._detect_models()

        logger.info(
            f"OllamaClusterProvider v31.0 (CPU-OPTIMIZED): url={self._active_url} | "
            f"primary={self._primary_model} | reserve={self._reserve_model}"
        )

    def _detect_models(self) -> None:
        """Определить доступные текстовые модели.

        v28.0: Qwen3-4B (основная) + Vikhr-Llama-1B (резерв).
        """
        # Основная модель — Qwen3-4B
        if self._is_model_installed(PRIMARY_MODEL):
            self._primary_model = PRIMARY_MODEL
        else:
            self._primary_model = PRIMARY_MODEL  # Всё равно ставим — Ollama скачает
            logger.warning(f"Primary model {PRIMARY_MODEL} not found in installed models, will use anyway")

        # Резервная модель — Vikhr-Llama-1B
        if self._is_model_installed(RESERVE_MODEL):
            self._reserve_model = RESERVE_MODEL
        else:
            self._reserve_model = RESERVE_MODEL
            logger.warning(f"Reserve model {RESERVE_MODEL} not found in installed models, will use anyway")

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
        if self._high_priority_pending > 0:
            logger.info("Skipping warm-up — high-priority requests pending")
            return
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
                },
                timeout=httpx.Timeout(300.0, connect=10.0),
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
        """Построить массив сообщений для Ollama API (БЕЗ изображений!)."""
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

        v29.0: Основная модель → Резервная модель → Pollinations → static fallback.
        НЕТ VISION — только текст!
        
        v29 CRITICAL: Qwen3-4B-instruct БЕЗ thinking mode — быстрые ответы!
        """
        if not self._client:
            await self.init()

        # Прогрев при первом запросе
        if not self._warm:
            await self._warm_up()

        system_prompt = kwargs.get("system_prompt", "")
        temperature = kwargs.get("temperature", 0.8)
        max_tokens = kwargs.get("max_tokens", 400)
        messages_history = kwargs.get("messages")
        priority = kwargs.get("priority", PRIORITY_HIGH)

        # Ограничение истории — v31: 6 сообщений для скорости на CPU
        # 12 сообщений = слишком много для 2 CPU ядер, модели таймаутятся
        if messages_history and len(messages_history) > 6:
            logger.info(f"Trimming history: {len(messages_history)} -> 6")
            messages_history = messages_history[-6:]

        # Проверка кэша (только для запросов без истории)
        has_conversation = bool(messages_history and len(messages_history) > 0)
        if not has_conversation:
            cached = self._cache.get(prompt, model=self._primary_model or "default")
            if cached:
                logger.info(f"Cache hit for prompt: {prompt[:50]}")
                return AIResponse(
                    text=cached, provider="ollama_cluster",
                    model=self._primary_model or "cached",
                    tokens_used=0, metadata={"from_cache": True},
                )

        # Track high-priority requests
        if priority == PRIORITY_HIGH:
            self._high_priority_pending += 1

        try:
            async with self._text_semaphore:
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

                    # v31: Оптимизированные параметры для CPU
                    options = {
                        "temperature": temperature,
                        "num_predict": min(max_tokens, 100),  # v31: Макс 100 токенов — Настя говорит коротко!
                        "num_ctx": 2048,  # v31: Было 4096 — слишком много для CPU
                    }
                    payload = {
                        "model": model,
                        "messages": ollama_messages,
                        "stream": False,
                        "options": options,
                    }
                    # Qwen3 thinking отключение — для любой модели выключаем thinking
                    # v31: Всегда выключаем thinking для скорости на CPU
                    payload["think"] = False

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
                            timeout=httpx.Timeout(timeout, connect=15.0),
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

                        # Очистка think-тегов (Qwen3 thinking mode)
                        text = self._strip_think_tags(text)
                        if not text:
                            last_error = ProviderError(self.name, f"Empty after cleaning from {model}", retryable=True)
                            continue

                        # Кэширование
                        if not has_conversation:
                            self._cache.set(prompt, text, model=model)

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
                        await self._try_failover()
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

                # Обе Ollama модели не ответили — Pollinations fallback
                if self._pollinations_fallback:
                    logger.warning("Both Ollama models failed! Trying Pollinations fallback...")
                    fallback_result = await self._try_pollinations_fallback(
                        prompt, system_prompt, messages_history
                    )
                    if fallback_result:
                        return fallback_result

        finally:
            if priority == PRIORITY_HIGH:
                self._high_priority_pending -= 1

        if last_error:
            raise last_error
        raise ProviderError(self.name, "All models failed (Ollama + Pollinations)", retryable=True)

    async def _try_pollinations_fallback(
        self, prompt: str, system_prompt: str = "",
        messages: Optional[List[Dict]] = None
    ) -> Optional[AIResponse]:
        """Try Pollinations as fallback when both Ollama models fail."""
        if not self._pollinations_fallback:
            return None
        for attempt in range(2):
            try:
                result = await self._pollinations_fallback.generate(
                    prompt,
                    system_prompt=system_prompt,
                    messages=messages,
                )
                if result and result.text:
                    cleaned = self._strip_think_tags(result.text)
                    if cleaned:
                        logger.info(f"Pollinations text fallback SUCCESS (attempt {attempt+1})!")
                        return AIResponse(
                            text=cleaned,
                            provider="pollinations_fallback",
                            model=result.model,
                            tokens_used=0,
                            metadata={"fallback": True},
                        )
            except Exception as e:
                err_str = str(e)
                if "502" in err_str or "Bad gateway" in err_str.lower():
                    if attempt < 1:
                        logger.warning("Pollinations 502, retrying in 2s...")
                        await asyncio.sleep(2)
                        continue
                logger.warning(f"Pollinations fallback failed: {e}")
                break
        return None

    async def _try_failover(self) -> None:
        """Переключиться на альтернативный URL при ошибке подключения."""
        old_url = self._active_url
        if old_url == self.cluster_url:
            new_url = self.ollama_url
        else:
            new_url = self.cluster_url

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
                resp = await client.get(f"{new_url}/api/tags")
                if resp.status_code == 200:
                    self._active_url = new_url
                    self._client = httpx.AsyncClient(
                        base_url=new_url,
                        timeout=httpx.Timeout(self.timeout, connect=15.0),
                        limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                        headers={"Content-Type": "application/json"},
                    )
                    logger.info(f"Failover: switched from {old_url} to {new_url}")
        except Exception:
            logger.warning(f"Failover failed: {new_url} not available")

    async def generate_with_context(self, messages: List[Dict[str, str]],
                                     image: Optional[str] = None,
                                     video: Optional[str] = None) -> str:
        """Генерация с учётом истории диалога (для совместимости).

        v31: image/video параметры игнорированы — бот текстовый!
        v31: Использует primary модель (Vikhr-1B), num_ctx=2048, max_tokens=100
        """
        if not messages:
            return "Привет! О чём хочешь поболтать?"

        model = self._primary_model or PRIMARY_MODEL

        ollama_messages = []
        for msg in messages[-6:]:  # v31: Только 6 последних сообщений
            ollama_messages.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", ""),
            })

        payload = {
            "model": model,
            "messages": ollama_messages,
            "stream": False,
            "think": False,  # v31: Всегда выключаем thinking
            "options": {
                "temperature": 0.7,
                "num_ctx": 2048,  # v31: Было 4096
                "num_predict": 100,  # v31: Настя говорит коротко
            },
        }

        try:
            async with self._client.post(
                "/api/chat",
                json=payload,
                timeout=httpx.Timeout(PRIMARY_TIMEOUT_SECONDS, connect=15.0),
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
            "high_priority_pending": self._high_priority_pending,
            "pollinations_fallback": self._pollinations_fallback is not None,
        }
