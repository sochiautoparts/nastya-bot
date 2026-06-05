"""Ollama Local Provider - v26.0 (NO QWEN).

Architecture v26.0:
  - phi4-mini:3.8b as FAST text model (best quality/speed on CPU)
  - moondream for VISION (2-3x faster than qwen3-vl on CPU!)
  - Semaphore(2) - allows 2 concurrent inferences
  - Smart model selection: vision model NEVER used for plain text
  - Reduced timeouts: 90s text, 15s vision + Pollinations fallback
  - Health check caching - no more spam every 30s
"""
import logging
import asyncio
import re
import time
from typing import Any, Dict, List, Optional

import httpx

from ai.providers.base import AIResponse, BaseProvider, ProviderError

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = "http://localhost:11434"

# Model priority for TEXT (fastest first)
TEXT_MODELS = ["phi4-mini:3.8b"]
# Model for VISION - moondream is 2-3x faster than qwen3-vl on CPU!
VISION_MODELS = ["moondream"]

VISION_MODEL_PREFIXES = ["moondream", "llava", "minicpm-v", "phi4-mini-vl"]

# Semaphore allows 2 concurrent Ollama requests (CPU can handle 2 small models)
_ollama_semaphore = asyncio.Semaphore(2)

# Health check cache
_last_health_check: float = 0
_last_health_status: bool = True
_HEALTH_CACHE_TTL = 120  # Cache health check for 120 seconds


