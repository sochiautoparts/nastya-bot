"""DeepSeek API Provider — PRIORITY #1 for best Russian conversation quality.

DeepSeek V3 is the best model for Russian language conversation:
- Natural, colloquial Russian output
- Excellent context understanding
- Low hallucination rate
- Great personality adherence

API: OpenAI-compatible at https://api.deepseek.com
Models:
  - deepseek-chat (DeepSeek V3) — primary, best quality
  - deepseek-reasoner (DeepSeek R1) — reasoning tasks
"""
import logging
from typing import Any, Dict, List, Optional

import httpx

from ai.providers.base import AIResponse, BaseProvider, ProviderError

logger = logging.getLogger(__name__)

TEXT_MODELS = {
    "default": "deepseek-chat",      # DeepSeek V3 — best for Russian
    "fast": "deepseek-chat",         # Same — it's fast enough
    "reasoning": "deepseek-reasoner", # DeepSeek R1 for complex reasoning
}


class DeepSeekProvider(BaseProvider):
    """DeepSeek API provider — PRIORITY #1 for Nastya Bot.

    DeepSeek V3 produces the most natural Russian conversation,
    perfectly matches Nastya's personality, and has great context memory.
    This should be the FIRST provider in the chain.
    """

    name: str = "deepseek"
    supports_streaming: bool = False
    supports_vision: bool = False  # DeepSeek doesn't support vision yet

    def __init__(self, api_key: str = "", timeout: float = 30.0):
        super().__init__(api_key=api_key, timeout=timeout)
        self._last_good_model: Optional[str] = None

    async def init(self) -> None:
        self._client = httpx.AsyncClient(
            base_url="https://api.deepseek.com",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(self.timeout, connect=5.0),
            limits=httpx.Limits(max_connections=15, max_keepalive_connections=5),
        )
        logger.info("DeepSeek provider initialized (DeepSeek V3 primary)")

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
        temperature: float = kwargs.get("temperature", 0.75)  # Slightly higher for Nastya's personality
        max_tokens: int = kwargs.get("max_tokens", 2048)
        messages_history: Optional[List[Dict[str, Any]]] = kwargs.get("messages")

        # Use last good model if available (avoids trying failed models)
        if self._last_good_model:
            model = self._last_good_model

        messages = self._build_messages(prompt, system_prompt, messages_history)

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "top_p": 0.9,
            "frequency_penalty": 0.3,  # Reduce repetition
            "presence_penalty": 0.3,   # Encourage diverse vocabulary
        }

        try:
            response = await self._client.post("/v1/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()
            choice = data["choices"][0]
            usage = data.get("usage", {})
            text = choice["message"]["content"]

            if not text:
                raise ProviderError(self.name, "Empty response", retryable=True)

            self._last_good_model = model

            return AIResponse(
                text=text,
                provider=self.name,
                model=f"deepseek:{model}",
                tokens_used=usage.get("total_tokens", 0),
                finish_reason=choice.get("finish_reason", ""),
                metadata={
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                },
            )

        except httpx.TimeoutException as exc:
            raise ProviderError(self.name, f"Request timed out: {exc}", retryable=True)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (401, 403):
                raise ProviderError(self.name, f"Auth failed (HTTP {status})", retryable=False)
            if status == 402:
                # Insufficient balance — NOT retryable, skip to next provider
                raise ProviderError(self.name, f"Insufficient balance (HTTP 402) — top up at platform.deepseek.com", retryable=False)
            retryable = status in (429, 500, 502, 503, 504)
            raise ProviderError(self.name, f"HTTP {status}: {exc.response.text[:200]}", retryable=retryable)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(self.name, f"Unexpected error: {exc}", retryable=True)
