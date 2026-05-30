"""Base AI Provider for Nastya Bot."""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class ProviderError(Exception):
    def __init__(self, provider: str, message: str, retryable: bool = True):
        self.provider = provider
        self.retryable = retryable
        super().__init__(f"[{provider}] {message}")


@dataclass
class AIResponse:
    text: str = ""
    provider: str = ""
    model: str = ""
    tokens_used: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseProvider:
    name: str = "base"
    NO_KEY_PROVIDERS = {"pollinations"}

    def __init__(self, api_key: str = "", timeout: float = 30.0):
        self.api_key = api_key
        self.timeout = timeout
        self._client = None

    async def init(self) -> None:
        pass

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def generate(self, prompt: str, **kwargs) -> AIResponse:
        raise NotImplementedError

    def is_available(self) -> bool:
        if self.name in self.NO_KEY_PROVIDERS:
            return True
        return bool(self.api_key)

    @staticmethod
    def _build_messages(prompt: str, system_prompt: str = "", messages: Optional[List[Dict]] = None) -> List[Dict]:
        result: List[Dict] = []
        if system_prompt:
            result.append({"role": "system", "content": system_prompt})
        if messages:
            result.extend(messages)
        result.append({"role": "user", "content": prompt})
        return result
