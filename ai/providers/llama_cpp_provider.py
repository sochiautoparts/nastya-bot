"""LlamaCppProvider v6.0 - LOCAL-FIRST with AUTO-DOWNLOAD + HF_TOKEN!

v6.0 LOCAL-FIRST UPGRADE:
  - AUTO-DOWNLOAD: Model downloads from HuggingFace if not found locally
  - HF_TOKEN support: Authenticated download for gated models (Qwen3-4B)
  - 3 download methods: huggingface_hub -> urllib with Bearer -> unauthenticated urllib
  - n_ctx=4096, n_batch=512 (faster batch processing)
  - max_tokens=512 (was 300 - fuller responses for chat/comments)
  - Smart history: up to 6 messages (was 4 - more context with 4096 ctx)
  - System prompt: up to 800 chars (was 600 - more personality context)
  - Each message: up to 400 chars
  - User message: up to 1500 chars
  - Total chars safety limit: 10000 (was 8000 - more room with 4096 ctx)
  - timeout=60.0 (was 45.0 - larger model needs more time)
  - /no_think prefix for Qwen models
  - stop=["<think", "<|im_end|>"] - BLOCKS Qwen3 thinking mode
  - asyncio.Semaphore(1) for serialized generation
  - asyncio.to_thread() for non-blocking generation
  - Circuit breaker: 5 consecutive errors -> 2 min cooldown
"""

import logging
import os
import re
import time
import asyncio
from typing import Any, Dict, List, Optional

from ai.providers.base import AIResponse, BaseProvider, ProviderError
from bot.config import ENABLE_LOCAL_MODEL, MODEL_PATH, MODEL_N_CTX, MODEL_N_THREADS, MODEL_MAX_TOKENS

logger = logging.getLogger(__name__)

# Model loading defaults
DEFAULT_MODEL_CONFIG = {
    "n_ctx": 4096,       # 4096 for Qwen3-4B Q4 - plenty of room
    "n_batch": 512,      # Faster batch processing
    "n_threads": 4,
    "n_gpu_layers": 0,   # CPU only — GitHub Actions has no GPU
    "verbose": False,
    "use_mmap": True,    # Memory-mapped file — faster loading
    "use_mlock": False,  # Don't lock memory — saves RAM
}

# Generation defaults
DEFAULT_GEN_CONFIG = {
    "max_tokens": 512,       # Fuller responses (was 300)
    "temperature": 0.82,
    "top_p": 0.92,
    "top_k": 50,
    "repeat_penalty": 1.12,
    "frequency_penalty": 0.0,
    "presence_penalty": 0.0,
    "stop": ["<think", "<|im_end|>"],  # Block thinking mode
}

# ── Context window limits for local model ──
# Qwen3-4B with n_ctx=4096 - much more room!
# Rough estimate: 1 token ≈ 3-4 chars for Russian text
LOCAL_MAX_SYSTEM_CHARS = 800    # v6: Was 600 - more personality context
LOCAL_MAX_HISTORY_MSGS = 6     # v6: Was 4 - more context with 4096 ctx
LOCAL_MAX_MSG_CHARS = 400      # v6: Was 300 - longer messages
LOCAL_MAX_USER_CHARS = 1500    # v6: Was 1200 - longer user messages
LOCAL_MAX_TOTAL_CHARS = 10000  # v6: Was 8000 - more room with 4096 ctx (~2500 tokens)

# HuggingFace model download URL
MODEL_DOWNLOAD_URL = "https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf"
# Correct model filename on HuggingFace
CORRECT_MODEL_FILENAME = "Qwen3-4B-Q4_K_M.gguf"


