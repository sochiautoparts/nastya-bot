"""OpenRouter AI Provider - access to free models via single API.

OpenRouter provides access to hundreds of models through one endpoint.
Free tier includes many models: Gemma 4, Nemotron, Llama, Hermes, Qwen, etc.
OpenAI-compatible API - just change base_url.

Rate limits: 50 free requests/day (or 1000/day with $10+ credit on account).

v3.1: Updated free model lists (March 2026) - removed dead models
       (llama-4-scout, qwen2.5-vl-72b, mistral-small-3.1), added new ones.
       Vision confirmed: gemma-4-31b-it, gemma-4-26b-a4b-it, nemotron-nano-12b-v2-vl.
"""
import logging
from typing import Any, Dict, List, Optional

import httpx

from ai.providers.base import AIResponse, BaseProvider, ProviderError

logger = logging.getLogger(__name__)

# Free models on OpenRouter - ordered by quality for Russian conversation
# Rate limit: 50 free requests/day (or 1000/day with $10+ credit)
TEXT_MODELS = {
    "default": "google/gemma-4-31b-it:free",                    # Gemma 4 31B - best free for Russian (262k ctx)
    "fast": "nvidia/nemotron-nano-9b-v2:free",                   # Fast, lightweight (128k ctx)
    "reasoning": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",  # Reasoning model (256k ctx)
    "coding": "qwen/qwen3-coder:free",                           # Code specialist (1M ctx, 262k out)
    "large": "nvidia/nemotron-3-super-120b-a12b:free",           # Largest context (1M ctx, 262k out)
}

# Fallback free models - ordered by quality for Russian conversation
# Only models confirmed to exist on free tier (March 2026)
FALLBACK_MODELS = [
    "google/gemma-4-31b-it:free",                                # Gemma 4 31B - best free for Russian
    "nvidia/nemotron-3-super-120b-a12b:free",                    # 120B MoE, 1M ctx - huge context
    "meta-llama/llama-3.3-70b-instruct:free",                   # Llama 70B - solid Russian
    "nousresearch/hermes-3-llama-3.1-405b:free",                # Hermes 405B - powerful
    "qwen/qwen3-next-80b-a3b-instruct:free",                    # Qwen 80B MoE - good Russian
    "moonshotai/kimi-k2.6:free",                                 # Kimi K2.6 - strong multilingual
    "openai/gpt-oss-120b:free",                                  # GPT-OSS 120B - capable
    "z-ai/glm-4.5-air:free",                                     # GLM 4.5 Air - good Russian
    "google/gemma-4-26b-a4b-it:free",                            # Gemma 4 26B MoE - backup
    "nvidia/nemotron-nano-9b-v2:free",                           # Nano 9B - fast last resort
]

# Vision-capable free models - only confirmed vision support on free tier (March 2026)
# NOTE: llama-4-scout, qwen2.5-vl-72b, mistral-small-3.1 NO LONGER exist on free tier
VISION_MODELS = [
    "google/gemma-4-31b-it:free",             # Gemma 4 31B - excellent vision + text (262k ctx)
    "google/gemma-4-26b-a4b-it:free",         # Gemma 4 26B MoE - backup vision (262k ctx)
    "nvidia/nemotron-nano-12b-v2-vl:free",    # Nemotron VL - dedicated vision model (128k ctx, 128k out)
]


class OpenRouterProvider(BaseProvider):
    """OpenRouter provider - access to many free models via single API.

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
            logger.info(f"Vision request: trying {len(VISION_MODELS)} vision models first")

        # Build model try list: vision models first if image provided
        if image_base64:
            models_to_try = list(VISION_MODELS)
            # Add non-vision fallbacks too in case vision models all fail
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
