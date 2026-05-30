"""Pollinations.ai — FREE, unlimited, no API key needed! 🎀"""
import logging
from typing import Any, Dict, List, Optional
import httpx
from ai.providers.base import AIResponse, BaseProvider, ProviderError

logger = logging.getLogger(__name__)


class PollinationsProvider(BaseProvider):
    name: str = "pollinations"

    def __init__(self, api_key: str = "", timeout: float = 60.0):
        super().__init__(api_key="", timeout=timeout)

    async def init(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=15.0),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            follow_redirects=True,
            headers={"User-Agent": "NastyaBot/1.0"},
        )

    def is_available(self) -> bool:
        return True

    async def generate(self, prompt: str, **kwargs) -> AIResponse:
        if not self._client:
            await self.init()

        model: str = kwargs.get("model", "openai")
        system_prompt: str = kwargs.get("system_prompt", "")
        temperature: float = kwargs.get("temperature", 0.85)
        messages_history: Optional[List[Dict[str, Any]]] = kwargs.get("messages")

        messages = self._build_messages(prompt, system_prompt, messages_history)

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }

        try:
            response = await self._client.post(
                "https://text.pollinations.ai/",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            text = response.text

            if not text:
                raise ProviderError(self.name, "Empty response", retryable=True)

            return AIResponse(
                text=text,
                provider=self.name,
                model=f"pollinations:{model}",
                tokens_used=0,
            )

        except httpx.TimeoutException as exc:
            raise ProviderError(self.name, f"Timeout: {exc}", retryable=True)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            retryable = status in (429, 500, 502, 503, 504)
            raise ProviderError(self.name, f"HTTP {status}", retryable=retryable)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(self.name, f"Error: {exc}", retryable=True)
