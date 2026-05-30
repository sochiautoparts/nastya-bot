"""Generic OpenAI-compatible provider for Nastya Bot.
Works with: OpenRouter, Groq, Cerebras, SambaNova, Mistral, etc.
"""
import logging
from typing import Any, Dict, List, Optional
import httpx
from ai.providers.base import AIResponse, BaseProvider, ProviderError

logger = logging.getLogger(__name__)


class OpenAICompatProvider(BaseProvider):
    """Generic provider for any OpenAI-compatible API."""

    def __init__(self, name: str, api_key: str, base_url: str, model: str = "", timeout: float = 30.0):
        super().__init__(api_key=api_key, timeout=timeout)
        self._name = name
        self._base_url = base_url
        self._default_model = model

    @property
    def name(self) -> str:
        return self._name

    async def init(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
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

        model: str = kwargs.get("model", self._default_model)
        system_prompt: str = kwargs.get("system_prompt", "")
        temperature: float = kwargs.get("temperature", 0.85)
        max_tokens: int = kwargs.get("max_tokens", 4096)
        messages_history: Optional[List[Dict[str, Any]]] = kwargs.get("messages")

        messages = self._build_messages(prompt, system_prompt, messages_history)

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        try:
            response = await self._client.post("/v1/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()

            choice = data["choices"][0]
            usage = data.get("usage", {})

            return AIResponse(
                text=choice["message"]["content"],
                provider=self._name,
                model=model,
                tokens_used=usage.get("total_tokens", 0),
            )

        except httpx.TimeoutException as exc:
            raise ProviderError(self._name, f"Timeout: {exc}", retryable=True)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            retryable = status in (429, 500, 502, 503, 504)
            raise ProviderError(self._name, f"HTTP {status}", retryable=retryable)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(self._name, f"Error: {exc}", retryable=True)
