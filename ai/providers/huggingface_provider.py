"""HuggingFace Free Inference API — no key needed for many models."""
import logging
from typing import Any, Dict, List, Optional
import httpx
from ai.providers.base import AIResponse, BaseProvider, ProviderError

logger = logging.getLogger(__name__)


class HuggingFaceProvider(BaseProvider):
    """HuggingFace free inference — no API key required for many models."""

    name: str = "huggingface"
    supports_streaming: bool = False

    # Free models that work without API key
    FREE_MODELS = [
        "mistralai/Mistral-7B-Instruct-v0.3",
        "microsoft/DialoGPT-large",
    ]

    def __init__(self, api_key: str = "", timeout: float = 30.0):
        super().__init__(api_key="", timeout=timeout)

    async def init(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=10.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=3),
            headers={"Content-Type": "application/json"},
        )

    def is_available(self) -> bool:
        return True  # Always available (free models)

    async def generate(self, prompt: str, **kwargs) -> AIResponse:
        if not self._client:
            await self.init()
        system_prompt: str = kwargs.get("system_prompt", "")

        # Build simple text prompt (HuggingFace inference API doesn't support chat format for free models)
        full_prompt = ""
        if system_prompt:
            full_prompt = f"{system_prompt}\n\n"
        full_prompt += prompt

        try:
            response = await self._client.post(
                f"https://api-inference.huggingface.co/models/{self.FREE_MODELS[0]}",
                json={"inputs": full_prompt, "parameters": {"max_new_tokens": 200, "temperature": 0.85}},
            )
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list) and data:
                    text = data[0].get("generated_text", "")
                    # Extract only the new part after the prompt
                    if full_prompt in text:
                        text = text[len(full_prompt):].strip()
                    if text and len(text) > 2:
                        return AIResponse(
                            text=text, provider=self.name,
                            model=self.FREE_MODELS[0],
                            tokens_used=0,
                        )
            # If first model fails, just report error
            raise ProviderError(self.name, f"HTTP {response.status_code}", retryable=True)
        except httpx.TimeoutException as exc:
            raise ProviderError(self.name, f"Request timed out: {exc}", retryable=True)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(self.name, f"Unexpected error: {exc}", retryable=True)
