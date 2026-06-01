"""OllamaClusterProvider — единый провайдер для кластера OLOL или прямого Ollama.

Architecture v23.0 (Production Cluster):
  - Единая точка входа: OLOL Proxy (:8000) или прямой Ollama (:11434)
  - Автовыбор модели: qwen3-vl:2b для vision, qwen3:1.7b для текста
  - Ретраи с экспоненциальной задержкой
  - Семафор для контроля параллельных запросов
  - Сжатие изображений перед отправкой в модель
  - Автоопределение доступных моделей
  - Локальный inference — БЕЗ внешних API!

ВАЖНО: На GitHub Actions (2 CPU, 7GB RAM) реалистичен только ОДИН экземпляр
Ollama. Поэтому провайдер поддерживает как кластер (OLOL proxy), так и
прямой Ollama с автоматическим откатом.
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

# Models — ONLY use what's installed via GitHub Actions workflow!
PRIMARY_MODEL = "qwen3-vl:2b"     # Vision + Text — PRIMARY
TEXT_FAST_MODEL = "qwen3:1.7b"   # Fast text-only fallback

# Vision-capable model prefixes
VISION_MODEL_PREFIXES = ["qwen3-vl", "qwen2.5-vl", "qwen2-vl", "llava", "minicpm-v"]


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
    """Провайдер для Ollama-кластера через OLOL Proxy или прямое подключение.

    Единый провайдер, заменяющий ВСЕ внешние API:
    - Текст: qwen3:1.7b (быстрый) или qwen3-vl:2b (fallback)
    - Vision: qwen3-vl:2b (основная модель)
    - Кэширование повторяющихся запросов
    - Семафор для ограничения параллельных запросов
    - Автоопределение доступных моделей
    """

    name: str = "ollama_cluster"
    supports_streaming: bool = False
    supports_vision: bool = True

    def __init__(self, api_key: str = "", timeout: float = 180.0, base_url: str = ""):
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
        self._semaphore = asyncio.Semaphore(10)  # Не больше 10 одновременных запросов
        self._request_count: int = 0
        self._error_count: int = 0
        self._last_health_check: float = 0
        # Глобальный lock для сериализации запросов на CPU
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        """Инициализация: автоопределение OLOL proxy или прямого Ollama."""
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
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            headers={"Content-Type": "application/json"},
        )

        # Определяем модели
        self._detect_models()

        status = f"url={self._active_url}"
        vision = "+vision" if self._vision_available else "NO_VISION"
        logger.info(f"OllamaClusterProvider: {status} | text={self._text_model} | vision={self._vision_model} | {vision}")

    def _detect_models(self) -> None:
        """Определить доступные модели."""
        # Текстовая модель
        if self._is_model_installed(TEXT_FAST_MODEL):
            self._text_model = TEXT_FAST_MODEL
        elif self._is_model_installed(PRIMARY_MODEL):
            self._text_model = PRIMARY_MODEL
        else:
            self._text_model = PRIMARY_MODEL  # Попробуем всё равно

        # Vision модель
        for prefix in VISION_MODEL_PREFIXES:
            for installed in self._installed_models:
                if installed.startswith(prefix):
                    self._vision_available = True
                    self._vision_model = PRIMARY_MODEL
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
        """Проверка здоровья кластера/Ollama."""
        try:
            if not self._client:
                return False
            resp = await self._client.get("/api/tags", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False

    async def _warm_up(self) -> None:
        """Прогрев модели — загрузка в память."""
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
                timeout=httpx.Timeout(180.0, connect=10.0),
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
        """Удалить Qwen3 <think/> блоки из ответа."""
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
        """Генерация ответа через локальный кластер Ollama.

        Поддерживает:
        - Текстовые запросы (qwen3:1.7b быстрее, qwen3-vl:2b fallback)
        - Vision запросы с изображениями (qwen3-vl:2b)
        - Историю диалога через messages
        - Кэширование повторных запросов
        - Ретраи с задержкой
        """
        if not self._client:
            await self.init()

        # Прогрев при первом запросе
        if not self._warm:
            await self._warm_up()

        system_prompt = kwargs.get("system_prompt", "")
        temperature = kwargs.get("temperature", 0.7)
        max_tokens = kwargs.get("max_tokens", 2048)
        messages_history = kwargs.get("messages")
        # ВАЖНО: Извлекаем image_base64 из kwargs, НЕ передаём дальше как kwargs
        image_base64 = kwargs.pop("image_base64", None)

        # Ограничение истории для маленьких моделей
        if image_base64 and messages_history and len(messages_history) > 8:
            logger.info(f"Trimming history for vision: {len(messages_history)} -> 8")
            messages_history = messages_history[-8:]
        elif not image_base64 and messages_history and len(messages_history) > 20:
            messages_history = messages_history[-20:]

        # Выбор модели
        is_vision = bool(image_base64 and self._vision_available)
        if is_vision:
            model = self._vision_model or PRIMARY_MODEL
            models_to_try = [model]
        else:
            models_to_try = []
            if self._text_model and self._is_model_installed(self._text_model):
                models_to_try.append(self._text_model)
            if PRIMARY_MODEL not in models_to_try and self._is_model_installed(PRIMARY_MODEL):
                models_to_try.append(PRIMARY_MODEL)
            if not models_to_try:
                models_to_try = [self._text_model or PRIMARY_MODEL]

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

        async with self._semaphore:
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

                # Сериализация запросов (CPU может обрабатывать только один inference)
                request_timeout = self.timeout
                if img:
                    request_timeout = min(request_timeout * 1.5, 300)
                    logger.info(f"Extended timeout {request_timeout:.0f}s for vision request")

                async with self._lock:
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
                        last_error = ProviderError(self.name, f"Timeout for {try_model} (CPU inference slow)", retryable=True)
                        continue
                    except Exception as exc:
                        self._error_count += 1
                        last_error = ProviderError(self.name, f"Error with {try_model}: {exc}", retryable=True)
                        continue

        if last_error:
            raise last_error
        raise ProviderError(self.name, "All models failed", retryable=True)

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
                        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
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
        model = self._vision_model if use_vision else self._text_model or PRIMARY_MODEL

        ollama_messages = []
        for msg in messages[-10:]:
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
                "num_ctx": 8192,
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
                timeout=httpx.Timeout(self.timeout, connect=15.0),
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
        }
