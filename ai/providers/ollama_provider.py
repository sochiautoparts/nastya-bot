"""Ollama Local Provider — Qwen3-VL-2B running on localhost via Ollama.

Architecture:
  - Runs a quantized model LOCALLY via Ollama (no external API dependency!)
  - Model is downloaded and cached during GitHub Actions setup
  - Ollama server runs on localhost:11434 during the bot session
  - PRIMARY provider: free, unlimited, no rate limits, no auth errors
  - Vision support via multimodal models (qwen3-vl)
  - NEVER leaks SSE artifacts (Ollama returns clean JSON)
  - NEVER has authentication errors (local inference)
  - Perfect for channel posts, news commentary, basic chat

v4.0: Reliability and model selection overhaul
  - Use qwen3:1.7b for TEXT-ONLY tasks (FASTER on CPU!)
  - Use qwen3-vl:2b ONLY for vision (image) tasks
  - Only try models that are INSTALLED (no auto-pull of uninstalled models!)
  - asyncio Lock serializes Ollama requests (CPU can't handle concurrent inference)
  - Better timeout handling: 180s for text, 300s for vision
  - No more pulling qwen2.5:3b wasting CPU and disk!
"""
import logging
import asyncio
import re
from typing import Any, Dict, List, Optional

import httpx

from ai.providers.base import AIResponse, BaseProvider, ProviderError

logger = logging.getLogger(__name__)

# Default Ollama endpoint (runs locally in GitHub Actions)
OLLAMA_BASE_URL = "http://localhost:11434"

# Models — ONLY use what's installed via GitHub Actions workflow!
# qwen3:1.7b = fast text model (pulled in workflow)
# qwen3-vl:2b = vision model (pulled in workflow)
# DO NOT add models that aren't pre-installed — auto-pull wastes CPU/disk!
TEXT_MODEL = "qwen3:1.7b"         # Fast text model — PRIMARY for text-only
VISION_MODEL = "qwen3-vl:2b"      # Vision model — ONLY for image tasks

# Vision-capable model prefixes — ANY model starting with these is vision-capable
VISION_MODEL_PREFIXES = ["qwen3-vl", "qwen2.5-vl", "qwen2-vl", "llava", "minicpm-v", "bakllava", "moondream", "llama3-vision"]

# Global lock to serialize Ollama requests — CPU can only handle one inference at a time!
_ollama_lock = asyncio.Lock()


