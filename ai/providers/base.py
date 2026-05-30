"""Base AI Provider for Nastya Bot.

FIXED: _build_messages now detects if the last user message in history
matches the current prompt, avoiding DUPLICATE messages in the AI call.
This was causing the AI to see the user's last message TWICE, leading
to confused responses and wasted tokens.
"""
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
    NO_KEY_PROVIDERS = {"pollinations", "chutes"}

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
    def _build_messages(prompt: str, system_prompt: str = "",
                        messages: Optional[List[Dict]] = None) -> List[Dict]:
        """Build message list for OpenAI-compatible APIs.

        CRITICAL FIX: If the last message in history is the same user message
        as the current prompt, we DON'T add it again. This prevents the AI
        from seeing the user's last message TWICE (once from history, once
        from the explicit prompt append).
        """
        result: List[Dict] = []
        if system_prompt:
            result.append({"role": "system", "content": system_prompt})
        if messages:
            result.extend(messages)

        # Check if the last message in history is already the current prompt
        # This happens because we save the user message to DB BEFORE calling AI,
        # so it's included in the history. Don't duplicate it!
        last_is_current = (
            messages
            and len(messages) > 0
            and messages[-1].get("role") == "user"
            and messages[-1].get("content") == prompt
        )
        if not last_is_current:
            result.append({"role": "user", "content": prompt})

        return result
