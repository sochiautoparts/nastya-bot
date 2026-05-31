"""HuggingFace Inference API Provider — free tier models with API token.

Uses HF inference API (serverless) for text generation.
Requires: HUGGINGFACE_API_KEY
Free tier: rate limited but works reliably with many models.
Models: Qwen 2.5 72B, Llama 3.3 70B, Mistral 7B, DeepSeek V3, etc.
"""
import logging
from typing import Any, Dict, List, Optional

import httpx

from ai.providers.base import AIResponse, BaseProvider, ProviderError

logger = logging.getLogger(__name__)

# Primary models to try in order
TEXT_MODELS = {
    "default": "Qwen/Qwen2.5-72B-Instruct",
    "fast": "mistralai/Mistral-7B-Instruct-v0.3",
    "reasoning": "deepseek-ai/DeepSeek-V3-0324",
}

# Fallback models if primary fails
FALLBACK_MODELS = [
    "meta-llama/Llama-3.3-70B-Instruct",
    "mistralai/Mixtral-8x7B-Instruct-v0.1",
    "microsoft/Phi-3-medium-4k-instruct",
    "HuggingFaceH4/zephyr-7b-beta",
]


class HuggingFaceProvider(BaseProvider):
    """HuggingFace Inference API provider — free tier, many models.

    Uses the /v1/chat/completions OpenAI-compatible endpoint
    for maximum compatibility and model support.
    """

    name: str = "huggingface"
    supports_streaming: bool = False
    supports_vision: bool = False

    def __init__(self, api_key: str = "", timeout: float = 30.0):
        super().__init__(api_key=api_key, timeout=timeout)
        self._last_good_model: Optional[str] = None

    async def init(self) -> None:
        self._client = httpx.AsyncClient(
            base_url="https://api-inference.huggingface.co",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(self.timeout, connect=10.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
        logger.info("HuggingFace provider initialized")

    def is_available(self) -> bool:
        return bool(self.api_key)

    async def generate(self, prompt: str, **kwargs) -> AIResponse:
        if not self._client:
            await self.init()
        if not self._client:
            raise ProviderError(self.name, "Not initialized", retryable=False)

        model_key: str = kwargs.get("model_key", "default")
        model: str = kwargs.get("model", TEXT_MODELS.get(model_key, TEXT_MODELS["default"]))
        system_prompt: str = kwargs.get("system_prompt", "")
        temperature: float = kwargs.get("temperature", 0.7)
        max_tokens: int = kwargs.get("max_tokens", 2048)
        messages_history: Optional[List[Dict[str, Any]]] = kwargs.get("messages")

        # Use last good model if available
        if self._last_good_model:
            model = self._last_good_model

        messages = self._build_messages(prompt, system_prompt, messages_history)

        # Try primary model, then fallbacks
        models_to_try = [model]
        for fb in FALLBACK_MODELS:
            if fb != model:
                models_to_try.append(fb)

        last_error = None
        for try_model in models_to_try:
            payload: Dict[str, Any] = {
                "model": try_model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }

            try:
                # Use OpenAI-compatible endpoint on HF
                response = await self._client.post(
                    f"/models/{try_model}/v1/chat/completions",
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()

                if "choices" in data:
                    choice = data["choices"][0]
                    usage = data.get("usage", {})
                    text = choice["message"]["content"]
                else:
                    # Fallback: raw generation response
                    if isinstance(data, list) and data:
                        text = data[0].get("generated_text", "")
                    elif isinstance(data, dict):
                        text = data.get("generated_text", "")
                    else:
                        text = ""
                    usage = {}

                if not text:
                    last_error = ProviderError(self.name, f"Empty response from {try_model}", retryable=True)
                    continue

                self._last_good_model = try_model

                return AIResponse(
                    text=text,
                    provider=self.name,
                    model=f"hf:{try_model}",
                    tokens_used=usage.get("total_tokens", 0),
                    metadata={
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                    },
                )

            except httpx.TimeoutException as exc:
                last_error = ProviderError(self.name, f"Timeout for {try_model}: {exc}", retryable=True)
                continue
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 401:
                    raise ProviderError(self.name, f"Auth failed (HTTP 401)", retryable=False)
                if status == 404:
                    logger.warning(f"HF model {try_model} not found, trying fallback")
                    continue
                if status in (429, 503):
                    last_error = ProviderError(self.name, f"HTTP {status} for {try_model}", retryable=True)
                    continue
                last_error = ProviderError(self.name, f"HTTP {status}: {exc.response.text[:200]}", retryable=status in (500, 502, 504))
                continue
            except ProviderError:
                raise
            except Exception as exc:
                last_error = ProviderError(self.name, f"Unexpected error with {try_model}: {exc}", retryable=True)
                continue

        if last_error:
            raise last_error
        raise ProviderError(self.name, "All HuggingFace models failed", retryable=True)
