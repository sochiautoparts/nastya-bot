"""Cloudflare Workers AI Provider — serverless AI via OpenAI-compatible API.

Ported from ai-mega-bot + enhanced with vision support.
Requires: CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID
Free tier: 10,000 neurons/day (enough for thousands of chat requests)
Models: Llama 3.3 70B, Llama 4 Scout, Qwen 2.5 Coder 32B, DeepSeek R1, Llava (vision).

v2.2: Uses base_url like ai-mega-bot (more reliable URL construction).
      Also tries Cloudflare native format as fallback if OpenAI format fails.
"""
import base64
import logging
import os
from typing import Any, Dict, List, Optional

import httpx

from ai.providers.base import AIResponse, BaseProvider, ProviderError

logger = logging.getLogger(__name__)

TEXT_MODELS = {
    "default": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
    "fast": "@cf/meta/llama-3.1-8b-instruct-fp8",
    "reasoning": "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b",
    "code": "@cf/qwen/qwen2.5-coder-32b-instruct",
}

VISION_MODEL = "@cf/meta/llama-3.2-11b-vision-instruct"


class CloudflareProvider(BaseProvider):
    """Cloudflare Workers AI provider using httpx.

    Now with vision support via Llama 3.2 11B Vision.
    Promoted to top of provider chain — free, fast, reliable.
    Uses base_url for cleaner URL construction (like ai-mega-bot).
    """

    name: str = "cloudflare"
    supports_streaming: bool = False
    supports_vision: bool = True

    def __init__(self, api_key: str = "", timeout: float = 30.0, account_id: str = ""):
        super().__init__(api_key=api_key, timeout=timeout)
        self.account_id = account_id or os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")

    async def init(self) -> None:
        if not self.account_id:
            logger.warning("Cloudflare: no ACCOUNT_ID configured")
            return
        self._client = httpx.AsyncClient(
            base_url=f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/ai",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(self.timeout, connect=10.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )

    def is_available(self) -> bool:
        """Cloudflare needs both API token and account ID."""
        return bool(self.api_key and self.account_id)

    async def generate(self, prompt: str, **kwargs) -> AIResponse:
        if not self._client:
            await self.init()
        if not self._client:
            raise ProviderError(self.name, "Not initialized (missing account ID or API key)", retryable=False)

        if not self.account_id:
            raise ProviderError(self.name, "No account ID configured", retryable=False)

        image_base64 = kwargs.pop("image_base64", None)
        model_key: str = kwargs.get("model_key", "default")
        model: str = kwargs.get("model", TEXT_MODELS.get(model_key, TEXT_MODELS["default"]))
        system_prompt: str = kwargs.get("system_prompt", "")
        temperature: float = kwargs.get("temperature", 0.7)
        max_tokens: int = kwargs.get("max_tokens", 4096)
        messages_history: Optional[List[Dict[str, Any]]] = kwargs.get("messages")

        # If image provided, use vision model
        if image_base64:
            model = VISION_MODEL

        messages = self._build_messages(prompt, system_prompt, messages_history)

        # Add image content to last user message if vision
        if image_base64 and messages:
            # Find the last user message and add image content
            for i in range(len(messages) - 1, -1, -1):
                if messages[i].get("role") == "user":
                    messages[i] = {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
                            {"type": "text", "text": messages[i]["content"] if isinstance(messages[i]["content"], str) else prompt},
                        ]
                    }
                    break

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        try:
            # Use base_url path (like ai-mega-bot) — cleaner URL construction
            response = await self._client.post("/v1/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()

            # Cloudflare wraps in {success, result, ...}
            if not data.get("success", True) and "result" not in data and "choices" not in data:
                errors = data.get("errors", [])
                err_msg = errors[0].get("message", "Unknown error") if errors else "Unknown error"
                raise ProviderError(self.name, f"API error: {err_msg}", retryable=True)

            # OpenAI-compatible response format
            if "choices" in data:
                choice = data["choices"][0]
                usage = data.get("usage", {})
                text = choice["message"]["content"]
            elif "result" in data and "response" in data["result"]:
                text = data["result"]["response"]
                usage = {}
            else:
                # Fallback: try to extract text from any reasonable structure
                text = str(data.get("result", {}).get("response", ""))
                usage = {}

            if not text:
                raise ProviderError(self.name, "Empty response from Cloudflare", retryable=True)

            return AIResponse(
                text=text,
                provider=self.name,
                model=model,
                tokens_used=usage.get("total_tokens", 0),
                metadata={
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "vision": bool(image_base64),
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
