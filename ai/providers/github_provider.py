"""GitHub Models Provider — free AI models via GitHub Marketplace.

Uses GH_MODELS_TOKEN for authentication (GITHUB_TOKEN prefix is forbidden by GitHub Actions).
Free tier: GitHub Models provides access to GPT-4o-mini, Llama, etc.
Rate limited but free — great as reliable fallback.
Supports vision via GPT-4o-mini.
"""
import base64
import logging
from typing import Any, Dict, List, Optional

import httpx

from ai.providers.base import AIResponse, BaseProvider, ProviderError

logger = logging.getLogger(__name__)

TEXT_MODELS = {
    "default": "gpt-4o-mini",
    "fast": "gpt-4o-mini",
    "reasoning": "meta-llama-3.1-70b-instruct",
}

VISION_MODEL = "gpt-4o-mini"


class GitHubModelsProvider(BaseProvider):
    """GitHub Models provider — free, reliable, uses GH_MODELS_TOKEN.

    GitHub Models provides free access to various AI models
    through the Azure AI inference API.
    """

    name: str = "github_models"
    supports_vision: bool = True

    def __init__(self, api_key: str = "", timeout: float = 30.0):
        super().__init__(api_key=api_key, timeout=timeout)

    async def init(self) -> None:
        self._client = httpx.AsyncClient(
            base_url="https://models.inference.ai.azure.com",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(self.timeout, connect=5.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )

    async def generate(self, prompt: str, **kwargs) -> AIResponse:
        if not self._client:
            await self.init()

        image_base64 = kwargs.pop("image_base64", None)
        model_key: str = kwargs.get("model_key", "default")
        model: str = kwargs.get("model", TEXT_MODELS.get(model_key, TEXT_MODELS["default"]))
        system_prompt: str = kwargs.get("system_prompt", "")
        temperature: float = kwargs.get("temperature", 0.7)
        max_tokens: int = kwargs.get("max_tokens", 4096)
        messages_history: Optional[List[Dict[str, Any]]] = kwargs.get("messages")

        # Use vision model if image provided
        if image_base64:
            model = VISION_MODEL

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

        payload: Dict[str, Any] = {
            "model": model,
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

            return AIResponse(
                text=choice["message"]["content"],
                provider=self.name,
                model=f"github:{model}",
                tokens_used=usage.get("total_tokens", 0),
                finish_reason=choice.get("finish_reason", ""),
                metadata={
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "vision": bool(image_base64),
                },
            )

        except httpx.TimeoutException as exc:
            raise ProviderError(self.name, f"Request timed out: {exc}", retryable=True)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            retryable = status in (429, 500, 502, 503, 504)
            raise ProviderError(self.name, f"HTTP {status}: {exc.response.text[:200]}", retryable=retryable)
        except Exception as exc:
            raise ProviderError(self.name, f"Unexpected error: {exc}", retryable=True)
