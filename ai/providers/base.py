"""Base AI Provider for Nastya Bot.

FIXED: _build_messages now detects if the last user message in history
matches the current prompt, avoiding DUPLICATE messages in the AI call.
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
    image_url: str = ""
    image_bytes: bytes = b""
    provider: str = ""
    model: str = ""
    tokens_used: int = 0
    finish_reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseProvider:
    """Base class for all AI providers."""
    name: str = "base"
    supports_streaming: bool = False
    NO_KEY_PROVIDERS = {"pollinations", "chutes", "blackbox", "huggingface"}

    def __init__(self, api_key: str = "", timeout: float = 30.0):
        self.api_key = api_key
        self.timeout = timeout
        self._client: Optional[Any] = None

    async def init(self) -> None:
        """Initialize async resources (httpx client, etc)."""
        pass

    async def close(self) -> None:
        """Cleanup async resources."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def generate(self, prompt: str, **kwargs) -> AIResponse:
        """Generate text response."""
        raise NotImplementedError

    def is_available(self) -> bool:
        """Check if provider has required credentials."""
        if self.name in self.NO_KEY_PROVIDERS:
            return True
        return bool(self.api_key)

    def _build_messages(
        self,
        prompt: str,
        system_prompt: str = "",
        history: Optional[List[Dict[str, str]]] = None,
    ) -> List[Dict[str, str]]:
        """Build messages array with system prompt, history, and current prompt.

        CRITICAL FIX: If the last message in history is the same user message
        as the current prompt, we DON'T add it again.
        """
        messages: List[Dict[str, str]] = []

        # System prompt first
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        # Add conversation history for context memory
        if history:
            for msg in history:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})

        # Check if the last message in history is already the current prompt
        last_is_current = (
            history
            and len(history) > 0
            and history[-1].get("role") == "user"
            and history[-1].get("content") == prompt
        )
        if not last_is_current:
            messages.append({"role": "user", "content": prompt})

        return messages
