"""Base AI Provider - unified interface for all providers.

Ported from ai-mega-bot with fixes:
  - _build_messages uses 'messages' kwarg (matching how router passes history)
  - Proper dedup: if last history message matches prompt, don't add again
  - Consistent interface across all providers
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class ProviderError(Exception):
    """Provider-specific error with retry info."""

    def __init__(self, provider: str, message: str, retryable: bool = True):
        self.provider = provider
        self.retryable = retryable
        super().__init__(f"[{provider}] {message}")


@dataclass
class AIResponse:
    """Unified AI response."""
    text: str = ""
    image_url: str = ""
    image_bytes: bytes = b""
    provider: str = ""
    model: str = ""
    tokens_used: int = 0
    finish_reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseProvider:
    """Base class for all AI providers.

    All providers use httpx.AsyncClient for maximum performance.
    Connection pooling is configured per provider.
    """

    name: str = "base"
    supports_streaming: bool = False
    supports_vision: bool = False

    # Providers that work without API keys
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
        """Generate text response.

        Args:
            prompt: The user's current message text
            **kwargs: Additional options including:
                messages: List[Dict] - conversation history with role/content
                system_prompt: str - system instructions
                model: str - model override
                temperature: float
                max_tokens: int
        """
        raise NotImplementedError

    def is_available(self) -> bool:
        """Check if provider has required credentials."""
        if self.name in self.NO_KEY_PROVIDERS:
            return True
        return bool(self.api_key)

    @staticmethod
    def _build_messages(
        prompt: str,
        system_prompt: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Build OpenAI-compatible messages array with history.

        This matches ai-mega-bot's approach exactly:
        - System prompt first
        - Then conversation history (role/content pairs)
        - Then current user message (dedup check)
        - Merge consecutive same-role messages (some providers reject them)
        - Flatten list content to string for non-vision providers

        Args:
            prompt: Current user message
            system_prompt: System instructions
            messages: Conversation history (list of {role, content} dicts)

        Returns:
            Complete messages array for API call
        """
        result: List[Dict[str, Any]] = []
        if system_prompt:
            result.append({"role": "system", "content": system_prompt})
        # Add conversation history (previous messages)
        if messages:
            for msg in messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role in ("user", "assistant", "system") and content:
                    result.append({"role": role, "content": content})
        # Check if the last message in history is already the current prompt
        last_is_current = (
            messages
            and len(messages) > 0
            and messages[-1].get("role") == "user"
            and messages[-1].get("content") == prompt
        )
        if not last_is_current:
            result.append({"role": "user", "content": prompt})

        # ── Merge consecutive same-role messages ──
        # Some providers (e.g., OpenAI-compatible) reject messages with
        # two "user" or two "assistant" messages in a row.
        merged: List[Dict[str, Any]] = []
        for msg in result:
            if merged and merged[-1].get("role") == msg.get("role"):
                # Merge content: concatenate with newline
                prev_content = merged[-1].get("content", "")
                new_content = msg.get("content", "")
                # Handle list content (vision) vs string content
                if isinstance(prev_content, list):
                    # Convert list to string before merging
                    text_parts = []
                    for part in prev_content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text_parts.append(part.get("text", ""))
                        elif isinstance(part, str):
                            text_parts.append(part)
                    prev_content = " ".join(text_parts)
                if isinstance(new_content, list):
                    text_parts = []
                    for part in new_content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text_parts.append(part.get("text", ""))
                        elif isinstance(part, str):
                            text_parts.append(part)
                    new_content = " ".join(text_parts)
                merged[-1]["content"] = f"{prev_content}\n{new_content}"
            else:
                merged.append(msg)
        # Ensure alternating roles: system -> (user|assistant)* with no consecutive same-role
        # If still have consecutive same-role after merge (shouldn't happen), skip extras
        result = merged

        return result
