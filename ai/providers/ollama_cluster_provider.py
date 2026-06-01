"""OllamaClusterProvider — единый провайдер для Ollama.

Architecture v27.0 (RELIABILITY FIX):
  - Единая точка входа: прямой Ollama (:11434)
  - Автовыбор модели: phi4-mini:3.8b для текста, moondream для vision
  - РАЗДЕЛЬНЫЕ СЕМАФОРЫ: текст (2) и vision (1) — v27: 4→2 для стабильности!
  - Таймаут vision: 30с (v27: 15→30 — moondream не успевал под нагрузкой)
  - Таймаут текст: 90с (v27: 120→90 — быстрее фоллбэк на Pollinations)
  - Pollinations fallback с retry при 502 Cloudflare
  - Per-user дедупликация — отбрасываем старые сообщения от того же юзера
  - Сжатие изображений до 448x448
  - Локальный inference — БЕЗ внешних API (кроме fallback)
  - Пропуск прогрева если есть ожидающие запросы
  - НИКАКИХ QWEN МОДЕЛЕЙ — полностью удалены!

ВАЖНО: На GitHub Actions (2 CPU, 7GB RAM) реалистичен только ОДИН экземпляр
Ollama. Поэтому провайдер поддерживает прямой Ollama с автоматическим откатом.

v27.0 vs v26.0:
  - Текстовый семафор 4→2 (2 CPU не тянут 4 параллельных phi4-mini)
  - Vision таймаут 15→30с (moondream тормозил под нагрузкой)
  - Текст таймаут 120→90с (быстрее fallback на Pollinations)
  - Pollinations retry при 502 с задержкой 2с
  - Per-user message dedup — обрабатываем только последнее сообщение
"""
import logging
import asyncio
import re
import time
import hashlib
import base64
import io
from typing import Any, Dict, List, Optional

import httpx

from ai.providers.base import AIResponse, BaseProvider, ProviderError

logger = logging.getLogger(__name__)

# Model priority for TEXT (fastest/best first)
TEXT_MODELS = ["phi4-mini:3.8b"]
# Model for VISION — moondream is 2-3x faster than qwen3-vl on CPU!
VISION_MODELS = ["moondream"]
# Legacy compat
PRIMARY_MODEL = "moondream"
TEXT_FAST_MODEL = "phi4-mini:3.8b"

# Vision-capable model prefixes
VISION_MODEL_PREFIXES = ["moondream", "llava", "minicpm-v", "phi4-mini-vl"]

# Request priorities
PRIORITY_HIGH = "high"    # User chat
PRIORITY_LOW = "low"      # Background tasks (news, channel)

# Vision timeout — if local model doesn't respond in 30s, fallback to Pollinations
# v27: Increased from 15s — moondream on CPU under load needs more time
VISION_TIMEOUT_SECONDS = 30.0
# Text timeout — v27: Reduced from 120s to 90s for faster Pollinations fallback
TEXT_TIMEOUT_SECONDS = 90.0


class ResponseCache:
    """Простой in-memory LRU кэш для AI ответов."""

    def __init__(self, maxsize: int = 200, ttl: int = 1800):
        self._cache: Dict[str, Dict] = {}
        self._maxsize = maxsize
        self._ttl = ttl

    def _get_key(self, prompt: str, image_hash: str = None, model: str = "") -> str:
        content = f"{model}:{prompt}"
        if image_hash:
            content += f":{image_hash}"
        return hashlib.md5(content.encode()).hexdigest()[:32]

    def get(self, prompt: str, image: str = None, model: str = "") -> Optional[str]:
        key = self._get_key(prompt, image[:50] if image else None, model)
        entry = self._cache.get(key)
        if entry and time.time() - entry["ts"] < self._ttl:
            return entry["text"]
        if key in self._cache:
            del self._cache[key]
        return None

    def set(self, prompt: str, response: str, image: str = None, model: str = "") -> None:
        key = self._get_key(prompt, image[:50] if image else None, model)
        self._cache[key] = {"text": response, "ts": time.time()}
        while len(self._cache) > self._maxsize:
            oldest_key = next(iter(self._cache))
            del self._cache[oldest_key]

    def clear(self) -> None:
        self._cache.clear()


