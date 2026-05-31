"""OpenRouter AI Provider — access to free models via single API.

OpenRouter provides access to hundreds of models through one endpoint.
Free tier includes many models: Gemma 4, Nemotron, Llama, Hermes, Qwen, etc.
OpenAI-compatible API — just change base_url.

v3.0: Updated with latest working free models (June 2026), vision support via Nemotron VL and Gemma 4.
"""
import logging
from typing import Any, Dict, List, Optional

import httpx

from ai.providers.base import AIResponse, BaseProvider, ProviderError

logger = logging.getLogger(__name__)

# Free models on OpenRouter — ordered by quality for Russian conversation
TEXT_MODELS = {
    "default": "google/gemma-4-31b-it:free",                    # Gemma 4 31B — best free for Russian
    "fast": "nvidia/nemotron-nano-9b-v2:free",                   # Fast, lightweight
    "reasoning": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",  # Reasoning model
}

# Fallback free models to try if primary fails
FALLBACK_MODELS = [
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "nousresearch/hermes-3-llama-3.1-405b:free",
    "nvidia/nemotron-3-nano-30b-a3b:free",
    "google/gemma-4-26b-a4b-it:free",
    "nvidia/nemotron-nano-9b-v2:free",
]

# Vision-capable free models
VISION_MODELS = [
    "nvidia/nemotron-nano-12b-v2-vl:free",
    "google/gemma-4-31b-it:free",
]


class OpenRouterProvider(BaseProvider):
    """OpenRouter provider — access to many free models via single API.

    OpenRouter gives us many free models through one endpoint.
    This is the most reliable fallback because even if some models
    go down, others are always available. Supports vision via
    Nemotron VL and Gemma 4 models.
    """

    name: str = "openrouter"
    supports_streaming: bool = False
    supports_vision: bool = True

    def __init__(self, api_key: str = "", timeout: float = 30.0):
        super().__init__(api_key=api_key, timeout=timeout)
        self._last_good_model: Optional[str] = None

    async def init(self) -> None:
        self._client = httpx.AsyncClient(
            base_url="https://openrouter.ai/api/v1",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/sochiautoparts/nastya-bot",
                "X-Title": "Nastya Bot",
            },
            timeout=httpx.Timeout(self.timeout, connect=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        logger.info("OpenRouter provider initialized (Gemma 4 31B primary)")

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
        max_tokens: int = kwargs.get("max_tokens", 4096)
        messages_history: Optional[List[Dict[str, Any]]] = kwargs.get("messages")
        image_base64: Optional[str] = kwargs.get("image_base64")

        # Use last good model if available (but not if we need vision)
        if self._last_good_model and not image_base64:
            model = self._last_good_model

        messages = self._build_messages(prompt, system_prompt, messages_history)

        # If an image is provided, add it to the last user message
        # and prioritize vision-capable models
        if image_base64:
            self._inject_image_into_messages(messages, image_base64)

        # Build model try list: vision models first if image provided
        if image_base64:
            models_to_try = list(VISION_MODELS)
            for fb in FALLBACK_MODELS:
                if fb not in models_to_try:
                    models_to_try.append(fb)
        else:
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
                response = await self._client.post("/chat/completions", json=payload)
                response.raise_for_status()
                data = response.json()

                choice = data["choices"][0]
                usage = data.get("usage", {})
                text = choice["message"]["content"]

                if not text:
                    last_error = ProviderError(self.name, f"Empty response from {try_model}", retryable=True)
                    continue

                self._last_good_model = try_model

                return AIResponse(
                    text=text,
                    provider=self.name,
                    model=f"openrouter:{try_model}",
                    tokens_used=usage.get("total_tokens", 0),
                    finish_reason=choice.get("finish_reason", ""),
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
                if status in (401, 403):
                    raise ProviderError(self.name, f"Auth failed (HTTP {status})", retryable=False)
                if status == 404:
                    logger.warning(f"OpenRouter model {try_model} not found, trying fallback")
                    last_error = ProviderError(self.name, f"Model {try_model} not found", retryable=True)
                    continue
                if status in (429, 503):
                    last_error = ProviderError(self.name, f"Rate limited (HTTP {status}) for {try_model}", retryable=True)
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
        raise ProviderError(self.name, "All OpenRouter models failed", retryable=True)

    @staticmethod
    def _inject_image_into_messages(
        messages: List[Dict[str, Any]], image_base64: str
    ) -> None:
        """Add a base64 image to the last user message for vision models."""
        if not messages:
            return
        # Find the last user message
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                content = messages[i].get("content", "")
                # Convert string content to multimodal format
                if isinstance(content, str):
                    messages[i]["content"] = [
                        {"type": "text", "text": content},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_base64}"
                            },
                        },
                    ]
                elif isinstance(content, list):
                    # Already multimodal, append image
                    content.append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_base64}"
                            },
                        }
                    )
                return
