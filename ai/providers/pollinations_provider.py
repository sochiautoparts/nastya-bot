"""Pollinations.ai Provider — FREE, no API key needed! ALWAYS available.

Ported from ai-mega-bot.
Supports:
  - Text generation via OpenAI-compatible POST API with history
  - Vision (image understanding) via openai model

Pollinations is the ultimate fallback — always available, no key, no limits.
"""
import base64
import logging
from typing import Any, Dict, List, Optional

import httpx

from ai.providers.base import AIResponse, BaseProvider, ProviderError

logger = logging.getLogger(__name__)

TEXT_BASE = "https://text.pollinations.ai"

TEXT_MODELS = {
    "default": "openai",      # GPT-4o-mini — good Russian + vision
    "fast": "mistral",        # Mistral Small — fast responses
    "reasoning": "deepseek",  # DeepSeek V3 — reasoning tasks
    "vision": "openai",       # openai model supports vision
    "qwen": "qwen",           # Qwen — excellent for multilingual/Russian
}


class PollinationsProvider(BaseProvider):
    """Pollinations.ai provider — free, no API key required, always available."""

    name: str = "pollinations"
    supports_streaming: bool = False
    supports_vision: bool = True  # via openai model

    def __init__(self, api_key: str = "", timeout: float = 30.0):
        super().__init__(api_key="", timeout=timeout)

    async def init(self) -> None:
        """Initialize httpx async client with connection pooling."""
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=15.0),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            follow_redirects=True,
            headers={"User-Agent": "NastyaBot/9.0"},
        )

    def is_available(self) -> bool:
        """Pollinations is always available — no key needed."""
        return True

    async def generate(self, prompt: str, **kwargs) -> AIResponse:
        """Generate text via Pollinations OpenAI-compatible POST API with history."""
        if not self._client:
            await self.init()

        model_key: str = kwargs.get("model_key", "default")
        model: str = kwargs.get("model", TEXT_MODELS.get(model_key, TEXT_MODELS["default"]))
        system_prompt: str = kwargs.get("system_prompt", "")
        temperature: float = kwargs.get("temperature", 0.7)
        messages_history: Optional[List[Dict[str, Any]]] = kwargs.get("messages")
        image_base64 = kwargs.get("image_base64")

        messages = self._build_messages(prompt, system_prompt, messages_history)

        # Handle vision via multimodal content
        if image_base64 and messages:
            model = TEXT_MODELS["vision"]
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
        }

        try:
            response = await self._client.post(
                f"{TEXT_BASE}/",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()

            text = response.text

            if not text:
                raise ProviderError(
                    self.name,
                    "Empty text response from Pollinations",
                    retryable=True,
                )

            return AIResponse(
                text=text,
                provider=self.name,
                model=f"pollinations:{model}",
                tokens_used=0,
                metadata={"endpoint": "text_post", "vision": bool(image_base64)},
            )

        except httpx.TimeoutException as exc:
            raise ProviderError(self.name, f"Text generation timed out: {exc}", retryable=True)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            retryable = status in (429, 500, 502, 503, 504)
            raise ProviderError(
                self.name,
                f"HTTP {status}: {exc.response.text[:200]}",
                retryable=retryable,
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(self.name, f"Unexpected error: {exc}", retryable=True)

    async def generate_with_vision(
        self,
        prompt: str,
        image_data: bytes = b"",
        image_url: str = "",
        **kwargs,
    ) -> AIResponse:
        """Generate response with image understanding via Pollinations openai model."""
        if not self._client:
            await self.init()

        system_prompt: str = kwargs.get("system_prompt", "")
        temperature: float = kwargs.get("temperature", 0.7)

        content_parts: List[Dict[str, Any]] = []
        if image_data:
            b64 = base64.b64encode(image_data).decode("utf-8")
            content_parts.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{b64}"
                },
            })
        elif image_url:
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": image_url},
            })
        content_parts.append({"type": "text", "text": prompt})

        messages: List[Dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": content_parts})

        payload: Dict[str, Any] = {
            "model": TEXT_MODELS["vision"],
            "messages": messages,
            "temperature": temperature,
        }

        try:
            response = await self._client.post(
                f"{TEXT_BASE}/",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            text = response.text

            if not text:
                raise ProviderError(
                    self.name,
                    "Empty vision response from Pollinations",
                    retryable=True,
                )

            return AIResponse(
                text=text,
                provider=self.name,
                model=f"pollinations:{TEXT_MODELS['vision']}",
                tokens_used=0,
                metadata={"vision": True, "endpoint": "text_post"},
            )

        except httpx.TimeoutException as exc:
            raise ProviderError(self.name, f"Vision request timed out: {exc}", retryable=True)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            retryable = status in (429, 500, 502, 503, 504)
            raise ProviderError(
                self.name,
                f"Vision HTTP {status}: {exc.response.text[:200]}",
                retryable=retryable,
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(self.name, f"Vision error: {exc}", retryable=True)