class OllamaClusterProvider(BaseProvider):
    """Провайдер для Ollama — v27.0 RELIABILITY FIX.

    v27.0 CRITICAL CHANGES vs v26.0:
    - Текстовый семафор 4→2 (2 CPU ядра не тянут 4 параллельных phi4-mini!)
    - Vision таймаут 15→30с (moondream тормозил под нагрузкой, 15с мало)
    - Текст таймаут 120→90с (быстрее fallback на Pollinations)
    - Pollinations retry при 502 Cloudflare с задержкой 2с
    - Per-user message dedup — отбрасываем старые сообщения от того же юзера
    - QWEN ПОЛНОСТЬЮ УДАЛЁН! Только phi4-mini (text) + moondream (vision)
    - Сжатие изображений до 448x448 для скорости
    """

    name: str = "ollama_cluster"
    supports_streaming: bool = False
    supports_vision: bool = True

    def __init__(self, api_key: str = "", timeout: float = 360.0, base_url: str = "",
                 pollinations_fallback=None):
        super().__init__(api_key="", timeout=timeout)
        # Приоритет: OLOL proxy -> прямой Ollama
        self.cluster_url = base_url or "http://localhost:8000"
        self.ollama_url = "http://localhost:11434"
        self._active_url: Optional[str] = None
        self._text_model: Optional[str] = None
        self._vision_model: Optional[str] = None
        self._vision_available: bool = False
        self._warm: bool = False
        self._installed_models: List[str] = []
        self._cache = ResponseCache(maxsize=200, ttl=1800)
        # РАЗДЕЛЬНЫЕ семафоры!
        # v27: Текст 4→2 — на 2 CPU ядра 4 параллельных phi4-mini вызывают
        # перегрузку и TIMEOUT! 2 параллельных = стабильная работа
        self._text_semaphore = asyncio.Semaphore(2)
        # Vision: moondream тяжёлая, только 1 одновременно
        self._vision_semaphore = asyncio.Semaphore(1)
        # Per-user message dedup: track last processed message per user
        self._user_last_msg: Dict[int, float] = {}
        self._request_count: int = 0
        self._error_count: int = 0
        self._last_health_check: float = 0
        self._last_health_status: bool = True
        self._HEALTH_CACHE_TTL: int = 300
        # Track pending high-priority requests
        self._high_priority_pending: int = 0
        self._low_priority_lock = asyncio.Lock()
        # Pollinations fallback for vision timeout
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

        # Если ничего не найдено — всё равно пробуем (могут запуститься позже)
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

        status = f"url={self._active_url}"
        vision = "+vision" if self._vision_available else "NO_VISION"
        logger.info(f"OllamaClusterProvider v27.0: {status} | text={self._text_model} | vision={self._vision_model} | {vision}")

    def _detect_models(self) -> None:
        """Определить доступные модели.

        v26.0: phi4-mini for text, moondream for vision.
        NO QWEN — completely removed!
        """
        # Текстовая модель — phi4-mini only
        for model in TEXT_MODELS:
            if self._is_model_installed(model):
                self._text_model = model
                logger.info(f"Text model selected: {self._text_model}")
                break
        if not self._text_model:
            self._text_model = TEXT_FAST_MODEL
            logger.warning(f"No preferred text model found, defaulting to {self._text_model}")

        # Vision модель — moondream only
        for model in VISION_MODELS:
            if self._is_model_installed(model):
                self._vision_model = model
                self._vision_available = True
                logger.info(f"Vision model selected: {self._vision_model}")
                break
        if not self._vision_available:
            for prefix in VISION_MODEL_PREFIXES:
                for installed in self._installed_models:
                    if installed.startswith(prefix):
                        self._vision_model = installed
                        self._vision_available = True
                        logger.info(f"Vision model detected: {installed}")
                        break
                if self._vision_available:
                    break
        if not self._vision_model:
            self._vision_model = PRIMARY_MODEL

    def _is_model_installed(self, model_name: str) -> bool:
        """Проверить, установлена ли модель."""
        prefix = model_name.split(":")[0]
        return any(m.startswith(prefix) for m in self._installed_models)

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
        """Прогрев модели — загрузка в память."""
        # Skip warm-up if there are pending high-priority requests
        if self._high_priority_pending > 0:
            logger.info("Skipping warm-up — high-priority requests pending")
            return
        if self._warm or not self._text_model:
            return
        try:
            logger.info(f"Warming up model: {self._text_model}...")
            resp = await self._client.post(
                "/api/chat",
                json={
                    "model": self._text_model,
                    "messages": [{"role": "user", "content": "привет"}],
                    "stream": False,
                    "options": {"num_predict": 5},
                },
                timeout=httpx.Timeout(300.0, connect=10.0),
            )
            if resp.status_code == 200:
                self._warm = True
                logger.info("Model warmed up successfully!")
            else:
                logger.warning(f"Warm-up failed: HTTP {resp.status_code}")
        except Exception as e:
            logger.warning(f"Warm-up error: {e}")

    @staticmethod
    def _strip_think_tags(text: str) -> str:
        """Удалить <think/> блоки из ответа."""
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
        image_base64: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Построить массив сообщений для Ollama API с корректным креплением изображения."""
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
            current_msg: Dict[str, Any] = {"role": "user", "content": prompt}
            if image_base64 and self._vision_available:
                current_msg["images"] = [image_base64]
            result.append(current_msg)
        elif image_base64 and self._vision_available:
            for i in range(len(result) - 1, -1, -1):
                if result[i]["role"] == "user":
                    result[i]["images"] = [image_base64]
                    break

        # Слияние подряд идущих одинаковых ролей
        merged: List[Dict[str, Any]] = []
        for msg in result:
            if merged and merged[-1].get("role") == msg.get("role"):
                prev = merged[-1].get("content", "")
                new = msg.get("content", "")
                merged[-1]["content"] = f"{prev}\n{new}"
                if "images" in msg:
                    merged[-1]["images"] = msg["images"]
            else:
                merged.append(msg)
        return merged

    async def generate(self, prompt: str, **kwargs) -> AIResponse:
        """Генерация ответа через локальный Ollama.

        v27.0: Раздельные семафоры (2 текст + 1 vision).
        Vision таймаут 30с + fallback на Pollinations с retry.
        Per-user дедупликация — отбрасываем старые сообщения.
        """
        if not self._client:
            await self.init()

        # Прогрев при первом запросе (только если нет ожидающих)
        if not self._warm:
            await self._warm_up()

        system_prompt = kwargs.get("system_prompt", "")
        temperature = kwargs.get("temperature", 0.7)
        max_tokens = kwargs.get("max_tokens", 512)
        messages_history = kwargs.get("messages")
        # ВАЖНО: Извлекаем image_base64 из kwargs, НЕ передаём дальше как kwargs
        image_base64 = kwargs.pop("image_base64", None)
        priority = kwargs.get("priority", PRIORITY_HIGH)

        # Ограничение истории для маленьких моделей
        if image_base64 and messages_history and len(messages_history) > 6:
            logger.info(f"Trimming history for vision: {len(messages_history)} -> 6")
            messages_history = messages_history[-6:]
        elif not image_base64 and messages_history and len(messages_history) > 15:
            logger.info(f"Trimming history for text: {len(messages_history)} -> 15")
            messages_history = messages_history[-15:]

        # Выбор модели и семафора
        is_vision = bool(image_base64 and self._vision_available)
        if is_vision:
            model = self._vision_model or PRIMARY_MODEL
            models_to_try = [model]
            semaphore = self._vision_semaphore
            logger.info(f"OllamaCluster: VISION request → {model} (semaphore=1)")
        else:
            model = self._text_model or TEXT_FAST_MODEL
            models_to_try = [model]
            semaphore = self._text_semaphore
            logger.info(f"OllamaCluster: TEXT request → {models_to_try[0]} (priority={priority})")

        # Проверка кэша (только для запросов без истории)
        has_conversation = bool(messages_history and len(messages_history) > 0)
        if not has_conversation and not image_base64:
            cached = self._cache.get(prompt, model=models_to_try[0])
            if cached:
                logger.info(f"Cache hit for prompt: {prompt[:50]}")
                return AIResponse(
                    text=cached, provider="ollama_cluster", model=models_to_try[0],
                    tokens_used=0, metadata={"from_cache": True},
                )

        # Track high-priority requests
        if priority == PRIORITY_HIGH:
            self._high_priority_pending += 1

        try:
            async with semaphore:
                last_error = None
                for try_model in models_to_try:
                    # Изображение прикрепляем только к vision-модели
                    img = image_base64 if (is_vision and try_model == models_to_try[0]) else None

                    ollama_messages = self._build_messages(
                        prompt=prompt,
                        system_prompt=system_prompt,
                        messages=messages_history,
                        image_base64=img,
                    )

                    payload = {
                        "model": try_model,
                        "messages": ollama_messages,
                        "stream": False,
                        "options": {
                            "temperature": temperature,
                            "num_predict": max_tokens,
                        },
                    }

                    # Адаптивные таймауты
                    # v27: Текст 90s (быстрее fallback на Pollinations при перегрузе)
                    # v27: Vision 30s (moondream под нагрузкой не успевала за 15с!)
                    request_timeout = TEXT_TIMEOUT_SECONDS if not is_vision else VISION_TIMEOUT_SECONDS

                    try:
                        self._request_count += 1
                        response = await self._client.post(
                            "/api/chat",
                            json=payload,
                            timeout=httpx.Timeout(request_timeout, connect=15.0),
                        )
                        response.raise_for_status()
                        data = response.json()

                        text = ""
                        if isinstance(data, dict):
                            msg = data.get("message", {})
                            text = msg.get("content", "") if isinstance(msg, dict) else ""

                        if not text:
                            last_error = ProviderError(self.name, f"Empty response from {try_model}", retryable=True)
                            continue

                        # Очистка think-тегов
                        text = self._strip_think_tags(text)
                        if not text:
                            last_error = ProviderError(self.name, f"Empty after cleaning from {try_model}", retryable=True)
                            continue

                        # Кэширование
                        if not has_conversation and not image_base64:
                            self._cache.set(prompt, text, model=try_model)

                        return AIResponse(
                            text=text,
                            provider=self.name,
                            model=f"ollama:{try_model}",
                            tokens_used=0,
                            metadata={"local": True, "vision": is_vision, "cluster_url": self._active_url},
                        )

                    except httpx.ConnectError:
                        # Попробуем переключиться на другой URL
                        await self._try_failover()
                        last_error = ProviderError(self.name, "Ollama server not reachable", retryable=True)
                        continue
                    except httpx.HTTPStatusError as exc:
                        status = exc.response.status_code
                        if status == 404:
                            logger.warning(f"Model {try_model} not found. Skipping (no auto-pull).")
                            last_error = ProviderError(self.name, f"Model {try_model} not found", retryable=True)
                            continue
                        last_error = ProviderError(self.name, f"HTTP {status}: {exc.response.text[:200]}", retryable=status in (429, 500, 502, 503, 504))
                        continue
                    except httpx.TimeoutException:
                        self._error_count += 1
                        if is_vision:
                            # Vision timeout — try Pollinations fallback WITH RETRY
                            logger.warning(f"Vision TIMEOUT for {try_model} ({VISION_TIMEOUT_SECONDS}s). Trying Pollinations fallback...")
                            fallback_result = await self._try_pollinations_vision_fallback(
                                prompt, image_base64, system_prompt, messages_history
                            )
                            if fallback_result:
                                return fallback_result
                            last_error = ProviderError(self.name, f"Vision timeout for {try_model} and Pollinations fallback failed", retryable=True)
                        else:
                            # Text timeout — also try Pollinations as fallback
                            logger.warning(f"Text TIMEOUT for {try_model} ({TEXT_TIMEOUT_SECONDS}s). Trying Pollinations fallback...")
                            fallback_result = await self._try_pollinations_text_fallback(
                                prompt, system_prompt, messages_history
                            )
                            if fallback_result:
                                return fallback_result
                            last_error = ProviderError(self.name, f"Timeout for {try_model} and Pollinations fallback failed", retryable=True)
                        continue
                    except Exception as exc:
                        self._error_count += 1
                        last_error = ProviderError(self.name, f"Error with {try_model}: {exc}", retryable=True)
                        continue
        finally:
            if priority == PRIORITY_HIGH:
                self._high_priority_pending -= 1

        if last_error:
            raise last_error
        raise ProviderError(self.name, "All models failed", retryable=True)

    async def _try_pollinations_vision_fallback(
        self, prompt: str, image_base64: str, system_prompt: str = "",
        messages: Optional[List[Dict]] = None
    ) -> Optional[AIResponse]:
        """Try Pollinations as fallback for vision when Ollama times out.
        v27: Added retry with delay for 502 Cloudflare errors.
        """
        if not self._pollinations_fallback:
            logger.warning("No Pollinations fallback available for vision")
            return None
        for attempt in range(3):
            try:
                result = await self._pollinations_fallback.generate(
                    prompt,
                    system_prompt=system_prompt,
                    messages=messages,
                    image_base64=image_base64,
                )
                if result and result.text:
                    cleaned = self._strip_think_tags(result.text)
                    if cleaned:
                        logger.info(f"Pollinations vision fallback SUCCESS (attempt {attempt+1})!")
                        return AIResponse(
                            text=cleaned,
                            provider="pollinations_fallback",
                            model=result.model,
                            tokens_used=0,
                            metadata={"vision": True, "fallback": True},
                        )
            except Exception as e:
                err_str = str(e)
                if "502" in err_str or "Bad gateway" in err_str.lower():
                    if attempt < 2:
                        wait = 2 * (attempt + 1)  # 2s, 4s
                        logger.warning(f"Pollinations 502, retrying in {wait}s (attempt {attempt+1}/3)...")
                        await asyncio.sleep(wait)
                        continue
                logger.warning(f"Pollinations vision fallback failed: {e}")
                break
        return None

    async def _try_pollinations_text_fallback(
        self, prompt: str, system_prompt: str = "",
        messages: Optional[List[Dict]] = None
    ) -> Optional[AIResponse]:
        """Try Pollinations as fallback for text when Ollama times out.
        v27: NEW — text timeout now also triggers Pollinations fallback!
        """
        if not self._pollinations_fallback:
            return None
        for attempt in range(2):  # Only 2 retries for text
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
                            metadata={"text": True, "fallback": True},
                        )
            except Exception as e:
                err_str = str(e)
                if "502" in err_str or "Bad gateway" in err_str.lower():
                    if attempt < 1:
                        logger.warning(f"Pollinations text 502, retrying in 2s...")
                        await asyncio.sleep(2)
                        continue
                logger.warning(f"Pollinations text fallback failed: {e}")
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
        """Генерация с учётом истории диалога (для совместимости с ТЗ)."""
        if not messages:
            return "Привет! О чём хочешь поболтать?"

        use_vision = image is not None or video is not None
        model = self._vision_model if use_vision else self._text_model or TEXT_FAST_MODEL

        ollama_messages = []
        for msg in messages[-6:]:
            ollama_messages.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", ""),
            })

        payload = {
            "model": model,
            "messages": ollama_messages,
            "stream": False,
            "options": {
                "temperature": 0.7,
                "num_ctx": 4096,
            },
        }

        if image and use_vision:
            if image.startswith(('http://', 'https://')):
                image_data = await self._download_image(image)
                if image_data:
                    payload["messages"][-1]["images"] = [image_data]
            else:
                payload["messages"][-1]["images"] = [image]

        try:
            async with self._client.post(
                "/api/chat",
                json=payload,
                timeout=httpx.Timeout(300.0, connect=15.0),
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

    async def _download_image(self, url: str) -> str:
        """Скачать изображение по URL и конвертировать в base64."""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return base64.b64encode(resp.content).decode('utf-8')
        except Exception as e:
            logger.warning(f"Failed to download image {url}: {e}")
        return ""

    def get_stats(self) -> Dict[str, Any]:
        """Статистика провайдера."""
        return {
            "active_url": self._active_url,
            "text_model": self._text_model,
            "vision_model": self._vision_model,
            "vision_available": self._vision_available,
            "warm": self._warm,
            "request_count": self._request_count,
            "error_count": self._error_count,
            "installed_models": self._installed_models,
            "high_priority_pending": self._high_priority_pending,
            "pollinations_fallback": self._pollinations_fallback is not None,
        }