class LlamaCppProvider(BaseProvider):
    """Single-model llama-cpp-python provider with auto-download.

    Qwen3-4B-Instruct as LOCAL-FIRST for chat/comments,
    fallback for function routes.
    Auto-downloads model from HuggingFace with HF_TOKEN auth.
    """

    name: str = "llama_cpp"
    supports_streaming: bool = False
    supports_vision: bool = False

    def __init__(
        self,
        model_path: str = "",
        timeout: float = 60.0,
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
        # Circuit breaker
        self._consecutive_errors = 0
        self._last_error_time = 0.0
        # Stats
        self._request_count = 0
        self._error_count = 0
        self._total_gen_time = 0.0

    # ── Model Download ──

    def _download_model(self) -> bool:
        """Download the GGUF model from HuggingFace.

        3 methods:
          1. huggingface_hub with HF_TOKEN (best for gated models)
          2. urllib with Bearer token header
          3. urllib unauthenticated (last resort)

        Returns True if download succeeded or file already exists.
        """
        if not self.model_path:
            logger.warning("LlamaCppProvider: MODEL_PATH not set, cannot download")
            return False

        # Already exists
        if os.path.exists(self.model_path):
            size_mb = os.path.getsize(self.model_path) / (1024 * 1024)
            logger.info(f"Model file already exists: {self.model_path} ({size_mb:.1f} MB)")
            return True

        hf_token = os.getenv("HF_TOKEN", "")
        download_url = os.getenv("MODEL_DOWNLOAD_URL", MODEL_DOWNLOAD_URL)

        try:
            # Create models directory
            model_dir = os.path.dirname(self.model_path)
            if model_dir:
                os.makedirs(model_dir, exist_ok=True)

            # Method 1: huggingface_hub with HF_TOKEN (best for gated models)
            if hf_token:
                try:
                    from huggingface_hub import hf_hub_download
                    logger.info("Downloading model via huggingface_hub (authenticated)...")

                    # Parse repo and filename from URL
                    if "huggingface.co/" in download_url:
                        parts = download_url.split("huggingface.co/")[1]
                        path_parts = parts.split("/resolve/")
                        if len(path_parts) >= 2:
                            repo_id = path_parts[0]  # e.g. Qwen/Qwen3-4B-GGUF
                            filename = path_parts[1].split("/", 1)[-1]  # e.g. Qwen3-4B-Q4_K_M.gguf

                            start_time = time.time()
                            downloaded_path = hf_hub_download(
                                repo_id=repo_id,
                                filename=filename,
                                token=hf_token,
                                local_dir=model_dir or ".",
                            )
                            elapsed = time.time() - start_time

                            # hf_hub_download may save to a different path — move if needed
                            if downloaded_path != self.model_path and os.path.exists(downloaded_path):
                                import shutil
                                shutil.move(downloaded_path, self.model_path)

                            if os.path.exists(self.model_path):
                                size_mb = os.path.getsize(self.model_path) / (1024 * 1024)
                                if size_mb > 100:
                                    logger.info(f"Model downloaded via HF hub: {size_mb:.1f} MB in {elapsed:.1f}s")
                                    return True

                    logger.warning("Could not parse HuggingFace URL, falling back to direct download")
                except ImportError:
                    logger.info("huggingface_hub not installed, falling back to direct download")
                except Exception as e:
                    logger.warning(f"HF hub download failed: {e}, falling back to direct download")

            # Method 2: Direct download via urllib (with or without token)
            if not download_url:
                logger.warning("MODEL_DOWNLOAD_URL not set, cannot download")
                return False

            import urllib.request

            logger.info(f"Downloading model from {download_url}")
            logger.info(f"Target: {self.model_path}")

            if hf_token:
                # Method 2a: Authenticated download with Bearer token
                logger.info("Using HF_TOKEN for authenticated download")
                request = urllib.request.Request(download_url)
                request.add_header("Authorization", f"Bearer {hf_token}")
                response = urllib.request.urlopen(request, timeout=600)
                with open(self.model_path, 'wb') as f:
                    total_size = int(response.headers.get('content-length', 0))
                    downloaded = 0
                    block_size = 8192
                    while True:
                        chunk = response.read(block_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0 and downloaded % (256 * 1024 * 1024) < block_size:
                            pct = downloaded * 100 // total_size
                            logger.info(f"  Download: {pct}% ({downloaded // 1048576}/{total_size // 1048576} MB)")
            else:
                # Method 2b: Unauthenticated download
                logger.info("No HF_TOKEN — attempting unauthenticated download")
                urllib.request.urlretrieve(download_url, self.model_path)

            # Verify download
            if not os.path.exists(self.model_path):
                logger.error("Download completed but file not found!")
                return False

            size_mb = os.path.getsize(self.model_path) / (1024 * 1024)
            if size_mb < 100:  # Sanity check — model should be ~2.5GB
                logger.error(f"Downloaded file too small ({size_mb:.1f} MB), likely corrupted. Removing.")
                os.remove(self.model_path)
                return False

            logger.info(f"Model downloaded: {size_mb:.1f} MB")
            return True

        except Exception as e:
            logger.error(f"Failed to download model: {e}")
            # Clean up partial download
            if os.path.exists(self.model_path):
                try:
                    os.remove(self.model_path)
                except Exception:
                    pass
            return False

    # ── Model Loading ──

    async def init(self) -> None:
        """Load the GGUF model into memory."""
        if self._loaded and self._llm:
            logger.info("LlamaCppProvider: model already loaded, skipping")
            return

        if not self.model_path:
            raise ProviderError(self.name, "model_path not specified", retryable=False)

        if not ENABLE_LOCAL_MODEL:
            raise ProviderError(self.name, "Local model DISABLED (ENABLE_LOCAL_MODEL=false)", retryable=False)

        try:
            from llama_cpp import Llama
        except ImportError:
            raise ProviderError(
                self.name,
                "llama-cpp-python not installed! Install with: "
                "CMAKE_ARGS='-DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS' pip install llama-cpp-python",
                retryable=False,
            )

        # Auto-download model if not found
        if not os.path.exists(self.model_path):
            logger.info(f"Model file not found at {self.model_path}, attempting auto-download...")
            if not self._download_model():
                raise ProviderError(
                    self.name,
                    f"Model file not found and download failed: {self.model_path}",
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
        """Warm up model - first request is always slower."""
        if not self._llm:
            return

        logger.info("LlamaCppProvider: warming up model...")
        start = time.time()
        try:
            warmup_msg = "/no_think\nПривет, как дела?"
            await asyncio.to_thread(
                self._llm.create_chat_completion,
                messages=[
                    {"role": "system", "content": "Ты Настя - девушка из Москвы, 23 года, блогер."},
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
        """Check if model is loaded and not in cooldown."""
        if not self._loaded or self._llm is None:
            return False

        # Circuit breaker: if too many consecutive errors, pause
        if self._consecutive_errors >= 5:
            elapsed = time.time() - self._last_error_time
            if elapsed < 120:  # 2-minute cooldown
                return False
            else:
                self._consecutive_errors = 0  # Reset after cooldown

        return True

    async def health_check(self) -> bool:
        return self.is_available()

    async def generate(self, prompt: str, **kwargs) -> AIResponse:
        """Generate response via llama-cpp-python.

        Uses asyncio.to_thread() to not block event loop.
        Semaphore ensures only one request at a time.
        Circuit breaker prevents hammering a broken model.
        """
        if not self._llm:
            raise ProviderError(self.name, "Model not loaded", retryable=True)

        # Circuit breaker check
        if self._consecutive_errors >= 5:
            elapsed = time.time() - self._last_error_time
            if elapsed < 120:  # 2-minute cooldown
                raise ProviderError(
                    self.name,
                    f"Local model in cooldown ({self._consecutive_errors} consecutive errors)",
                    retryable=True,
                )
            else:
                self._consecutive_errors = 0  # Reset after cooldown

        system_prompt = kwargs.get("system_prompt", "")
        temperature = kwargs.get("temperature", self.gen_config["temperature"])
        max_tokens = kwargs.get("max_tokens", self.gen_config["max_tokens"])
        messages_history = kwargs.get("messages")

        # ── Aggressive truncation for local model context window ──
        # Truncate system prompt - keep the most important part (persona)
        if len(system_prompt) > LOCAL_MAX_SYSTEM_CHARS:
            # Keep the first part which usually has the persona definition
            system_prompt = system_prompt[:LOCAL_MAX_SYSTEM_CHARS].rsplit('.', 1)[0] + '.'

        # Truncate user prompt
        if len(prompt) > LOCAL_MAX_USER_CHARS:
            prompt = prompt[:LOCAL_MAX_USER_CHARS]

        # Truncate and limit history messages
        truncated_history = []
        if messages_history:
            # Take only the last N messages
            recent = messages_history[-LOCAL_MAX_HISTORY_MSGS:]
            for msg in recent:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role in ("user", "assistant") and content:
                    # Truncate each message
                    if len(content) > LOCAL_MAX_MSG_CHARS:
                        content = content[:LOCAL_MAX_MSG_CHARS] + "..."
                    truncated_history.append({"role": role, "content": content})

        messages = self._build_messages(prompt, system_prompt, truncated_history)

        # Safety check: estimate total tokens
        total_chars = sum(len(m.get("content", "")) for m in messages)
        if total_chars > LOCAL_MAX_TOTAL_CHARS:
            # Remove oldest history messages until it fits
            while len(messages) > 2 and total_chars > LOCAL_MAX_TOTAL_CHARS:  # Keep system + user
                if messages[0].get("role") == "system":
                    messages.pop(1)
                else:
                    messages.pop(0)
                total_chars = sum(len(m.get("content", "")) for m in messages)

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
                    self._consecutive_errors += 1
                    self._last_error_time = time.time()
                    raise ProviderError(self.name, "Empty response from model", retryable=True)

                tokens_used = 0
                usage = response.get("usage", {})
                if usage:
                    tokens_used = usage.get("total_tokens", 0)

                # Reset error tracking on success
                self._consecutive_errors = 0

                logger.info(
                    f"LlamaCppProvider: generated in {elapsed:.1f}s, "
                    f"tokens={tokens_used}, len={len(text)}"
                )

                return AIResponse(
                    text=text.strip(),
                    provider=self.name,
                    model=self._model_name or "local-qwen3-4b",
                    tokens_used=tokens_used,
                    metadata={
                        "local": True,
                        "gen_time": elapsed,
                        "backend": "llama-cpp-python",
                    },
                )

            except asyncio.TimeoutError:
                self._consecutive_errors += 1
                self._last_error_time = time.time()
                self._error_count += 1
                raise ProviderError(
                    self.name,
                    f"Generation timed out ({self.timeout}s)",
                    retryable=True,
                )
            except ProviderError:
                raise
            except Exception as e:
                self._consecutive_errors += 1
                self._last_error_time = time.time()
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
            "model_path": self.model_path,
            "load_time": self._load_time,
            "request_count": self._request_count,
            "error_count": self._error_count,
            "consecutive_errors": self._consecutive_errors,
            "avg_gen_time": avg_gen_time,
            "n_ctx": self.model_config.get("n_ctx", 0),
            "n_threads": self.model_config.get("n_threads", 0),
        }
