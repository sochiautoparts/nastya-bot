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

v2.0: Improved reliability
  - Better model detection and auto-pull
  - Longer timeouts for cold starts
  - Qwen3 <think/> tag stripping
  - Robust fallback chain: qwen3-vl:2b → qwen3:1.7b → qwen2.5:3b
  - Model cache verification on startup
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

# Models to try in order — Qwen3-VL-2B is the target
TEXT_MODELS = {
    "default": "qwen3-vl:2b",       # Primary: Qwen3-VL-2B — vision + text
    "fast": "qwen3:1.7b",           # Fast: smaller Qwen3 for quick responses
    "reasoning": "qwen3:4b",        # Reasoning: bigger model for complex tasks
    "fallback": "qwen2.5:3b",      # Fallback: stable Qwen2.5
}

# Vision-capable model prefixes — ANY model starting with these is vision-capable
# CRITICAL: "qwen3-vl" must be here! Without it, photos are not sent to Ollama!
VISION_MODEL_PREFIXES = ["qwen3-vl", "qwen2.5-vl", "qwen2-vl", "llava", "minicpm-v", "bakllava", "moondream", "llama3-vision"]
VISION_MODEL = "qwen3-vl:2b"


class OllamaProvider(BaseProvider):
    """Ollama local provider — runs Qwen3-VL on localhost.

    This is the PRIMARY provider because:
    - Completely free (no API costs)
    - No rate limits
    - No authentication errors
    - No SSE artifact issues (Ollama returns clean JSON)
    - Vision support built-in
    - Full control over model behavior

    The model is 'woken up' when the GitHub Actions workflow starts.
    Ollama automatically loads the model on first request (cold start ~10-30s).
    """

    name: str = "ollama"
    supports_streaming: bool = False
    supports_vision: bool = True

    def __init__(self, api_key: str = "", timeout: float = 90.0, base_url: str = ""):
        super().__init__(api_key="", timeout=timeout)
        self.base_url = base_url or OLLAMA_BASE_URL
        self._available: bool = False
        self._available_model: Optional[str] = None
        self._vision_available: bool = False
        self._warm: bool = False

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
                installed_models = [m.get("name", "").lower() for m in data.get("models", [])]
                logger.info(f"Ollama server running. Installed models: {installed_models}")

                # Find the best available model
                for model_key in ["default", "fast", "reasoning", "fallback"]:
                    model_name = TEXT_MODELS.get(model_key, "")
                    # Check exact match or prefix match (e.g., "qwen3-vl:2b" matches "qwen3-vl:2b-q4_K_M")
                    for installed in installed_models:
                        if installed.startswith(model_name.split(":")[0]):
                            self._available = True
                            self._available_model = model_name
                            logger.info(f"Ollama: using model {model_name}")
                            break
                    if self._available:
                        break

                # Check for vision model — use prefix list for robust detection
                # This ensures qwen3-vl:2b is correctly detected as vision-capable
                for prefix in VISION_MODEL_PREFIXES:
                    for installed in installed_models:
                        if installed.startswith(prefix):
                            self._vision_available = True
                            logger.info(f"Ollama: vision model detected: {installed} (prefix: {prefix})")
                            break
                    if self._vision_available:
                        break

                if not self._available:
                    logger.warning("Ollama server running but no suitable models found. Will try to pull.")
                    self._available = True  # Try anyway — Ollama can auto-pull
                    self._available_model = TEXT_MODELS["default"]

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
        logger.info(f"Ollama provider: {status}{vision} | model={self._available_model} | vision_detected={self._vision_available}")

    def is_available(self) -> bool:
        """Check if Ollama is potentially available.

        Returns True because Ollama doesn't need API keys — we can only
        know for sure after init() connects to the server. The actual
        availability check happens in init() and generate().
        """
        # Ollama needs no API key — always potentially available.
        # The real check happens in init() which connects to the server.
        # If Ollama is not running, generate() will raise ProviderError
        # and the router will fall back to other providers.
        return True

    async def _warm_up(self) -> None:
        """Send a warm-up request to load the model into memory.

        Ollama loads models lazily — the first request is slow (10-30s on CPU).
        This pre-loads the model so subsequent requests are fast.
        """
        if self._warm:
            return

        if not self._available_model:
            return

        try:
            logger.info(f"Warming up Ollama model: {self._available_model}...")
            resp = await self._client.post(
                "/api/chat",
                json={
                    "model": self._available_model,
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

    async def generate(self, prompt: str, **kwargs) -> AIResponse:
        """Generate text via local Ollama instance.

        Uses the Ollama native /api/chat endpoint for
        clean JSON responses — NO SSE artifacts, NO auth errors.
        """
        if not self._client:
            await self.init()

        if not self._available:
            raise ProviderError(self.name, "Ollama server not available", retryable=False)

        # Warm up on first real request
        if not self._warm:
            await self._warm_up()

        model = kwargs.get("model", self._available_model or TEXT_MODELS["default"])
        system_prompt = kwargs.get("system_prompt", "")
        temperature = kwargs.get("temperature", 0.7)
        max_tokens = kwargs.get("max_tokens", 2048)
        messages_history = kwargs.get("messages")
        image_base64 = kwargs.get("image_base64")

        # Build messages — LIMIT history for vision requests (2B model has limited context)
        # Vision with 50 history messages = context overflow / extremely slow on CPU
        if image_base64 and messages_history and len(messages_history) > 10:
            logger.info(f"Ollama: Trimming history for vision request: {len(messages_history)} -> 10 messages")
            messages_history = messages_history[-10:]

        messages = self._build_messages(prompt, system_prompt, messages_history)

        # Handle vision — Ollama native API uses "images" field at message level
        # NOT the OpenAI format with content list!
        # See: https://github.com/ollama/ollama/blob/main/docs/api.md
        ollama_images = None
        if image_base64 and self._vision_available:
            model = VISION_MODEL
            # Ollama native API expects images as an array of base64 strings
            # attached to the user message as a separate "images" field
            ollama_images = [image_base64]
            logger.info(f"Ollama: Sending image with vision model {model} (image size: {len(image_base64)} chars)")
        elif image_base64 and not self._vision_available:
            # No vision model available — describe without image
            logger.warning("Ollama: Image provided but no vision model. Processing text only.")

        # Try models in order if primary fails
        models_to_try = [model]
        if model != TEXT_MODELS.get("fast"):
            models_to_try.append(TEXT_MODELS["fast"])
        if model != TEXT_MODELS.get("fallback"):
            models_to_try.append(TEXT_MODELS["fallback"])

        last_error = None
        for try_model in models_to_try:
            # Build Ollama-native messages format
            # Ollama expects: {"role": "user", "content": "text", "images": ["base64..."]}
            # CRITICAL: Images MUST go on the LAST user message (the current prompt),
            # NOT the first! Previous code attached to first user message = BROKEN VISION!
            ollama_messages = []
            for msg in messages:
                ollama_msg = {"role": msg["role"], "content": msg["content"]}
                ollama_messages.append(ollama_msg)

            # Attach images to the LAST user message (the current prompt with the image)
            if ollama_images:
                for i in range(len(ollama_messages) - 1, -1, -1):
                    if ollama_messages[i]["role"] == "user":
                        ollama_messages[i]["images"] = ollama_images
                        logger.info(f"Ollama: Attached image to last user message (index {i})")
                        break

            payload = {
                "model": try_model,
                "messages": ollama_messages,
                "stream": False,  # CRITICAL: No streaming — clean JSON response!
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                },
            }

            try:
                # Use /api/chat endpoint (Ollama native) — always returns JSON
                # Vision + CPU inference needs longer timeout!
                request_timeout = self.timeout
                if ollama_images:
                    request_timeout = min(request_timeout * 2, 300)  # Up to 5 min for vision on CPU
                    logger.info(f"Ollama: Using extended timeout {request_timeout}s for vision request")
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
                    # Try OpenAI-compatible endpoint as fallback
                    try:
                        oai_payload = {
                            "model": try_model,
                            "messages": messages,
                            "temperature": temperature,
                            "max_tokens": max_tokens,
                            "stream": False,
                        }
                        oai_resp = await self._client.post(
                            "/v1/chat/completions",
                            json=oai_payload,
                            timeout=httpx.Timeout(self.timeout, connect=10.0),
                        )
                        oai_resp.raise_for_status()
                        oai_data = oai_resp.json()
                        if "choices" in oai_data:
                            text = oai_data["choices"][0].get("message", {}).get("content", "")
                    except Exception:
                        pass

                if not text:
                    last_error = ProviderError(
                        self.name,
                        f"Empty response from {try_model}",
                        retryable=True,
                    )
                    continue

                # Clean up think tags (Qwen3 uses <think/> blocks)
                text = re.sub(r'<think\b[^>]*>.*?</think\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r'<thinking\b[^>]*>.*?</thinking\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
                text = text.strip()

                if not text:
                    last_error = ProviderError(
                        self.name,
                        f"Empty content after cleaning from {try_model}",
                        retryable=True,
                    )
                    continue

                # Update available model if we found a working one
                if not self._available_model:
                    self._available_model = try_model
                    self._available = True

                return AIResponse(
                    text=text,
                    provider=self.name,
                    model=f"ollama:{try_model}",
                    tokens_used=0,
                    metadata={
                        "local": True,
                        "vision": bool(image_base64 and self._vision_available),
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
                    # Model not found — try to pull it
                    logger.warning(f"Model {try_model} not found locally, attempting pull...")
                    try:
                        pull_resp = await self._client.post(
                            "/api/pull",
                            json={"name": try_model, "stream": False},
                            timeout=httpx.Timeout(300.0, connect=10.0),
                        )
                        if pull_resp.status_code == 200:
                            logger.info(f"Successfully pulled model {try_model}")
                            self._available_model = try_model
                            # Retry the request
                            continue
                    except Exception as pull_err:
                        logger.error(f"Failed to pull model {try_model}: {pull_err}")

                    last_error = ProviderError(
                        self.name,
                        f"Model {try_model} not found and pull failed",
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
