"""Chutes.ai — FREE, unlimited, no API key. DeepSeek V3 + DeepSeek VL2 vision.

v4.0: DeepSeek V3 as primary — excellent for Russian language conversation.
      DeepSeek R1 for reasoning tasks.
"""
import logging
from typing import Any, Dict, List, Optional

import httpx

from ai.providers.base import AIResponse, BaseProvider, ProviderError

logger = logging.getLogger(__name__)

TEXT_MODELS = {
    "default": "deepseek-ai/DeepSeek-V3-0324",
    "fast": "Qwen/Qwen3-32B",
    "reasoning": "deepseek-ai/DeepSeek-R1-0528",
}

VISION_MODEL = "deepseek-ai/deepseek-vl2"


class ChutesProvider(BaseProvider):
    """Chutes.ai provider — free, no API key required, supports vision.

    v4.0: DeepSeek V3 primary — best free model for Russian conversation.
    """

    name: str = "chutes"
    supports_vision: bool = True

    def __init__(self, api_key: str = "", timeout: float = 30.0):
        super().__init__(api_key="", timeout=timeout)

    async def init(self) -> None:
        self._client = httpx.AsyncClient(
            base_url="https://llm.chutes.ai",
            timeout=httpx.Timeout(self.timeout, connect=5.0),
            limits=httpx.Limits(max_connections=15, max_keepalive_connections=5),
            follow_redirects=True,
            headers={"Content-Type": "application/json", "User-Agent": "NastyaBot/10.0"},
        )
        logger.info("Chutes provider initialized (DeepSeek V3 primary, vision supported)")

    def is_available(self) -> bool:
        return True

    async def generate(self, prompt: str, **kwargs) -> AIResponse:
        if not self._client:
            await self.init()

        model_key = kwargs.get("model_key", "default")
        model = kwargs.get("model", TEXT_MODELS.get(model_key, TEXT_MODELS["default"]))
        system_prompt = kwargs.get("system_prompt", "")
        temperature = kwargs.get("temperature", 0.7)
        max_tokens = kwargs.get("max_tokens", 2048)
        messages_history = kwargs.get("messages")
        image_base64 = kwargs.get("image_base64")  # Read, don't pop — allow fallback

        messages = self._build_messages(prompt, system_prompt, messages_history)

        # If image, use vision model with multimodal content
        if image_base64:
            model = VISION_MODEL
            messages[-1] = {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
                    {"type": "text", "text": prompt},
                ]
            }

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
            text = choice["message"]["content"]
            if not text:
                raise ProviderError(self.name, "Empty response", retryable=True)
            return AIResponse(
                text=text, provider=self.name, model=f"chutes:{model}",
                tokens_used=usage.get("total_tokens", 0),
                metadata={
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                },
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(self.name, f"Request timed out: {exc}", retryable=True)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            retryable = status in (429, 500, 502, 503, 504)
            raise ProviderError(self.name, f"HTTP {status}: {exc.response.text[:200]}", retryable=retryable)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(self.name, f"Unexpected error: {exc}", retryable=True)
