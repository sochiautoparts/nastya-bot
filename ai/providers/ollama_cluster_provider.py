"""OllamaClusterProvider v35.0 - SMART MODEL AUTO-DETECTION + RSS-first.

КЛЮЧЕВЫЕ ИЗМЕНЕНИЯ v35:
  - Новости теперь через RSS-парсер + шаблоны, БЕЗ AI!
  - Ollama свободен для чата - не тратит время на news commentary
  - Уменьшен num_ctx до 1536 - быстрее на CPU
  - Уменьшен num_predict до 100-150 - Настины ответы короткие
  - Добавлены top_p и repeat_penalty против повторов
  - THINKING OFF для всех моделей - экономия токенов
  - vikhr-1B полностью игнорируется (даже если установлен)

МОДЕЛИ (по приоритету):
  1. qwen2.5:1.5b - быстрый, отличный русский (0.9GB)
  2. qwen3:4b-instruct - умный, медленнее, thinking mode (2.5GB)
  3. НЕ ИСПОЛЬЗУЕМ: vikhr-1B (генерирует бред на русском)
"""

import logging
import asyncio
import re
import time
from typing import Any, Dict, List, Optional

import httpx

from ai.providers.base import AIResponse, BaseProvider, ProviderError

logger = logging.getLogger(__name__)

# ── Приоритет моделей (лучшая -> худшая для русского на CPU) ──
# vikhr-1B исключён - генерирует бред
MODEL_PRIORITY = [
    "qwen2.5:1.5b",          # Лучший баланс: быстрый + хороший русский
    "qwen3:4b-instruct",      # Умнее, медленнее, thinking mode
    "qwen2.5:3b",             # Средний вариант
    "qwen2.5:0.5b",           # Самый быстрый, но слабый
    "phi4-mini",              # Хороший английский, средний русский
    "llama3.2:3b",            # Средний
    "gemma2:2b",              # Средний
]

# Модели, которые НИКОГДА не используем (плохой русский)
BANNED_MODELS = [
    "vikhr",  # Все vikhr модели генерируют бред на русском
]

# Параметры для каждой модели (индивидуальные!)
MODEL_CONFIGS = {
    "qwen2.5:1.5b": {
        "num_predict": 100,      # Короткие ответы Насти - 1-2 предложения
        "num_ctx": 1536,         # Меньше контекст = быстрее генерация
        "timeout": 20.0,         # Быстрая модель - 20с хватит
        "think": False,          # Нет thinking mode
        "temperature": 0.85,     # Чуть выше = разнообразнее
        "top_p": 0.9,           # Nucleus sampling для качества
        "repeat_penalty": 1.1,   # Чтобы не повторялась
    },
    "qwen3:4b-instruct": {
        "num_predict": 150,      # Отключён thinking - можно меньше
        "num_ctx": 1536,         # Меньше = быстрее на CPU
        "timeout": 45.0,         # 4B на CPU - медленнее
        "think": False,          # ВЫКЛЮЧАЕМ thinking - он жрёт токены!
        "temperature": 0.8,
        "top_p": 0.9,
        "repeat_penalty": 1.1,
    },
    "qwen2.5:3b": {
        "num_predict": 120,
        "num_ctx": 1536,
        "timeout": 35.0,
        "think": False,
        "temperature": 0.8,
        "top_p": 0.9,
        "repeat_penalty": 1.1,
    },
    "default": {
        "num_predict": 100,
        "num_ctx": 1536,
        "timeout": 35.0,
        "think": False,
        "temperature": 0.85,
        "top_p": 0.9,
        "repeat_penalty": 1.1,
    },
}

# Request priorities
PRIORITY_HIGH = "high"    # User chat
PRIORITY_LOW = "low"      # Background tasks (news, channel)


def _get_model_config(model_name: str) -> dict:
    """Получить конфигурацию для модели."""
    # Сначала точное совпадение
    if model_name in MODEL_CONFIGS:
        return MODEL_CONFIGS[model_name]
    # Частичное совпадение (например, qwen3:4b-instruct содержит qwen3)
    for key in MODEL_CONFIGS:
        if key in model_name:
            return MODEL_CONFIGS[key]
    return MODEL_CONFIGS["default"]


def _is_model_banned(model_name: str) -> bool:
    """Проверить, запрещена ли модель."""
    name_lower = model_name.lower()
    return any(banned in name_lower for banned in BANNED_MODELS)