class OllamaProvider(BaseProvider):
    """Ollama local provider — runs Qwen3-VL on localhost.

    This is the PRIMARY provider because:
    - Completely free (no API costs)
    - No rate limits
    - No authentication errors
    - No SSE artifact issues (Ollama returns clean JSON)
    - Vision support built-in
    - Full control over model behavior

    v4.0: Uses asyncio Lock to serialize requests (CPU can't handle concurrent
    inference). Uses qwen3:1.7b for text (faster!) and qwen3-vl:2b for vision only.
    """

    name: str = "ollama"
    supports_streaming: bool = False
    supports_vision: bool = True

    def __init__(self, api_key: str = "", timeout: float = 180.0, base_url: str = ""):
        super().__init__(api_key="", timeout=timeout)
        self.base_url = base_url or OLLAMA_BASE_URL
        self._available: bool = False
        self._text_model: Optional[str] = None
        self._vision_model: Optional[str] = None
        self._vision_available: bool = False
        self._warm: bool = False
        self._installed_models: List[str] = []

    async def init(self) -> None:
        """Initialize and check if Ollama server is running with models."""
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout, connect=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            headers={"Content-Type": "application/json"},
        )

        # Check if Ollama server is running
        try:
            resp = await self._client.get("/api/tags", timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                self._installed_models = [m.get("name", "").lower() for m in data.get("models", [])]
                logger.info(f"Ollama server running. Installed models: {self._installed_models}")

                # Find text model (prefer qwen3:1.7b for speed)
                if any(m.startswith("qwen3:1.7b") or m.startswith("qwen3:1.7b") for m in self._installed_models):
                    self._text_model = TEXT_MODEL
                    self._available = True
                    logger.info(f"Ollama: text model = {self._text_model}")
                elif any(m.startswith("qwen3-vl") for m in self._installed_models):
                    # Fallback: use vision model for text too (slower but works)
                    self._text_model = VISION_MODEL
                    self._available = True
                    logger.info(f"Ollama: text model = {self._text_model} (vision model fallback)")

                # Find vision model
                for prefix in VISION_MODEL_PREFIXES:
                    for installed in self._installed_models:
                        if installed.startswith(prefix):
                            self._vision_available = True
                            self._vision_model = VISION_MODEL
                            logger.info(f"Ollama: vision model detected: {installed} (prefix: {prefix})")
                            break
                    if self._vision_available:
                        break

                if not self._available:
                    # No models at all — try anyway with default
                    logger.warning("Ollama server running but no suitable models found!")
                    self._available = True
                    self._text_model = TEXT_MODEL

            else:
                logger.warning(f"Ollama server returned HTTP {resp.status_code}")
                self._available = False

        except httpx.ConnectError:
            logger.warning("Ollama server not running on localhost:11434. Skipping.")
            self._available = False
        except Exception as e:
            logger.warning(f"Ollama health check failed: {e}")
            self._available = False

        status = "available" if self._available else "not available"
        vision = "+vision" if self._vision_available else " NO_VISION"
        logger.info(f"Ollama provider: {status}{vision} | text_model={self._text_model} | vision_model={self._vision_model}")

    def is_available(self) -> bool:
        """Check if Ollama is potentially available."""
        return True

    async def _warm_up(self) -> None:
        """Send a warm-up request to load the model into memory.

        Ollama loads models lazily — the first request is slow (10-30s on CPU).
        This pre-loads the model so subsequent requests are fast.
        """
        if self._warm:
            return

        if not self._text_model:
            return

        try:
            logger.info(f"Warming up Ollama model: {self._text_model}...")
            resp = await self._client.post(
                "/api/chat",
                json={
                    "model": self._text_model,
                    "messages": [{"role": "user", "content": "привет"}],
                    "stream": False,
                    "options": {"num_predict": 5},
                },
                timeout=httpx.Timeout(180.0, connect=10.0),  # Long timeout for cold start on CPU
            )
            if resp.status_code == 200:
                self._warm = True
                logger.info("Ollama model warmed up successfully!")
            else:
                logger.warning(f"Ollama warm-up failed: HTTP {resp.status_code}")
        except Exception as e:
            logger.warning(f"Ollama warm-up error: {e}")

    @staticmethod
    def _strip_think_tags(text: str) -> str:
        """Remove Qwen3 <think/> blocks from response."""
        text = re.sub(r'<think\b[^>]*>.*?</think\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<thinking\b[^>]*>.*?</thinking\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
        # Also strip any partial think tags that might be left
        text = re.sub(r'</?think[^>]*>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'</?thinking[^>]*>', '', text, flags=re.IGNORECASE)
        return text.strip()

    def _build_ollama_messages(
        self,
        prompt: str,
        system_prompt: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
        image_base64: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Build Ollama-native messages with CORRECT image attachment.

        Images are ALWAYS attached to the CURRENT prompt message (last user msg).
        """
        result: List[Dict[str, Any]] = []

        # System prompt first
        if system_prompt:
            result.append({"role": "system", "content": system_prompt})

        # Add conversation history
        if messages:
            for msg in messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role in ("user", "assistant", "system") and content:
                    result.append({"role": role, "content": content})

        # Check if the last message in history already matches the current prompt
        last_is_current = (
            messages
            and len(messages) > 0
            and messages[-1].get("role") == "user"
            and messages[-1].get("content") == prompt
        )

        # Add current user prompt with image if applicable
        if not last_is_current:
            current_msg: Dict[str, Any] = {"role": "user", "content": prompt}
            # Attach image to the CURRENT prompt message
            if image_base64 and self._vision_available:
                current_msg["images"] = [image_base64]
            result.append(current_msg)
        elif image_base64 and self._vision_available:
            # Last history message IS the current prompt — attach image to it
            for i in range(len(result) - 1, -1, -1):
                if result[i]["role"] == "user":
                    result[i]["images"] = [image_base64]
                    break

        # Merge consecutive same-role messages (Ollama can reject them)
        merged: List[Dict[str, Any]] = []
        for msg in result:
            if merged and merged[-1].get("role") == msg.get("role"):
                prev_content = merged[-1].get("content", "")
                new_content = msg.get("content", "")
                merged[-1]["content"] = f"{prev_content}\n{new_content}"
                if "images" in msg:
                    merged[-1]["images"] = msg["images"]
            else:
                merged.append(msg)

        return merged

    def _is_model_installed(self, model_name: str) -> bool:
        """Check if a model is installed locally."""
        prefix = model_name.split(":")[0]
        return any(m.startswith(prefix) for m in self._installed_models)

    async def generate(self, prompt: str, **kwargs) -> AIResponse:
        """Generate text via local Ollama instance.

        v4.0: Uses Lock to serialize requests (CPU can only do one inference at a time).
        Uses qwen3:1.7b for text (faster), qwen3-vl:2b for vision only.
        Only tries INSTALLED models — no auto-pull of uninstalled models!
        """
        if not self._client:
            await self.init()

        if not self._available:
            raise ProviderError(self.name, "Ollama server not available", retryable=False)

        # Warm up on first real request
        if not self._warm:
            await self._warm_up()

        system_prompt = kwargs.get("system_prompt", "")
        temperature = kwargs.get("temperature", 0.7)
        max_tokens = kwargs.get("max_tokens", 2048)
        messages_history = kwargs.get("messages")
        image_base64 = kwargs.get("image_base64")

        # Limit history for small models
        if image_base64 and messages_history and len(messages_history) > 10:
            logger.info(f"Ollama: Trimming history for vision: {len(messages_history)} -> 10 messages")
            messages_history = messages_history[-10:]
        elif not image_base64 and messages_history and len(messages_history) > 30:
            messages_history = messages_history[-30:]

        # ── Select model based on task type ──
        # Text-only → qwen3:1.7b (FASTER on CPU!)
        # Vision → qwen3-vl:2b (required for image processing)
        is_vision_request = bool(image_base64 and self._vision_available)

        if is_vision_request:
            primary_model = self._vision_model or VISION_MODEL
            fallback_model = self._text_model  # Fallback to text model without image
        else:
            primary_model = self._text_model or TEXT_MODEL
            fallback_model = None  # For text, just try the primary model

        # Build list of models to try — ONLY installed ones!
        models_to_try = []
        if primary_model and self._is_model_installed(primary_model):
            models_to_try.append(primary_model)
        if fallback_model and fallback_model != primary_model and self._is_model_installed(fallback_model):
            models_to_try.append(fallback_model)

        # If no models found in installed list, try the primary anyway
        if not models_to_try:
            models_to_try = [primary_model]

        last_error = None
        for try_model in models_to_try:
            # For fallback models (non-vision), don't attach image
            img = image_base64 if (is_vision_request and try_model == primary_model) else None

            ollama_messages = self._build_ollama_messages(
                prompt=prompt,
                system_prompt=system_prompt,
                messages=messages_history,
                image_base64=img,
            )

            msg_count = len(ollama_messages)
            has_images = any("images" in m for m in ollama_messages)
            logger.info(f"Ollama: Sending {msg_count} messages to {try_model} (vision={has_images})")

            payload = {
                "model": try_model,
                "messages": ollama_messages,
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                },
            }

            # ── CRITICAL: Serialize Ollama requests ──
            # CPU can only handle ONE inference at a time. Without the lock,
            # concurrent requests cause timeouts and cascade failures.
            request_timeout = self.timeout
            if img:
                request_timeout = min(request_timeout * 1.5, 300)  # Vision needs more time
                logger.info(f"Ollama: Extended timeout {request_timeout:.0f}s for vision request")

            async with _ollama_lock:
                try:
                    response = await self._client.post(
                        "/api/chat",
                        json=payload,
                        timeout=httpx.Timeout(request_timeout, connect=15.0),
                    )
                    response.raise_for_status()
                    data = response.json()

                    # Ollama native API returns: {"message": {"role": "assistant", "content": "..."}}
                    text = ""
                    if isinstance(data, dict):
                        msg = data.get("message", {})
                        text = msg.get("content", "") if isinstance(msg, dict) else ""

                    if not text:
                        last_error = ProviderError(
                            self.name,
                            f"Empty response from {try_model}",
                            retryable=True,
                        )
                        continue

                    # Clean up think tags (Qwen3 uses <think/> blocks)
                    text = self._strip_think_tags(text)

                    if not text:
                        last_error = ProviderError(
                            self.name,
                            f"Empty content after cleaning from {try_model}",
                            retryable=True,
                        )
                        continue

                    return AIResponse(
                        text=text,
                        provider=self.name,
                        model=f"ollama:{try_model}",
                        tokens_used=0,
                        metadata={
                            "local": True,
                            "vision": is_vision_request,
                        },
                    )

                except httpx.ConnectError:
                    raise ProviderError(
                        self.name,
                        "Ollama server not running on localhost:11434",
                        retryable=False,
                    )
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    if status == 404:
                        # Model not found — DO NOT auto-pull! Just fail and try next model.
                        logger.warning(f"Model {try_model} not found locally. Skipping (no auto-pull).")
                        last_error = ProviderError(
                            self.name,
                            f"Model {try_model} not found locally",
                            retryable=True,
                        )
                        continue
                    last_error = ProviderError(
                        self.name,
                        f"HTTP {status}: {exc.response.text[:200]}",
                        retryable=status in (429, 500, 502, 503, 504),
                    )
                    continue
                except httpx.TimeoutException:
                    last_error = ProviderError(
                        self.name,
                        f"Request timed out for {try_model} (CPU inference can be slow)",
                        retryable=True,
                    )
                    continue
                except Exception as exc:
                    last_error = ProviderError(
                        self.name,
                        f"Unexpected error with {try_model}: {exc}",
                        retryable=True,
                    )
                    continue

        if last_error:
            raise last_error
        raise ProviderError(self.name, "All Ollama models failed", retryable=True)
