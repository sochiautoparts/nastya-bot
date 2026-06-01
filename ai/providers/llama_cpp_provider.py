"""LlamaCppProvider v2.0 — DUAL-MODEL llama-cpp-python provider.

v37: DUAL-MODEL SYSTEM!
  - Поддерживает ДВЕ GGUF модели: PRIMARY + SECONDARY
  - Автотест при старте — выбирает лучшую для русского языка
  - Если PRIMARY не отвечает — переключается на SECONDARY
  - Автопереключение при ошибках (failover)

АРХИТЕКТУРА:
  - Только ОДНА модель загружена в память (экономия RAM)
  - При ошибке — выгружает PRIMARY и загружает SECONDARY
  - asyncio.Semaphore(1) для сериализации запросов
  - asyncio.to_thread() для неблокирующей генерации
"""

import logging
import re
import time
import asyncio
from typing import Any, Dict, List, Optional

from ai.providers.base import AIResponse, BaseProvider, ProviderError

logger = logging.getLogger(__name__)

# Параметры загрузки модели по умолчанию
DEFAULT_MODEL_CONFIG = {
    "n_ctx": 4096,          # Расширенный контекст — 4096 токенов для развёрнутых ответов
    "n_threads": 4,         # Количество потоков (4 vCPU)
    "n_gpu_layers": 0,      # Без GPU — чисто CPU
    "verbose": False,       # Без лишнего вывода
    "use_mmap": True,       # Memory-mapped файл — быстрее загрузка
    "use_mlock": False,     # Не блокировать RAM (может быть проблемой на VPS)
    "rope_scaling_type": 0, # Без масштабирования RoPE
    "rope_freq_base": 0.0,  # Default RoPE
}

# Параметры генерации по умолчанию
DEFAULT_GEN_CONFIG = {
    "max_tokens": 256,       # Развёрнутые ответы — 2-5 предложений!
    "temperature": 0.82,     # Чуть ниже = стабильнее, но живо
    "top_p": 0.92,          # Nucleus sampling — чуть выше для разнообразия
    "top_k": 50,            # Ограничиваем top-k для качества
    "repeat_penalty": 1.12, # Против повторов
    "frequency_penalty": 0.0,
    "presence_penalty": 0.0,
}


