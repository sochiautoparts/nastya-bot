"""OpenRouter AI Provider — access to multiple free models with conversation context."""
import logging
from typing import Any, Dict, List, Optional
import httpx
from ai.providers.base import AIResponse, BaseProvider, ProviderError

logger = logging.getLogger(__name__)

FREE_MODELS = {
    "default": "google/gemma-4-31b-it:free",
    "fast": "qwen/qwen3-coder:free",
    "reasoning": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "code": "qwen/qwen3-coder:free",
}


class OpenRouterProvider(BaseProvider):
    name: str = "openrouter"

    def __init__(self, api_key: str = "", timeout: float = 30.0):
        super().__init__(api_key=api_key, timeout=timeout)

    async def init(self) -> None:
        self._client = httpx.AsyncClient(
            base_url="https://openrouter.ai",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/sochiautoparts/nastya-bot",
                "X-Title": "Nastya Bot",
            },
            timeout=httpx.Timeout(self.timeout, connect=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    async def generate(self, prompt: str, **kwargs) -> AIResponse:
        if not self._client:
            await self.init()
        model_key = kwargs.get("model_key", "default")
        model = kwargs.get("model", FREE_MODELS.get(model_key, FREE_MODELS["default"]))
        system_prompt = kwargs.get("system_prompt", "")
        temperature = kwargs.get("temperature", 0.85)
        max_tokens = kwargs.get("max_tokens", 4096)
        history = kwargs.get("messages")
        messages = self._build_messages(prompt, system_prompt, history)
        payload = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        try:
            response = await self._client.post("/api/v1/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()
            choice = data["choices"][0]
            usage = data.get("usage", {})
            return AIResponse(
                text=choice["message"]["content"], provider=self.name, model=model,
                tokens_used=usage.get("total_tokens", 0), finish_reason=choice.get("finish_reason", ""),
                metadata={"context_messages": len(messages)},
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(self.name, f"Request timed out: {exc}", retryable=True)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            retryable = status in (429, 500, 502, 503, 504)
            raise ProviderError(self.name, f"HTTP {status}: {exc.response.text[:200]}", retryable=retryable)
        except Exception as exc:
            raise ProviderError(self.name, f"Unexpected error: {exc}", retryable=True)
