"""Cloudflare Workers AI Provider — serverless AI via OpenAI-compatible API.

Ported from ai-mega-bot.
Requires: CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID
Free tier: 10,000 neurons/day (enough for thousands of chat requests)
Models: Llama 3.3 70B, Llama 4 Scout, Qwen 2.5 Coder 32B, DeepSeek R1, and more.
"""
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

CHAT_URL_TEMPLATE = "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/chat/completions"


class CloudflareProvider(BaseProvider):
    """Cloudflare Workers AI provider using httpx."""

    name: str = "cloudflare"
    supports_streaming: bool = False
    supports_vision: bool = False

    def __init__(self, api_key: str = "", timeout: float = 30.0, account_id: str = ""):
        super().__init__(api_key=api_key, timeout=timeout)
        self.account_id = account_id or os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")

    async def init(self) -> None:
        if not self.account_id:
            logger.warning("Cloudflare: no ACCOUNT_ID configured")
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(self.timeout, connect=5.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )

    def is_available(self) -> bool:
        """Cloudflare needs both API token and account ID."""
        return bool(self.api_key and self.account_id)

    async def generate(self, prompt: str, **kwargs) -> AIResponse:
        if not self._client:
            await self.init()

        if not self.account_id:
            raise ProviderError(self.name, "No account ID configured", retryable=False)

        model_key: str = kwargs.get("model_key", "default")
        model: str = kwargs.get("model", TEXT_MODELS.get(model_key, TEXT_MODELS["default"]))
        system_prompt: str = kwargs.get("system_prompt", "")
        temperature: float = kwargs.get("temperature", 0.7)
        max_tokens: int = kwargs.get("max_tokens", 4096)
        messages_history: Optional[List[Dict[str, Any]]] = kwargs.get("messages")

        messages = self._build_messages(prompt, system_prompt, messages_history)

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        url = CHAT_URL_TEMPLATE.format(account_id=self.account_id)

        try:
            response = await self._client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()

            # Cloudflare wraps in {success, result, ...}
            if not data.get("success", True) and "result" not in data:
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

            return AIResponse(
                text=text,
                provider=self.name,
                model=model,
                tokens_used=usage.get("total_tokens", 0),
                metadata={
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
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
