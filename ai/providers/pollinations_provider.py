"""Pollinations.ai — FREE, unlimited, no API key!
VERY SHORT timeouts — this provider is unreliable, so we fail fast
and move on to the next provider."""
import logging
import asyncio
from typing import Any, Dict, List, Optional
import httpx
from ai.providers.base import AIResponse, BaseProvider, ProviderError

logger = logging.getLogger(__name__)

# Models to try in order — "openai" (GPT-4o-mini) is most reliable
POLLINATIONS_MODELS = ["openai", "mistral", "openai-large"]


class PollinationsProvider(BaseProvider):
    name: str = "pollinations"

    def __init__(self, api_key: str = "", timeout: float = 12.0):
        super().__init__(api_key="", timeout=timeout)

    async def init(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=5.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=3),
            follow_redirects=True,
            headers={"User-Agent": "NastyaBot/3.0"},
        )

    def is_available(self) -> bool:
        return True

    async def generate(self, prompt: str, **kwargs) -> AIResponse:
        if not self._client:
            await self.init()

        system_prompt: str = kwargs.get("system_prompt", "")
        temperature: float = kwargs.get("temperature", 0.85)
        messages_history: Optional[List[Dict[str, Any]]] = kwargs.get("messages")
        requested_model: str = kwargs.get("model", "")

        messages = self._build_messages(prompt, system_prompt, messages_history)

        models_to_try = [requested_model] if requested_model else POLLINATIONS_MODELS

        last_error = None
        for model in models_to_try:
            try:
                payload = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                }

                response = await self._client.post(
                    "https://text.pollinations.ai/",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
                text = response.text.strip()

                if not text:
                    last_error = ProviderError(self.name, f"Empty from {model}", retryable=True)
                    continue

                return AIResponse(
                    text=text,
                    provider=self.name,
                    model=f"pollinations:{model}",
                    tokens_used=0,
                )

            except httpx.TimeoutException:
                last_error = ProviderError(self.name, f"Timeout on {model}", retryable=True)
                logger.warning(f"Pollinations {model} timeout")
                continue
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                # 429 = rate limited, try next model immediately
                if status == 429:
                    last_error = ProviderError(self.name, f"Rate limited on {model}", retryable=True)
                    logger.warning(f"Pollinations {model} rate limited")
                    continue
                retryable = status in (500, 502, 503, 504)
                last_error = ProviderError(self.name, f"HTTP {status} on {model}", retryable=retryable)
                continue
            except ProviderError:
                raise
            except Exception as exc:
                last_error = ProviderError(self.name, f"Error on {model}: {exc}", retryable=True)
                continue

        if last_error:
            raise last_error
        raise ProviderError(self.name, "All Pollinations models failed", retryable=True)
