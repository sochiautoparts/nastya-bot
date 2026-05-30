"""Blackbox AI Provider — FREE, no API key. Fast and reliable."""
import logging
from typing import Any, Dict, List, Optional
import httpx
from ai.providers.base import AIResponse, BaseProvider, ProviderError

logger = logging.getLogger(__name__)


class BlackboxProvider(BaseProvider):
    """Blackbox AI — free, no API key required."""

    name: str = "blackbox"
    supports_streaming: bool = False

    def __init__(self, api_key: str = "", timeout: float = 30.0):
        super().__init__(api_key="", timeout=timeout)

    async def init(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=10.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
        )

    def is_available(self) -> bool:
        return True

    async def generate(self, prompt: str, **kwargs) -> AIResponse:
        if not self._client:
            await self.init()
        system_prompt: str = kwargs.get("system_prompt", "")
        history: Optional[List[Dict[str, str]]] = kwargs.get("messages")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if history:
            for msg in history:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})
        # Add current prompt if not already in history
        last_is_current = (
            history and len(history) > 0
            and history[-1].get("role") == "user"
            and history[-1].get("content") == prompt
        )
        if not last_is_current:
            messages.append({"role": "user", "content": prompt})

        payload = {
            "messages": messages,
            "agentMode": {},
            "trendingAgentMode": {},
            "isMicMode": False,
            "maxTokens": 1024,
            "isChromeExt": False,
            "githubToken": None,
        }
        try:
            response = await self._client.post(
                "https://www.blackbox.ai/api/chat",
                json=payload,
            )
            response.raise_for_status()
            text = response.text.strip()
            # Blackbox sometimes returns metadata at the end, strip it
            if "$~~~$" in text:
                text = text.split("$~~~$")[0].strip()
            if text and len(text) > 2:
                return AIResponse(
                    text=text, provider=self.name, model="blackbox-free",
                    tokens_used=0, metadata={"context_messages": len(messages)},
                )
            raise ProviderError(self.name, "Empty response from Blackbox", retryable=True)
        except httpx.TimeoutException as exc:
            raise ProviderError(self.name, f"Request timed out: {exc}", retryable=True)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            retryable = status in (429, 500, 502, 503, 504)
            raise ProviderError(self.name, f"HTTP {status}", retryable=retryable)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(self.name, f"Unexpected error: {exc}", retryable=True)