class OllamaProvider(BaseProvider):
    """Ollama local provider - v26.0 (NO QWEN).

    phi4-mini for text, moondream for vision.
    Never uses vision model for plain text.
    """

    name: str = "ollama"
    supports_streaming: bool = False
    supports_vision: bool = True

    def __init__(self, api_key: str = "", timeout: float = 90.0, base_url: str = ""):
        super().__init__(api_key="", timeout=timeout)
        self.base_url = base_url or OLLAMA_BASE_URL
        self._available: bool = False
        self._text_model: Optional[str] = None
        self._vision_model: Optional[str] = None
        self._vision_available: bool = False
        self._warm: bool = False
        self._installed_models: List[str] = []
        self._warm_models: set = set()

    async def init(self) -> None:
        """Initialize and check if Ollama server is running with models."""
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout, connect=10.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            headers={"Content-Type": "application/json"},
        )

        try:
            resp = await self._client.get("/api/tags", timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                self._installed_models = [m.get("name", "").lower() for m in data.get("models", [])]
                logger.info(f"Ollama server running. Installed models: {self._installed_models}")

                # Select TEXT model - phi4-mini only, NEVER vision model for text!
                for model in TEXT_MODELS:
                    if self._is_model_installed(model):
                        self._text_model = model
                        logger.info(f"Ollama: text model = {self._text_model}")
                        break
                
                if not self._text_model:
                    # Last resort - use any installed model
                    if self._installed_models:
                        self._text_model = self._installed_models[0]
                        logger.warning(f"Ollama: no preferred text model, using {self._text_model}")
                    else:
                        self._text_model = "phi4-mini:3.8b"
                        logger.warning("Ollama: no models found, defaulting to phi4-mini:3.8b")

                # Select VISION model
                for model in VISION_MODELS:
                    if self._is_model_installed(model):
                        self._vision_model = model
                        self._vision_available = True
                        logger.info(f"Ollama: vision model = {self._vision_model}")
                        break

                if not self._vision_available:
                    # Check by prefix
                    for prefix in VISION_MODEL_PREFIXES:
                        for installed in self._installed_models:
                            if installed.startswith(prefix):
                                self._vision_model = installed
                                self._vision_available = True
                                logger.info(f"Ollama: vision model detected: {installed}")
                                break
                        if self._vision_available:
                            break

                self._available = True
            else:
                logger.warning(f"Ollama server returned HTTP {resp.status_code}")
                self._available = True  # Try anyway

        except httpx.ConnectError:
            logger.warning("Ollama server not running. Skipping.")
            self._available = True  # Try anyway - errors handled in generate()
        except Exception as e:
            logger.warning(f"Ollama health check failed: {e}")
            self._available = True  # Try anyway

        status = "available" if self._available else "not available"
        vision = "+vision" if self._vision_available else " NO_VISION"
        logger.info(f"Ollama provider: {status}{vision} | text={self._text_model} | vision={self._vision_model}")

    def is_available(self) -> bool:
        return True  # Always try - errors handled in generate()

    async def _warm_up(self) -> None:
        """Warm up the text model."""
        if self._warm and self._text_model in self._warm_models:
            return

        model_to_warm = self._text_model
        if not model_to_warm:
            return

        if model_to_warm in self._warm_models:
            self._warm = True
            return

        try:
            logger.info(f"Warming up Ollama model: {model_to_warm}...")
            resp = await self._client.post(
                "/api/chat",
                json={
                    "model": model_to_warm,
                    "messages": [{"role": "user", "content": "привет"}],
                    "stream": False,
                    "options": {"num_predict": 5},
                },
                timeout=httpx.Timeout(120.0, connect=10.0),
            )
            if resp.status_code == 200:
                self._warm = True
                self._warm_models.add(model_to_warm)
                logger.info(f"Ollama model {model_to_warm} warmed up successfully!")
            else:
                logger.warning(f"Ollama warm-up failed: HTTP {resp.status_code}")
        except Exception as e:
            logger.warning(f"Ollama warm-up error: {e}")

    @staticmethod
    def _strip_think_tags(text: str) -> str:
        """Remove Qwen3/Phi think blocks from response."""
        text = re.sub(r'<think\b[^>]*>.*?</think\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<thinking\b[^>]*>.*?</thinking\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
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
        """Build Ollama-native messages with correct image attachment."""
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
            messages
            and len(messages) > 0
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

        # Merge consecutive same-role messages
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

    async def health_check(self) -> bool:
        """Check if Ollama server is still responding - with caching."""
        global _last_health_check, _last_health_status
        
        now = time.time()
        if now - _last_health_check < _HEALTH_CACHE_TTL:
            logger.debug("Ollama health check: using cached result")
            return _last_health_status
            
        try:
            if not self._client:
                return False
            resp = await self._client.get("/api/tags", timeout=5.0)
            _last_health_status = resp.status_code == 200
        except Exception:
            _last_health_status = False
            
        _last_health_check = now
        return _last_health_status

    async def generate(self, prompt: str, **kwargs) -> AIResponse:
        """Generate text via local Ollama instance.

        v7.0: CRITICAL FIX - vision model NEVER used for plain text!
        Text always goes to phi4-mini.
        Vision goes to moondream.
        """
        if not self._client:
            await self.init()

        system_prompt = kwargs.get("system_prompt", "")
        temperature = kwargs.get("temperature", 0.7)
        max_tokens = kwargs.get("max_tokens", 2048)
        messages_history = kwargs.get("messages")
        image_base64 = kwargs.get("image_base64")

        # Limit history
        if image_base64 and messages_history and len(messages_history) > 8:
            messages_history = messages_history[-8:]
        elif not image_base64 and messages_history and len(messages_history) > 20:
            messages_history = messages_history[-20:]

        # ── CRITICAL: Model selection ──
        # Vision requests: ONLY use vision model
        # Text requests: ONLY use text model (NEVER vision model for text!)
        is_vision_request = bool(image_base64 and self._vision_available)

        if is_vision_request:
            model_to_use = self._vision_model or "moondream"
            models_to_try = [model_to_use]
            request_timeout = 180.0  # Vision needs more time
            logger.info(f"Ollama: VISION request -> {model_to_use}")
        else:
            model_to_use = self._text_model or "phi4-mini:3.8b"
            # NEVER add vision model to text models!
            models_to_try = [model_to_use]
            # If text model is the same as vision model, use phi4-mini instead
            if model_to_use == self._vision_model:
                if self._is_model_installed("phi4-mini:3.8b"):
                    models_to_try = ["phi4-mini:3.8b"]
            request_timeout = 90.0  # Text should be faster
            logger.info(f"Ollama: TEXT request -> {model_to_use}")

        # Warm up text model on first request
        if not self._warm and self._text_model:
            await self._warm_up()

        last_error = None
        for try_model in models_to_try:
            img = image_base64 if (is_vision_request and try_model == models_to_try[0]) else None

            # Warm up model if not yet warmed
            if try_model not in self._warm_models and try_model != self._text_model:
                try:
                    logger.info(f"Pre-warming model {try_model}...")
                    warm_resp = await self._client.post(
                        "/api/chat",
                        json={
                            "model": try_model,
                            "messages": [{"role": "user", "content": "привет"}],
                            "stream": False,
                            "options": {"num_predict": 3},
                        },
                        timeout=httpx.Timeout(120.0, connect=10.0),
                    )
                    if warm_resp.status_code == 200:
                        self._warm_models.add(try_model)
                except Exception:
                    pass

            ollama_messages = self._build_ollama_messages(
                prompt=prompt,
                system_prompt=system_prompt,
                messages=messages_history,
                image_base64=img,
            )

            msg_count = len(ollama_messages)
            has_images = any("images" in m for m in ollama_messages)
            logger.info(f"Ollama: Sending {msg_count} msgs to {try_model} (vision={has_images})")

            payload = {
                "model": try_model,
                "messages": ollama_messages,
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                },
            }

            # Use semaphore instead of lock - allows 2 concurrent requests
            async with _ollama_semaphore:
                try:
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

                    text = self._strip_think_tags(text)

                    if not text:
                        last_error = ProviderError(self.name, f"Empty after cleaning from {try_model}", retryable=True)
                        continue

                    self._warm_models.add(try_model)

                    return AIResponse(
                        text=text,
                        provider=self.name,
                        model=f"ollama:{try_model}",
                        tokens_used=0,
                        metadata={"local": True, "vision": is_vision_request},
                    )

                except httpx.ConnectError:
                    raise ProviderError(self.name, "Ollama server not running", retryable=False)
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    if status == 404:
                        logger.warning(f"Model {try_model} not found locally. Skipping.")
                        last_error = ProviderError(self.name, f"Model {try_model} not found", retryable=True)
                        continue
                    last_error = ProviderError(self.name, f"HTTP {status}", retryable=status in (429, 500, 502, 503, 504))
                    continue
                except httpx.TimeoutException:
                    last_error = ProviderError(self.name, f"Timeout for {try_model} (CPU slow)", retryable=True)
                    continue
                except Exception as exc:
                    last_error = ProviderError(self.name, f"Error with {try_model}: {exc}", retryable=True)
                    continue

        if last_error:
            raise last_error
        raise ProviderError(self.name, "All Ollama models failed", retryable=True)
