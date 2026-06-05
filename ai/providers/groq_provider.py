"""Groq Provider - FREE, ultra-fast inference via LPU hardware.

Groq provides the fastest AI inference available - perfect for real-time chat.
Free tier: ~30 RPM, generous daily limits.
Models: Llama 3.3 70B, Llama 4 Scout, Mixtral, Gemma 2, Qwen 3.
OpenAI-compatible API - just change base_url.
"""
import logging
from typing import Any, Dict, List, Optional

import httpx

from ai.providers.base import AIResponse, BaseProvider, ProviderError

logger = logging.getLogger(__name__)

# Models - free tier, ordered by quality for Russian conversation
TEXT_MODELS = {
    "default": "llama-3.3-70b-versatile",       # Best free model, great Russian
    "fast": "llama-3.1-8b-instant",              # Ultra-fast for simple responses
    "reasoning": "deepseek-r1-distill-llama-70b", # Reasoning tasks
}

FALLBACK_MODELS = [
    "llama-3.3-70b-versatile",
    "llama3-70b-8192",
    "mixtral-8x7b-32768",
    "gemma2-9b-it",
]


class GroqProvider(BaseProvider):
    """Groq provider - fastest free inference, OpenAI-compatible.

    Groq's LPU hardware delivers ~100ms response times.
    Excellent for real-time chat - users get instant responses.
    Free tier is generous enough for a Telegram bot.
    """

    name: str = "groq"
    supports_streaming: bool = False
    supports_vision: bool = False

    def __init__(self, api_key: str = "", timeout: float = 30.0):
        super().__init__(api_key=api_key, timeout=timeout)
        self._last_good_model: Optional[str] = None

    async def init(self) -> None:
        self._client = httpx.AsyncClient(
            base_url="https://api.groq.com/openai/v1",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(self.timeout, connect=5.0),
            limits=httpx.Limits(max_connections=15, max_keepalive_connections=5),
        )
        logger.info("Groq provider initialized (Llama 3.3 70B primary)")

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
                    model=f"groq:{try_model}",
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
                    logger.warning(f"Groq model {try_model} not found, trying fallback")
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
        raise ProviderError(self.name, "All Groq models failed", retryable=True)
