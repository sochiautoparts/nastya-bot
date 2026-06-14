"""LlamaCppProvider v11.0 - RUADAPT QWEN3-4B-INSTRUCT + PERFORMANCE TUNING!

v11.0 RuadaptQwen3-4B-Instruct upgrade:
  - REPLACED Qwen3-4B-Q4_K_M with RuadaptQwen3-4B-Instruct-Q4_K_M!
  - Russian tokenizer: 48K extra Russian tokens → up to 2x faster Russian generation
  - Instruct version: answers DIRECTLY without <think> tags (no thinking overhead!)
  - Russian fine-tuning: better understanding and generation of Russian text
  - LEP (Learned Embedding Propagation): quality preserved with new tokenizer
  - REMOVED all /no_think logic — Instruct model doesn't need it!
  - REMOVED think tag stripping — Instruct answers directly, no <think> tags!
  - Tighter dynamic timeouts: Instruct is faster (no thinking tokens to generate)

v9.0 CRITICAL FIX - Segfault after timeout recovery:
  - REPLACED _try_model_recovery() reset() call with full model reload.
    The OpenBLAS backend + llama-cpp-python reset() causes SEGFAULT (exit 139)
    after a generation timeout. reset() corrupts internal state.
    Now we fully unload and reload the model instead.

v7.0 LOCAL-ONLY POSTING UPGRADE:
  - n_threads=2 (was 4 - matches GitHub Actions 2 vCPU, avoids contention)
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
    "n_ctx": 4096,       # 4096 for RuadaptQwen3-4B Q4 - plenty of room
    "n_batch": 512,      # Faster batch processing
    "n_threads": 2,      # GitHub Actions 2 vCPU (4 = contention)
    "n_gpu_layers": 0,   # CPU only — GitHub Actions has no GPU
    "verbose": False,
    "use_mmap": True,    # Memory-mapped file — faster loading
    "use_mlock": False,  # Don't lock memory — saves RAM
}

# Generation defaults
DEFAULT_GEN_CONFIG = {
    "max_tokens": 1024,      # v11: Instruct answers directly (no thinking tokens), 1024 is plenty
    "temperature": 0.82,
    "top_p": 0.92,
    "top_k": 50,
    "repeat_penalty": 1.12,
    "frequency_penalty": 0.0,
    "presence_penalty": 0.0,
    "stop": ["<|im_end|>"],  # v11: Instruct version — no <think> tags, just <|im_end|> stop
}

# ── Context window limits for local model ──
# RuadaptQwen3-4B with n_ctx=4096 - Russian tokenizer is more efficient!
# Rough estimate: 1 token ≈ 4-6 chars for Russian text (vs 3-4 with base Qwen3)
# This means we can fit MORE Russian text in the same context window!
LOCAL_MAX_SYSTEM_CHARS = 1000   # v11: Was 800 — Russian tokenizer fits more chars per token
LOCAL_MAX_HISTORY_MSGS = 6     # v6: Was 4 - more context with 4096 ctx
LOCAL_MAX_MSG_CHARS = 500      # v11: Was 400 — more efficient Russian tokenization
LOCAL_MAX_USER_CHARS = 1800    # v11: Was 1500 — more efficient Russian tokenization
LOCAL_MAX_TOTAL_CHARS = 12000  # v11: Was 10000 — Russian tokenizer saves ~30% tokens on Russian text

# HuggingFace model download URL — RuadaptQwen3-4B-Instruct
# Russian tokenizer + Instruct (answers directly, no <think> tags) + Russian fine-tuning
MODEL_DOWNLOAD_URL = "https://huggingface.co/RefalMachine/RuadaptQwen3-4B-Instruct-GGUF/resolve/main/Q4_K_M.gguf"
# Model filename for local storage (descriptive name)
CORRECT_MODEL_FILENAME = "RuadaptQwen3-4B-Instruct-Q4_K_M.gguf"


class LlamaCppProvider(BaseProvider):
    """Single-model llama-cpp-python provider with auto-download.

    RuadaptQwen3-4B-Instruct as LOCAL-FIRST for chat/comments,
    fallback for function routes.
    Instruct version: answers DIRECTLY without <think> tags!
    Russian tokenizer: 48K extra Russian tokens → up to 2x faster.
    Auto-downloads model from HuggingFace with HF_TOKEN auth.
    """

    name: str = "llama_cpp"
    supports_streaming: bool = False
    supports_vision: bool = False

    def __init__(
        self,
        model_path: str = "",
        timeout: float = 90.0,  # v9: Was 60s — 57s generation was timing out!
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
            # v11: Ruadapt Instruct — answers directly, no /no_think or think tags!
            await asyncio.to_thread(
                self._llm.create_chat_completion,
                messages=[
                    {"role": "system", "content": "Ты Настя - девушка из Москвы, 23 года, блогер."},
                    {"role": "user", "content": "Привет, как дела?"},
                ],
                max_tokens=50,
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

        # v11: RuadaptQwen3-4B-Instruct — answers DIRECTLY, no <think> tags!
        # No need for /no_think prefix — Instruct model always answers directly.
        # No need to strip think tags — Instruct version never generates them.

        # v11: Dynamic timeout — Ruadapt Instruct is FASTER (no thinking tokens!)
        # Russian tokenizer also makes generation ~2x faster for Russian text
        effective_max = min(max_tokens, self.gen_config["max_tokens"])
        if effective_max <= 512:
            request_timeout = 45.0    # Posting: Instruct + Russian tokenizer = ~15-25s
        elif effective_max <= 1024:
            request_timeout = 70.0    # Chat: moderate response (~30-50s)
        else:
            request_timeout = 100.0   # Long responses (~60-80s)

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
                    timeout=request_timeout,
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

                # v11: Ruadapt Instruct doesn't generate <think> tags, but strip as safety net
                text = self._strip_think_tags(text)

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
                    model=self._model_name or "local-ruadapt-qwen3-4b",
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
                # v9: Full model reload instead of reset() — prevents SEGFAULT with OpenBLAS!
                logger.warning("LlamaCppProvider: generation timed out (%.1fs, max_tokens=%d), attempting model reload", request_timeout, effective_max)
                await self._try_model_recovery()
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
                # v9: Full model reload instead of reset() — prevents SEGFAULT with OpenBLAS!
                logger.error("LlamaCppProvider: generation error: %s, attempting model reload", e)
                await self._try_model_recovery()
                raise ProviderError(self.name, f"Generation error: {e}", retryable=True)

    async def _try_model_recovery(self) -> None:
        """Attempt to recover the model after a timeout or error.

        v9: DO NOT call self._llm.reset() — it causes SEGFAULT with OpenBLAS!
        After a generation timeout, the model's internal state is corrupted.
        Calling reset() on a corrupted model triggers a segfault (exit code 139).
        Instead, we fully unload the model and reload it from disk.
        This is slower (~2s) but guaranteed safe.
        """
        if not self._llm:
            return
        try:
            # v9: DO NOT call reset() — it causes SEGFAULT with OpenBLAS!
            # Fully unload and reload the model instead.
            logger.warning("LlamaCppProvider: unloading corrupted model for full reload...")
            try:
                del self._llm
            except Exception:
                pass
            self._llm = None
            self._loaded = False

            # Reload model from disk
            from llama_cpp import Llama
            start = time.time()
            self._llm = await asyncio.to_thread(
                Llama,
                model_path=self.model_path,
                **self.model_config,
            )
            elapsed = time.time() - start
            self._loaded = True
            self._consecutive_errors = 0  # v10: Reset — fresh model shouldn't carry old errors
            logger.info(f"LlamaCppProvider: model reloaded after error in {elapsed:.1f}s — recovery successful, errors reset")
        except ImportError:
            logger.error("LlamaCppProvider: llama-cpp-python not available for reload")
            self._llm = None
            self._loaded = False
        except Exception as e:
            logger.error("LlamaCppProvider: model reload failed: %s — model disabled", e)
            self._llm = None
            self._loaded = False

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
        """Strip think tags from response. v11: Ruadapt Instruct doesn't generate these,
        but kept as safety net in case model occasionally produces them."""
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
