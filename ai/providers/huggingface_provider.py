"""HuggingFace Inference API Provider — free tier models, API token optional.

v4.0 FIXED:
  - Proper API key handling (works with or without key)
  - Vision support via Qwen2.5-VL models
  - Correct message format for chat completions
  - Better model selection and fallback
  - Robust error handling
"""
import logging
from typing import Any, Dict, List, Optional

import httpx

from ai.providers.base import AIResponse, BaseProvider, ProviderError

logger = logging.getLogger(__name__)

# Primary models to try in order — using HF Inference API (serverless)
TEXT_MODELS = {
    "default": "Qwen/Qwen2.5-72B-Instruct",
    "fast": "mistralai/Mistral-7B-Instruct-v0.3",
    "reasoning": "deepseek-ai/DeepSeek-V3-0324",
}

# Fallback models if primary fails
FALLBACK_MODELS = [
    "meta-llama/Llama-3.3-70B-Instruct",
    "mistralai/Mixtral-8x7B-Instruct-v0.1",
    "microsoft/phi-4",
    "HuggingFaceH4/zephyr-7b-beta",
]

# Vision model
VISION_MODEL = "Qwen/Qwen2.5-VL-72B-Instruct"


class HuggingFaceProvider(BaseProvider):
    """HuggingFace Inference API provider — free tier, many models.

    Uses the OpenAI-compatible endpoint for maximum compatibility.
    Falls back to the native Inference API if OpenAI-compat fails.
    API key is OPTIONAL — works without one (with lower rate limits).
    """

    name: str = "huggingface"
    supports_streaming: bool = False
    supports_vision: bool = True  # Via Qwen2.5-VL

    def __init__(self, api_key: str = "", timeout: float = 30.0):
        super().__init__(api_key=api_key, timeout=timeout)
        self._last_good_model: Optional[str] = None

    async def init(self) -> None:
        headers = {"Content-Type": "application/json"}
        # Only add Authorization header if api_key is truthy and non-whitespace.
        # Sending "Authorization: Bearer " (empty) causes "Authentication Error,
        # No api key passed in." from HuggingFace. No key = free tier, works fine.
        if self.api_key and self.api_key.strip():
            headers["Authorization"] = f"Bearer {self.api_key.strip()}"

        self._client = httpx.AsyncClient(
            base_url="https://api-inference.huggingface.co",
            headers=headers,
            timeout=httpx.Timeout(self.timeout, connect=10.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
        key_status = "with API key" if (self.api_key and self.api_key.strip()) else "without API key (free tier)"
        logger.info(f"HuggingFace provider initialized ({key_status})")

    def is_available(self) -> bool:
        """HuggingFace is available with or without API key."""
        return True

    async def generate(self, prompt: str, **kwargs) -> AIResponse:
        if not self._client:
            await self.init()

        model_key: str = kwargs.get("model_key", "default")
        model: str = kwargs.get("model", TEXT_MODELS.get(model_key, TEXT_MODELS["default"]))
        system_prompt: str = kwargs.get("system_prompt", "")
        temperature: float = kwargs.get("temperature", 0.7)
        max_tokens: int = kwargs.get("max_tokens", 2048)
        messages_history: Optional[List[Dict[str, Any]]] = kwargs.get("messages")
        image_base64 = kwargs.get("image_base64")

        # Build messages
        messages = self._build_messages(prompt, system_prompt, messages_history)

        # Handle vision via multimodal content
        if image_base64:
            model = VISION_MODEL
            # Modify last user message for vision
            for i in range(len(messages) - 1, -1, -1):
                if messages[i].get("role") == "user":
                    existing_content = messages[i].get("content", "")
                    if isinstance(existing_content, str):
                        messages[i] = {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{image_base64}"
                                    },
                                },
                                {"type": "text", "text": existing_content},
                            ],
                        }
                    break

        # Use last good model if available (but not for vision)
        models_to_try = []
        if not image_base64 and self._last_good_model:
            models_to_try.append(self._last_good_model)
        if model not in models_to_try:
            models_to_try.append(model)
        for fb in FALLBACK_MODELS:
            if fb not in models_to_try:
                models_to_try.append(fb)

        last_error = None
        for try_model in models_to_try:
            # METHOD 1: OpenAI-compatible endpoint
            payload: Dict[str, Any] = {
                "model": try_model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }

            try:
                response = await self._client.post(
                    f"/models/{try_model}/v1/chat/completions",
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()

                if "choices" in data:
                    choice = data["choices"][0]
                    usage = data.get("usage", {})
                    text = choice.get("message", {}).get("content", "")
                else:
                    text = ""
                    usage = {}

                if text:
                    self._last_good_model = try_model
                    return AIResponse(
                        text=text,
                        provider=self.name,
                        model=f"hf:{try_model}",
                        tokens_used=usage.get("total_tokens", 0),
                        metadata={
                            "prompt_tokens": usage.get("prompt_tokens", 0),
                            "completion_tokens": usage.get("completion_tokens", 0),
                            "method": "openai_compat",
                        },
                    )

            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 401:
                    # Auth error — skip to native API
                    logger.warning(f"HF OpenAI-compat auth error for {try_model}, trying native API")
                elif status == 404:
                    # Model not found on OpenAI-compat, try native API
                    pass
                elif status in (429, 503):
                    last_error = ProviderError(self.name, f"Rate limited (HTTP {status}) for {try_model}", retryable=True)
                    continue
                else:
                    last_error = ProviderError(self.name, f"HTTP {status}: {exc.response.text[:200]}", retryable=status in (500, 502, 504))
                    continue
            except httpx.TimeoutException:
                last_error = ProviderError(self.name, f"Timeout for {try_model}", retryable=True)
                continue
            except Exception as exc:
                last_error = ProviderError(self.name, f"OpenAI-compat error for {try_model}: {exc}", retryable=True)

            # METHOD 2: Native Inference API (text-generation)
            try:
                native_payload = {
                    "inputs": prompt,
                    "parameters": {
                        "max_new_tokens": max_tokens,
                        "temperature": temperature,
                        "return_full_text": False,
                    },
                }

                response = await self._client.post(
                    f"/models/{try_model}",
                    json=native_payload,
                )
                response.raise_for_status()
                data = response.json()

                if isinstance(data, list) and data:
                    text = data[0].get("generated_text", "")
                elif isinstance(data, dict):
                    text = data.get("generated_text", "")
                    if not text and "choices" in data:
                        text = data["choices"][0].get("message", {}).get("content", "")
                else:
                    text = ""

                if not text:
                    last_error = ProviderError(self.name, f"Empty response from {try_model}", retryable=True)
                    continue

                self._last_good_model = try_model
                return AIResponse(
                    text=text,
                    provider=self.name,
                    model=f"hf:{try_model}",
                    tokens_used=0,
                    metadata={"method": "native"},
                )

            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 404:
                    logger.warning(f"HF model {try_model} not found")
                    last_error = ProviderError(self.name, f"Model {try_model} not found", retryable=True)
                    continue
                if status in (429, 503):
                    last_error = ProviderError(self.name, f"Rate limited (HTTP {status}) for {try_model}", retryable=True)
                    continue
                last_error = ProviderError(self.name, f"HTTP {status}: {exc.response.text[:200]}", retryable=status in (500, 502, 504))
                continue
            except httpx.TimeoutException:
                last_error = ProviderError(self.name, f"Timeout for {try_model}", retryable=True)
                continue
            except Exception as exc:
                last_error = ProviderError(self.name, f"Native API error for {try_model}: {exc}", retryable=True)
                continue

        if last_error:
            raise last_error
        raise ProviderError(self.name, "All HuggingFace models failed", retryable=True)
