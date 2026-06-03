"""LlamaCppProvider v3.0 — SINGLE-MODEL llama-cpp-python provider.

v41: SINGLE MODEL ARCHITECTURE!
  - Removed dual-model support (Qwen2.5-3B removed from project)
  - Only Qwen3-4B-Instruct remains as LOCAL FALLBACK
  - Pollinations.ai is now PRIMARY (cloud, fast, smart)
  - Qwen3-4B = offline/reserve when Pollinations is down
  - max_tokens=256 — decent response length for local fallback
  - n_ctx=2048, history=10 — optimized for speed
  - stop=["<think"] — BLOCKS Qwen3 thinking mode
  - /no_think prefix for Qwen models
  - asyncio.Semaphore(1) for serialized generation
  - asyncio.to_thread() for non-blocking generation
"""

import logging
import re
import time
import asyncio
from typing import Any, Dict, List, Optional

from ai.providers.base import AIResponse, BaseProvider, ProviderError

logger = logging.getLogger(__name__)

# Model loading defaults
DEFAULT_MODEL_CONFIG = {
    "n_ctx": 2048,
    "n_threads": 4,
    "n_gpu_layers": 0,
    "verbose": False,
    "use_mmap": True,
    "use_mlock": False,
    "rope_scaling_type": 0,
    "rope_freq_base": 0.0,
}

# Generation defaults
DEFAULT_GEN_CONFIG = {
    "max_tokens": 256,       # Decent length for local fallback
    "temperature": 0.82,
    "top_p": 0.92,
    "top_k": 50,
    "repeat_penalty": 1.12,
    "frequency_penalty": 0.0,
    "presence_penalty": 0.0,
    "stop": ["<think", "<|im_end|>"],  # Block thinking mode
}


