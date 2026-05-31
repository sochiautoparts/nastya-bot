"""Cloudflare Workers AI Provider — serverless AI via OpenAI-compatible API.

Requires: CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID
Free tier: 10,000 neurons/day (enough for thousands of chat requests)

v3.0: Updated with latest available models, better model fallback chain.
      Cloudflare now has many more models including Llama 4, Qwen 3, etc.
"""
import base64
import logging
import os
from typing import Any, Dict, List, Optional

import httpx

from ai.providers.base import AIResponse, BaseProvider, ProviderError

logger = logging.getLogger(__name__)

# Model chain — try in order, fall back to next on failure
# Updated with latest Cloudflare Workers AI models
TEXT_MODELS = {
    "default": "@cf/meta/llama-4-scout-17b-16e-instruct",
    "fast": "@cf/meta/llama-3.1-8b-instruct-fp8",
    "reasoning": "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b",
    "code": "@cf/qwen/qwen2.5-coder-32b-instruct",
}

# Fallback models to try if default fails — ordered by reliability
FALLBACK_MODELS = [
    "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
    "@cf/meta/llama-4-scout-17b-16e-instruct",
    "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b",
    "@cf/qwen/qwen2.5-coder-32b-instruct",
    "@cf/meta/llama-3.1-8b-instruct-fp8",
    "@cf/mistralai/mistral-small-3.1-24b-instruct",
]

VISION_MODEL = "@cf/meta/llama-3.2-11b-vision-instruct"


class CloudflareProvider(BaseProvider):
    """Cloudflare Workers AI provider using httpx.

    PRIMARY provider — free, reliable, many models, vision support.
    v3.0: Updated model list with latest Cloudflare models.
    """

    name: str = "cloudflare"
    supports_streaming: bool = False
    supports_vision: bool = True

    def __init__(self, api_key: str = "", timeout: float = 30.0, account_id: str = ""):
        super().__init__(api_key=api_key, timeout=timeout)
        self.account_id = account_id or os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
        self._last_good_model: Optional[str] = None

    async def init(self) -> None:
        if not self.account_id:
            logger.warning("Cloudflare: no ACCOUNT_ID configured")
            return
        self._client = httpx.AsyncClient(
            base_url=f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/ai",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(self.timeout, connect=10.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )

    def is_available(self) -> bool:
        """Cloudflare needs both API token and account ID."""
        return bool(self.api_key and self.account_id)

    async def generate(self, prompt: str, **kwargs) -> AIResponse:
        if not self._client:
            await self.init()
        if not self._client:
            raise ProviderError(self.name, "Not initialized (missing account ID or API key)", retryable=False)

        if not self.account_id:
            raise ProviderError(self.name, "No account ID configured", retryable=False)

        image_base64 = kwargs.get("image_base64")  # Read, don't pop — allow fallback
        model_key: str = kwargs.get("model_key", "default")
        model: str = kwargs.get("model", TEXT_MODELS.get(model_key, TEXT_MODELS["default"]))
        system_prompt: str = kwargs.get("system_prompt", "")
        temperature: float = kwargs.get("temperature", 0.7)
        max_tokens: int = kwargs.get("max_tokens", 4096)
        messages_history: Optional[List[Dict[str, Any]]] = kwargs.get("messages")

        # If image provided, use vision model
        if image_base64:
            model = VISION_MODEL

        # Use last good model if available
        if not image_base64 and self._last_good_model:
            model = self._last_good_model

        messages = self._build_messages(prompt, system_prompt, messages_history)

        # Add image content to last user message if vision
        if image_base64 and messages:
            for i in range(len(messages) - 1, -1, -1):
                if messages[i].get("role") == "user":
                    messages[i] = {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
                            {"type": "text", "text": messages[i]["content"] if isinstance(messages[i]["content"], str) else prompt},
                        ]
                    }
                    break

        # Try primary model, then fallbacks
        models_to_try = [model]
        if not image_base64:
            for fb in FALLBACK_MODELS:
                if fb != model:
                    models_to_try.append(fb)

        last_error = None
        for try_model in models_to_try:
            payload: Dict[str, Any] = {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }

            try:
                # Cloudflare uses /v1/chat/completions for OpenAI-compatible
                # or /run/{model} for native format
                response = await self._client.post("/v1/chat/completions", json=payload)
                response.raise_for_status()
                data = response.json()

                # OpenAI-compatible response format
                if "choices" in data:
                    choice = data["choices"][0]
                    usage = data.get("usage", {})
                    text = choice["message"]["content"]
                elif "result" in data and isinstance(data["result"], dict):
                    text = data["result"].get("response", "")
                    usage = {}
                else:
                    # Try to extract text from any format
                    text = ""
                    if isinstance(data, dict):
                        if "response" in data:
                            text = data["response"]
                        elif "result" in data:
                            result = data["result"]
                            if isinstance(result, str):
                                text = result
                            elif isinstance(result, dict):
                                text = result.get("response", "")
                    usage = {}

                if not text:
                    last_error = ProviderError(self.name, f"Empty response from model {try_model}", retryable=True)
                    continue

                # Remember working model
                self._last_good_model = try_model

                return AIResponse(
                    text=text,
                    provider=self.name,
                    model=f"cf:{try_model}",
                    tokens_used=usage.get("total_tokens", 0),
                    metadata={
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                        "vision": bool(image_base64),
                    },
                )

            except httpx.TimeoutException as exc:
                last_error = ProviderError(self.name, f"Timeout for model {try_model}: {exc}", retryable=True)
                continue
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 404:
                    # Model not found — try next model
                    logger.warning(f"CF model {try_model} not found, trying fallback")
                    continue
                if status in (429, 503):
                    # Rate limited or overloaded — try next model
                    last_error = ProviderError(self.name, f"HTTP {status} for {try_model}", retryable=True)
                    continue
                last_error = ProviderError(self.name, f"HTTP {status}: {exc.response.text[:200]}", retryable=status in (500, 502, 504))
                continue
            except ProviderError as exc:
                last_error = exc
                continue
            except Exception as exc:
                last_error = ProviderError(self.name, f"Unexpected error with {try_model}: {exc}", retryable=True)
                continue

        # All models failed
        if last_error:
            raise last_error
        raise ProviderError(self.name, "All Cloudflare models failed", retryable=True)