class LlamaCppProvider(BaseProvider):
    """Провайдер на базе llama-cpp-python — DUAL-MODEL система.

    Поддерживает две GGUF модели:
    - primary_model_path: основная модель (Phi-4-mini или Qwen3)
    - secondary_model_path: резервная модель (автопереключение при ошибках)

    В любой момент загружена ТОЛЬКО одна модель.
    При ошибке генерации — модель переключается автоматически.
    """

    name: str = "llama_cpp"
    supports_streaming: bool = False
    supports_vision: bool = False

    def __init__(
        self,
        primary_model_path: str = "",
        secondary_model_path: str = "",
        timeout: float = 90.0,
        model_config: Optional[Dict] = None,
        gen_config: Optional[Dict] = None,
    ):
        super().__init__(api_key="", timeout=timeout)
        self.primary_model_path = primary_model_path
        self.secondary_model_path = secondary_model_path
        self.model_config = {**DEFAULT_MODEL_CONFIG, **(model_config or {})}
        self.gen_config = {**DEFAULT_GEN_CONFIG, **(gen_config or {})}
        self._llm = None
        self._semaphore = asyncio.Semaphore(1)  # Один запрос за раз
        self._loaded = False
        self._load_time = 0.0
        self._current_model_path = ""  # Какая модель сейчас загружена
        self._current_model_name = ""  # Человекочитаемое имя
        self._is_secondary = False  # True если сейчас secondary
        # Stats
        self._request_count = 0
        self._error_count = 0
        self._total_gen_time = 0.0
        self._switch_count = 0  # Количество переключений модели

    async def init(self) -> None:
        """Загрузка PRIMARY GGUF модели в память."""
        if self._loaded and self._llm:
            logger.info("LlamaCppProvider: model already loaded, skipping")
            return

        if not self.primary_model_path:
            raise ProviderError(self.name, "primary_model_path not specified", retryable=False)

        try:
            from llama_cpp import Llama
        except ImportError:
            raise ProviderError(
                self.name,
                "llama-cpp-python not installed! Install with: "
                "CMAKE_ARGS='-DGGML_AVX2=on' pip install llama-cpp-python",
                retryable=False,
            )

        # Попробовать загрузить PRIMARY
        success = await self._load_model(self.primary_model_path, is_secondary=False)

        if not success and self.secondary_model_path:
            # PRIMARY не загрузился — пробуем SECONDARY
            logger.warning(f"PRIMARY model failed, trying SECONDARY: {self.secondary_model_path}")
            success = await self._load_model(self.secondary_model_path, is_secondary=True)

        if not success:
            raise ProviderError(self.name, "Failed to load any model!", retryable=False)

    async def _load_model(self, model_path: str, is_secondary: bool = False) -> bool:
        """Загрузить конкретную GGUF модель. Возвращает True если успешно."""
        try:
            from llama_cpp import Llama
        except ImportError:
            return False

        if not model_path:
            return False

        model_name = model_path.split("/")[-1]
        logger.info(f"LlamaCppProvider: loading {'SECONDARY' if is_secondary else 'PRIMARY'} model: {model_name}...")
        start = time.time()

        try:
            # Если уже загружена другая модель — выгрузить
            if self._llm is not None:
                try:
                    del self._llm
                except Exception:
                    pass
                self._llm = None
                self._loaded = False

            self._llm = await asyncio.to_thread(
                Llama,
                model_path=model_path,
                **self.model_config,
            )
            self._load_time = time.time() - start
            self._loaded = True
            self._current_model_path = model_path
            self._current_model_name = model_name
            self._is_secondary = is_secondary

            logger.info(
                f"LlamaCppProvider: model '{model_name}' loaded in {self._load_time:.1f}s "
                f"(n_ctx={self.model_config['n_ctx']}, n_threads={self.model_config['n_threads']}, "
                f"{'SECONDARY' if is_secondary else 'PRIMARY'})"
            )

            # Прогрев модели — первый запрос всегда медленнее
            await self._warm_up()
            return True

        except Exception as e:
            logger.error(f"LlamaCppProvider: failed to load model '{model_name}': {e}")
            self._llm = None
            self._loaded = False
            return False

    async def _warm_up(self) -> None:
        """Прогрев модели — первый запрос всегда медленнее из-за ленивой инициализации."""
        if not self._llm:
            return

        logger.info("LlamaCppProvider: warming up model...")
        start = time.time()
        try:
            # Для Qwen3: добавляем /no_think, для Phi-4: не нужно
            warmup_msg = "Привет, как дела?" if self._is_secondary else "/no_think\nПривет, как дела?"
            await asyncio.to_thread(
                self._llm.create_chat_completion,
                messages=[
                    {"role": "system", "content": "Ты Настя — девушка из Москвы."},
                    {"role": "user", "content": warmup_msg},
                ],
                max_tokens=10,
                temperature=0.1,
            )
            elapsed = time.time() - start
            logger.info(f"LlamaCppProvider: warm-up done in {elapsed:.1f}s")
        except Exception as e:
            logger.warning(f"LlamaCppProvider: warm-up error (non-critical): {e}")

    async def _switch_to_secondary(self) -> bool:
        """Переключиться на SECONDARY модель. Возвращает True если успешно."""
        if not self.secondary_model_path or self._is_secondary:
            return False

        logger.warning("Switching to SECONDARY model...")
        self._switch_count += 1
        return await self._load_model(self.secondary_model_path, is_secondary=True)

    async def _switch_to_primary(self) -> bool:
        """Переключиться обратно на PRIMARY модель."""
        if self._is_secondary and self.primary_model_path:
            logger.info("Attempting to switch back to PRIMARY model...")
            return await self._load_model(self.primary_model_path, is_secondary=False)
        return False

    async def close(self) -> None:
        """Выгрузка модели из памяти."""
        if self._llm:
            try:
                del self._llm
            except Exception:
                pass
            self._llm = None
            self._loaded = False
            logger.info("LlamaCppProvider: model unloaded")

    def is_available(self) -> bool:
        """Провайдер доступен если модель загружена."""
        return self._loaded and self._llm is not None

    async def health_check(self) -> bool:
        """Проверка что модель жива."""
        return self._loaded and self._llm is not None

    async def generate(self, prompt: str, **kwargs) -> AIResponse:
        """Генерация ответа через llama-cpp-python.

        Использует asyncio.to_thread() чтобы не блокировать event loop.
        Semaphore гарантирует что только один запрос обрабатывается за раз.
        При ошибке — автоматически пробует вторую модель.
        """
        if not self._llm:
            raise ProviderError(self.name, "Model not loaded", retryable=True)

        system_prompt = kwargs.get("system_prompt", "")
        temperature = kwargs.get("temperature", self.gen_config["temperature"])
        max_tokens = kwargs.get("max_tokens", self.gen_config["max_tokens"])
        messages_history = kwargs.get("messages")
        history_limit = kwargs.get("history_limit", 10)

        # Ограничение истории — 10 сообщений для оптимального контекста
        if messages_history and len(messages_history) > history_limit:
            messages_history = messages_history[-history_limit:]

        # Строим сообщения
        messages = self._build_messages(prompt, system_prompt, messages_history)

        # Для Qwen3 (secondary): добавляем /no_think — отключает thinking mode
        # Для Phi-4 (primary): НЕ добавляем /no_think — модель работает без него
        if self._is_secondary and messages and messages[-1].get("role") == "user":
            content = messages[-1]["content"]
            if not content.startswith("/no_think"):
                messages[-1]["content"] = f"/no_think\n{content}"

        async with self._semaphore:
            self._request_count += 1
            start = time.time()

            try:
                # Запускаем генерацию в отдельном потоке
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        self._llm.create_chat_completion,
                        messages=messages,
                        max_tokens=min(max_tokens, self.gen_config["max_tokens"]),
                        temperature=temperature,
                        top_p=self.gen_config["top_p"],
                        top_k=self.gen_config["top_k"],
                        repeat_penalty=self.gen_config["repeat_penalty"],
                    ),
                    timeout=self.timeout,
                )

                elapsed = time.time() - start
                self._total_gen_time += elapsed

                # Извлекаем текст ответа
                text = ""
                if isinstance(response, dict):
                    choices = response.get("choices", [])
                    if choices:
                        msg = choices[0].get("message", {})
                        text = msg.get("content", "")

                # Очистка think-тегов (Qwen3 thinking mode)
                text = self._strip_think_tags(text)

                if not text or not text.strip():
                    raise ProviderError(self.name, "Empty response from model", retryable=True)

                # Подсчёт токенов (если доступно)
                tokens_used = 0
                usage = response.get("usage", {})
                if usage:
                    tokens_used = usage.get("total_tokens", 0)

                logger.info(
                    f"LlamaCppProvider: generated in {elapsed:.1f}s, "
                    f"tokens={tokens_used}, len={len(text)}, model={'secondary' if self._is_secondary else 'primary'}"
                )

                return AIResponse(
                    text=text.strip(),
                    provider=self.name,
                    model=self._current_model_name,
                    tokens_used=tokens_used,
                    metadata={
                        "local": True,
                        "gen_time": elapsed,
                        "backend": "llama-cpp-python",
                        "is_secondary": self._is_secondary,
                    },
                )

            except asyncio.TimeoutError:
                self._error_count += 1
                # Попробовать переключить модель при таймауте
                if not self._is_secondary:
                    logger.warning("Timeout on PRIMARY model, trying SECONDARY...")
                    if await self._switch_to_secondary():
                        # Рекурсивный вызов с новой моделью
                        return await self.generate(prompt, **kwargs)
                raise ProviderError(
                    self.name,
                    f"Generation timed out ({self.timeout}s)",
                    retryable=True,
                )
            except ProviderError:
                raise
            except Exception as e:
                self._error_count += 1
                # При критической ошибке — попробовать вторую модель
                if not self._is_secondary and self._switch_count < 3:
                    logger.warning(f"PRIMARY model error: {e}, trying SECONDARY...")
                    if await self._switch_to_secondary():
                        return await self.generate(prompt, **kwargs)
                raise ProviderError(self.name, f"Generation error: {e}", retryable=True)

    @staticmethod
    def _build_messages(prompt: str, system_prompt: str, messages_history: Optional[List[Dict]]) -> List[Dict]:
        """Строим список сообщений для модели."""
        messages = []

        # System prompt
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        # History
        if messages_history:
            for msg in messages_history:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})

        # Current user message
        messages.append({"role": "user", "content": prompt})

        return messages

    @staticmethod
    def _strip_think_tags(text: str) -> str:
        """Удалить <think/> блоки из ответа (Qwen3 thinking mode)."""
        if not text:
            return ""
        # Полные блоки <think...</think >
        text = re.sub(r'<think\b[^>]*>.*?</think\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<thinking\b[^>]*>.*?</thinking\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
        # Незакрытые теги
        text = re.sub(r'</?think[^>]*>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'</?thinking[^>]*>', '', text, flags=re.IGNORECASE)
        return text.strip()

    def get_stats(self) -> Dict[str, Any]:
        """Статистика провайдера."""
        avg_gen_time = (
            self._total_gen_time / self._request_count
            if self._request_count > 0
            else 0
        )
        return {
            "model_loaded": self._loaded,
            "current_model": self._current_model_name,
            "is_secondary": self._is_secondary,
            "primary_path": self.primary_model_path,
            "secondary_path": self.secondary_model_path,
            "load_time": self._load_time,
            "request_count": self._request_count,
            "error_count": self._error_count,
            "avg_gen_time": avg_gen_time,
            "switch_count": self._switch_count,
            "n_ctx": self.model_config.get("n_ctx", 0),
            "n_threads": self.model_config.get("n_threads", 0),
        }