class LlamaCppProvider(BaseProvider):
    """Single-model llama-cpp-python provider.

    Qwen3-4B-Instruct as LOCAL FALLBACK when Pollinations is unavailable.
    Only ONE model loaded at a time — minimal RAM usage.
    """

    name: str = "llama_cpp"
    supports_streaming: bool = False
    supports_vision: bool = False

    def __init__(
        self,
        model_path: str = "",
        timeout: float = 65.0,
        model_config: Optional[Dict] = None,
        gen_config: Optional[Dict] = None,
    ):
        super().__init__(api_key="", timeout=timeout)
        self.model_path = model_path
        self.model_config = {**DEFAULT_MODEL_CONFIG, **(model_config or {})}
        self.gen_config = {**DEFAULT_GEN_CONFIG, **(gen_config or {})}
        self._llm = None
        self._semaphore = asyncio.Semaphore(1)
        self._loaded = False
        self._load_time = 0.0
        self._model_name = ""
        # Stats
        self._request_count = 0
        self._error_count = 0
        self._total_gen_time = 0.0

    async def init(self) -> None:
        """Load the GGUF model into memory."""
        if self._loaded and self._llm:
            logger.info("LlamaCppProvider: model already loaded, skipping")
            return

        if not self.model_path:
            raise ProviderError(self.name, "model_path not specified", retryable=False)

        try:
            from llama_cpp import Llama
        except ImportError:
            raise ProviderError(
                self.name,
                "llama-cpp-python not installed! Install with: "
                "CMAKE_ARGS='-DGGML_AVX2=on' pip install llama-cpp-python",
                retryable=False,
            )

        model_name = self.model_path.split("/")[-1]
        logger.info(f"LlamaCppProvider: loading model: {model_name}...")
        start = time.time()

        try:
            self._llm = await asyncio.to_thread(
                Llama,
                model_path=self.model_path,
                **self.model_config,
            )
            self._load_time = time.time() - start
            self._loaded = True
            self._model_name = model_name

            logger.info(
                f"LlamaCppProvider: model '{model_name}' loaded in {self._load_time:.1f}s "
                f"(n_ctx={self.model_config['n_ctx']}, n_threads={self.model_config['n_threads']})"
            )

            # Warm up
            await self._warm_up()

        except Exception as e:
            logger.error(f"LlamaCppProvider: failed to load model: {e}")
            self._llm = None
            self._loaded = False
            raise ProviderError(self.name, f"Failed to load model: {e}", retryable=False)

    async def _warm_up(self) -> None:
        """Warm up model — first request is always slower."""
        if not self._llm:
            return

        logger.info("LlamaCppProvider: warming up model...")
        start = time.time()
        try:
            warmup_msg = "/no_think\nПривет, как дела?"
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

    async def close(self) -> None:
        """Unload model from memory."""
        if self._llm:
            try:
                del self._llm
            except Exception:
                pass
            self._llm = None
            self._loaded = False
            logger.info("LlamaCppProvider: model unloaded")

    def is_available(self) -> bool:
        return self._loaded and self._llm is not None

    async def health_check(self) -> bool:
        return self._loaded and self._llm is not None

    async def generate(self, prompt: str, **kwargs) -> AIResponse:
        """Generate response via llama-cpp-python.

        Uses asyncio.to_thread() to not block event loop.
        Semaphore ensures only one request at a time.
        """
        if not self._llm:
            raise ProviderError(self.name, "Model not loaded", retryable=True)

        system_prompt = kwargs.get("system_prompt", "")
        temperature = kwargs.get("temperature", self.gen_config["temperature"])
        max_tokens = kwargs.get("max_tokens", self.gen_config["max_tokens"])
        messages_history = kwargs.get("messages")
        history_limit = kwargs.get("history_limit", 10)

        # Limit history
        if messages_history and len(messages_history) > history_limit:
            messages_history = messages_history[-history_limit:]

        messages = self._build_messages(prompt, system_prompt, messages_history)

        # For Qwen3: add /no_think prefix to disable thinking mode
        if messages and messages[-1].get("role") == "user":
            content = messages[-1]["content"]
            if not content.startswith("/no_think"):
                messages[-1]["content"] = f"/no_think\n{content}"

        async with self._semaphore:
            self._request_count += 1
            start = time.time()

            try:
                stop_sequences = self.gen_config.get("stop", [])

                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        self._llm.create_chat_completion,
                        messages=messages,
                        max_tokens=min(max_tokens, self.gen_config["max_tokens"]),
                        temperature=temperature,
                        top_p=self.gen_config["top_p"],
                        top_k=self.gen_config["top_k"],
                        repeat_penalty=self.gen_config["repeat_penalty"],
                        stop=stop_sequences if stop_sequences else None,
                    ),
                    timeout=self.timeout,
                )

                elapsed = time.time() - start
                self._total_gen_time += elapsed

                # Extract response text
                text = ""
                if isinstance(response, dict):
                    choices = response.get("choices", [])
                    if choices:
                        msg = choices[0].get("message", {})
                        text = msg.get("content", "")

                # Strip think tags
                text = self._strip_think_tags(text)
                text = re.sub(r'<think\b[^>]*$', '', text, flags=re.IGNORECASE)

                if not text or not text.strip():
                    raise ProviderError(self.name, "Empty response from model", retryable=True)

                tokens_used = 0
                usage = response.get("usage", {})
                if usage:
                    tokens_used = usage.get("total_tokens", 0)

                logger.info(
                    f"LlamaCppProvider: generated in {elapsed:.1f}s, "
                    f"tokens={tokens_used}, len={len(text)}"
                )

                return AIResponse(
                    text=text.strip(),
                    provider=self.name,
                    model=self._model_name,
                    tokens_used=tokens_used,
                    metadata={
                        "local": True,
                        "gen_time": elapsed,
                        "backend": "llama-cpp-python",
                    },
                )

            except asyncio.TimeoutError:
                self._error_count += 1
                raise ProviderError(
                    self.name,
                    f"Generation timed out ({self.timeout}s)",
                    retryable=True,
                )
            except ProviderError:
                raise
            except Exception as e:
                self._error_count += 1
                raise ProviderError(self.name, f"Generation error: {e}", retryable=True)

    @staticmethod
    def _build_messages(prompt: str, system_prompt: str, messages_history: Optional[List[Dict]]) -> List[Dict]:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if messages_history:
            for msg in messages_history:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": prompt})
        return messages

    @staticmethod
    def _strip_think_tags(text: str) -> str:
        if not text:
            return ""
        text = re.sub(r'<think\b[^>]*>.*?</think\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<thinking\b[^>]*>.*?</thinking\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'</?think[^>]*>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'</?thinking[^>]*>', '', text, flags=re.IGNORECASE)
        return text.strip()

    def get_stats(self) -> Dict[str, Any]:
        avg_gen_time = (
            self._total_gen_time / self._request_count
            if self._request_count > 0
            else 0
        )
        return {
            "model_loaded": self._loaded,
            "model_name": self._model_name,
            "load_time": self._load_time,
            "request_count": self._request_count,
            "error_count": self._error_count,
            "avg_gen_time": avg_gen_time,
            "n_ctx": self.model_config.get("n_ctx", 0),
            "n_threads": self.model_config.get("n_threads", 0),
        }
