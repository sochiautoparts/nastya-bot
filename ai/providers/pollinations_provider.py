"""Pollinations.ai Provider — FREE, no API key needed! ALWAYS available.

Supports:
  - Text generation via OpenAI-compatible POST API
  - Conversation context (history) for multi-turn chat

Pollinations is the ultimate fallback — always available, no key, no limits.
"""
import logging
from typing import Any, Dict, List, Optional
import httpx
from ai.providers.base import AIResponse, BaseProvider, ProviderError

logger = logging.getLogger(__name__)

TEXT_BASE = "https://text.pollinations.ai"

TEXT_MODELS = {
    "default": "openai",      # GPT-4o-mini
    "fast": "mistral",        # Mistral Small
    "reasoning": "deepseek",  # DeepSeek V3
}


class PollinationsProvider(BaseProvider):
    """Pollinations.ai provider — free, no API key required, always available."""

    name: str = "pollinations"
    supports_streaming: bool = False

    def __init__(self, api_key: str = "", timeout: float = 60.0):
        super().__init__(api_key="", timeout=timeout)

    async def init(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=15.0),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            follow_redirects=True,
            headers={"User-Agent": "NastyaBot/8.0"},
        )

    def is_available(self) -> bool:
        return True

    async def generate(self, prompt: str, **kwargs) -> AIResponse:
        if not self._client:
            await self.init()
        model_key: str = kwargs.get("model_key", "default")
        model: str = kwargs.get("model", TEXT_MODELS.get(model_key, TEXT_MODELS["default"]))
        system_prompt: str = kwargs.get("system_prompt", "")
        temperature: float = kwargs.get("temperature", 0.85)
        history: Optional[List[Dict[str, str]]] = kwargs.get("messages")
        messages = self._build_messages(prompt, system_prompt, history)
        payload = {"model": model, "messages": messages, "temperature": temperature}
        try:
            response = await self._client.post(
                f"{TEXT_BASE}/",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            text = response.text
            if not text:
                raise ProviderError(self.name, "Empty text response", retryable=True)
            return AIResponse(
                text=text, provider=self.name, model=f"pollinations:{model}",
                tokens_used=0, metadata={"context_messages": len(messages)},
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(self.name, f"Text generation timed out: {exc}", retryable=True)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            retryable = status in (429, 500, 502, 503, 504)
            raise ProviderError(self.name, f"HTTP {status}: {exc.response.text[:200]}", retryable=retryable)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(self.name, f"Unexpected error: {exc}", retryable=True)
