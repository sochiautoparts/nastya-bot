"""Blackbox AI Provider — FREE, no API key needed! Unlimited access.

v5.0 FIXED:
  - Better vision message format
  - Proper error handling
  - Strips source citation artifacts
"""
import logging
import re
from typing import Any, Dict, List, Optional

import httpx

from ai.providers.base import AIResponse, BaseProvider, ProviderError

logger = logging.getLogger(__name__)

CHAT_URL = "https://www.blackbox.ai/api/chat"

TEXT_MODELS = {
    "default": "blackboxai",
    "pro": "blackboxai-pro",
    "gpt4o": "gpt-4o",
    "gemini": "gemini-pro",
    "claude": "claude-sonnet-4-20250514",
}

VISION_MODELS = ["blackboxai", "blackboxai-pro", "gpt-4o", "gemini-pro"]


class BlackboxProvider(BaseProvider):
    """Blackbox AI provider — free, no API key required, unlimited."""

    name: str = "blackbox"
    supports_streaming: bool = False
    supports_vision: bool = True

    def __init__(self, api_key: str = "", timeout: float = 30.0):
        super().__init__(api_key="", timeout=timeout)
        self._last_good_model: Optional[str] = None

    async def init(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=15.0),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            follow_redirects=True,
            headers={
                "User-Agent": "NastyaBot/14.0",
                "Content-Type": "application/json",
                "Accept": "*/*",
                "Origin": "https://www.blackbox.ai",
                "Referer": "https://www.blackbox.ai/",
            },
        )
        logger.info("Blackbox provider initialized (free, unlimited, vision supported)")

    def is_available(self) -> bool:
        return True

    async def generate(self, prompt: str, **kwargs) -> AIResponse:
        if not self._client:
            await self.init()

        model_key: str = kwargs.get("model_key", "default")
        model: str = kwargs.get("model", TEXT_MODELS.get(model_key, TEXT_MODELS["default"]))
        system_prompt: str = kwargs.get("system_prompt", "")
        temperature: float = kwargs.get("temperature", 0.7)
        max_tokens: int = kwargs.get("max_tokens", 4096)
        messages_history: Optional[List[Dict[str, Any]]] = kwargs.get("messages")
        image_base64 = kwargs.get("image_base64")

        if self._last_good_model and not image_base64:
            model = self._last_good_model

        messages = self._build_messages(prompt, system_prompt, messages_history)

        # Handle vision via multimodal content
        if image_base64:
            if model not in VISION_MODELS:
                model = "blackboxai"
            for i in range(len(messages) - 1, -1, -1):
                if messages[i].get("role") == "user":
                    content = messages[i].get("content", "")
                    if isinstance(content, str):
                        messages[i] = {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{image_base64}"
                                    },
                                },
                                {"type": "text", "text": content},
                            ],
                        }
                    break

        payload: Dict[str, Any] = {
            "messages": messages,
            "model": model,
            "max_tokens": max_tokens,
        }

        models_to_try = [model]
        for fb_key in ["default", "pro", "gemini", "gpt4o"]:
            fb_model = TEXT_MODELS.get(fb_key, "")
            if fb_model and fb_model not in models_to_try:
                models_to_try.append(fb_model)

        last_error = None
        for try_model in models_to_try:
            payload["model"] = try_model

            try:
                response = await self._client.post(CHAT_URL, json=payload)
                response.raise_for_status()

                text = response.text
                if not text:
                    last_error = ProviderError(self.name, f"Empty response from {try_model}", retryable=True)
                    continue

                text = self._clean_blackbox_response(text)
                if not text:
                    last_error = ProviderError(self.name, f"Empty cleaned response from {try_model}", retryable=True)
                    continue

                self._last_good_model = try_model

                return AIResponse(
                    text=text,
                    provider=self.name,
                    model=f"blackbox:{try_model}",
                    tokens_used=0,
                    metadata={"endpoint": "chat", "vision": bool(image_base64), "model_used": try_model},
                )

            except httpx.TimeoutException as exc:
                last_error = ProviderError(self.name, f"Request timed out for {try_model}: {exc}", retryable=True)
                continue
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                retryable = status in (429, 500, 502, 503, 504)
                last_error = ProviderError(self.name, f"HTTP {status} for {try_model}: {exc.response.text[:200]}", retryable=retryable)
                continue
            except ProviderError:
                raise
            except Exception as exc:
                last_error = ProviderError(self.name, f"Unexpected error with {try_model}: {exc}", retryable=True)
                continue

        if last_error:
            raise last_error
        raise ProviderError(self.name, "All Blackbox models failed", retryable=True)

    @staticmethod
    def _clean_blackbox_response(text: str) -> str:
        """Clean up Blackbox response — strip ads, metadata, artifacts."""
        if not text:
            return ""

        # Strip common Blackbox metadata patterns
        if text.startswith("{") and "\"response\"" in text:
            try:
                import json
                data = json.loads(text)
                text = data.get("response", text)
            except Exception:
                pass

        # Strip "$@~`" type markers
        text = re.sub(r'\$@~\$.*?\$~@\$', '', text, flags=re.DOTALL)

        # Strip source citation blocks
        text = re.sub(r'\[\d+:\s*\d+†source\]', '', text)
        text = re.sub(r'\[\d+†source\]', '', text)

        # Strip "Sources:" sections
        text = re.sub(r'\n*Sources?:\s*.*$', '', text, flags=re.DOTALL | re.IGNORECASE)

        # Strip markdown formatting
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)

        # Clean up multiple newlines
        text = re.sub(r'\n{3,}', '\n\n', text)

        text = text.strip()
        return text