class OllamaClusterProvider(BaseProvider):
    """Провайдер для Ollama - v34.0 SMART AUTO-DETECTION.

    v34 КЛЮЧЕВЫЕ ИЗМЕНЕНИЯ:
    - Автоопределение лучшей модели из установленных
    - Индивидуальные параметры для каждой модели
    - Правильная работа с Qwen3 thinking mode
    - Запрещённые модели (vikhr) игнорируются
    - Раздельные семафоры: чат + фон
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
        self._chat_sem = asyncio.Semaphore(1)
        self._bg_sem = asyncio.Semaphore(1)
        # Stats
        self._request_count: int = 0
        self._error_count: int = 0
        self._primary_requests: int = 0
        self._reserve_requests: int = 0
        self._last_health_check: float = 0
        self._last_health_status: bool = True
        self._HEALTH_CACHE_TTL: int = 300
        # Pollinations fallback
        self._pollinations_fallback = pollinations_fallback
        # v33: Pollinations 429 кулдаун
        self._pollinations_429_until: float = 0
        self._POLLINATIONS_429_COOLDOWN: float = 300.0

    async def init(self) -> None:
        """Инициализация: автоопределение Ollama и моделей."""
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
            logger.warning("Ollama not available at startup - will try later")

        if not self._active_url:
            self._active_url = self.ollama_url
            logger.warning(f"Defaulting to {self._active_url} - may not be available yet")

        # Создаём постоянный HTTP клиент
        self._client = httpx.AsyncClient(
            base_url=self._active_url,
            timeout=httpx.Timeout(self.timeout, connect=10.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            headers={"Content-Type": "application/json"},
        )

        # Определяем ЛУЧШИЕ модели из установленных
        self._detect_models()

        logger.info(
            f"OllamaClusterProvider v34.0: url={self._active_url} | "
            f"primary={self._primary_model} | reserve={self._reserve_model}"
        )

    def _detect_models(self) -> None:
        """Умное определение моделей: выбираем лучшие из УСТАНОВЛЕННЫХ.

        Логика:
          1. Фильтруем запрещённые модели (vikhr)
          2. Ищем лучшую модель по приоритету -> primary
          3. Ищем вторую лучшую -> reserve
          4. Если ничего нет - используем defaults
        """
        # Фильтруем запрещённые модели
        good_models = [m for m in self._installed_models if not _is_model_banned(m)]
        if len(good_models) < len(self._installed_models):
            banned = [m for m in self._installed_models if _is_model_banned(m)]
            logger.warning(f"Banned models (ignored): {banned}")

        # Ищем лучшую модель по приоритету
        primary_found = None
        reserve_found = None

        for candidate in MODEL_PRIORITY:
            if primary_found and reserve_found:
                break
            for installed in good_models:
                # Проверяем совпадение: "qwen2.5:1.5b" должно совпадать с
                # "qwen2.5:1.5b" или "qwen2.5:1.5b-instruct" и т.д.
                candidate_prefix = candidate.split(":")[0]  # e.g. "qwen2.5"
                installed_prefix = installed.split(":")[0]  # e.g. "qwen2.5"

                if candidate in installed or (candidate_prefix in installed and candidate.split(":")[-1] in installed):
                    if not primary_found:
                        primary_found = installed
                    elif not reserve_found and installed != primary_found:
                        reserve_found = installed
                    break

        # Если не нашли через приоритет - берём любые доступные
        if not primary_found and good_models:
            primary_found = good_models[0]
        if not reserve_found and len(good_models) > 1:
            # Берём вторую модель (не primary)
            for m in good_models:
                if m != primary_found:
                    reserve_found = m
                    break

        # Устанавливаем модели
        self._primary_model = primary_found
        self._reserve_model = reserve_found

        if not self._primary_model:
            logger.error(
                "NO suitable Ollama model found! Need at least qwen2.5:1.5b or qwen3:4b-instruct. "
                f"Installed: {self._installed_models}"
            )
        else:
            config = _get_model_config(self._primary_model)
            logger.info(
                f"Primary model: {self._primary_model} "
                f"(num_predict={config['num_predict']}, timeout={config['timeout']}s, think={config['think']})"
            )

        if self._reserve_model:
            config = _get_model_config(self._reserve_model)
            logger.info(
                f"Reserve model: {self._reserve_model} "
                f"(num_predict={config['num_predict']}, timeout={config['timeout']}s, think={config['think']})"
            )
        else:
            logger.warning("No reserve model available!")

    def is_available(self) -> bool:
        """Всегда пробуем - ошибки обрабатываются в generate()."""
        return True

    async def health_check(self) -> bool:
        """Проверка здоровья Ollama - с кэшированием."""
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
        """Прогрев основной модели - загрузка в память."""
        if self._warm or not self._primary_model:
            return
        try:
            config = _get_model_config(self._primary_model)
            logger.info(f"Warming up primary model: {self._primary_model}...")
            resp = await self._client.post(
                "/api/chat",
                json={
                    "model": self._primary_model,
                    "messages": [{"role": "user", "content": "привет"}],
                    "stream": False,
                    "options": {"num_predict": 5, "num_ctx": config["num_ctx"]},
                    "think": config["think"],
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
        # Очистка полных блоков <think...</think >
        text = re.sub(r'<think\b[^>]*>.*?</think\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<thinking\b[^>]*>.*?</thinking\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
        # Очистка незакрытых тегов (Qwen3 иногда не закрывает <think >)
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

        v34: Умная маршрутизация по моделям с индивидуальными параметрами.
        Раздельные семафоры для чата и фона.
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

        # Ограничение истории - 4 сообщения для скорости
        if messages_history and len(messages_history) > 4:
            logger.info(f"Trimming history: {len(messages_history)} -> 4")
            messages_history = messages_history[-4:]

        # Выбираем семафор по приоритету
        sem = self._chat_sem if priority == PRIORITY_HIGH else self._bg_sem

        async with sem:
            # Строим список моделей для попыток
            models_to_try = []
            if self._primary_model:
                config = _get_model_config(self._primary_model)
                models_to_try.append((self._primary_model, config, "primary"))
            if self._reserve_model and self._reserve_model != self._primary_model:
                config = _get_model_config(self._reserve_model)
                models_to_try.append((self._reserve_model, config, "reserve"))

            last_error = None
            for model, config, model_type in models_to_try:
                ollama_messages = self._build_messages(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    messages=messages_history,
                )

                # v35: Индивидуальные параметры для каждой модели!
                options = {
                    "temperature": temperature,
                    "num_predict": min(max_tokens, config["num_predict"]),
                    "num_ctx": config["num_ctx"],
                    "top_p": config.get("top_p", 0.9),
                    "repeat_penalty": config.get("repeat_penalty", 1.1),
                }
                payload = {
                    "model": model,
                    "messages": ollama_messages,
                    "stream": False,
                    "options": options,
                    "think": config["think"],  # False для скорости
                }

                try:
                    self._request_count += 1
                    if model_type == "primary":
                        self._primary_requests += 1
                    else:
                        self._reserve_requests += 1

                    logger.info(
                        f"OllamaCluster: TEXT request -> {model} "
                        f"({model_type}, timeout={config['timeout']}s, "
                        f"num_predict={options['num_predict']}, think={config['think']}, "
                        f"priority={priority})"
                    )

                    response = await self._client.post(
                        "/api/chat",
                        json=payload,
                        timeout=httpx.Timeout(config["timeout"], connect=10.0),
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
                        f"TIMEOUT for {model} ({config['timeout']}s, {model_type}). "
                        f"Trying next model..."
                    )
                    last_error = ProviderError(
                        self.name,
                        f"Timeout for {model} ({config['timeout']}s)",
                        retryable=True,
                    )
                    continue
                except Exception as exc:
                    self._error_count += 1
                    last_error = ProviderError(self.name, f"Error with {model}: {exc}", retryable=True)
                    continue

        if last_error:
            raise last_error
        raise ProviderError(self.name, "All models failed", retryable=True)

    def set_pollinations_429_cooldown(self, until: float) -> None:
        """Установить кулдаун Pollinations после 429."""
        self._pollinations_429_until = until

    def is_pollinations_on_cooldown(self) -> bool:
        """Проверить, на кулдауне ли Pollinations."""
        return time.time() < self._pollinations_429_until

    async def generate_with_context(self, messages: List[Dict[str, str]],
                                     image: Optional[str] = None,
                                     video: Optional[str] = None) -> str:
        """Генерация с учётом истории диалога (для совместимости).

        v34: Использует автоопределённую модель с правильными параметрами.
        """
        if not messages:
            return "Привет! О чём хочешь поболтать?"

        model = self._primary_model or "qwen3:4b-instruct"
        config = _get_model_config(model)

        ollama_messages = []
        for msg in messages[-4:]:
            ollama_messages.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", ""),
            })

        payload = {
            "model": model,
            "messages": ollama_messages,
            "stream": False,
            "think": config["think"],
            "options": {
                "temperature": config["temperature"],
                "num_ctx": config["num_ctx"],
                "num_predict": config["num_predict"],
            },
        }

        try:
            async with self._client.post(
                "/api/chat",
                json=payload,
                timeout=httpx.Timeout(config["timeout"], connect=10.0),
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
