"""GitHub Models Provider — free AI models via GitHub Marketplace.

Uses GH_MODELS_TOKEN for authentication.
Free tier: GitHub Models provides access to GPT-4o-mini, DeepSeek, Llama, etc.
Rate limited but free — great as reliable fallback.
Supports vision via GPT-4o-mini.

v4.0: DeepSeek as PRIMARY model! DeepSeek-V3 first for best quality,
      then DeepSeek-R1 for reasoning, then GPT-4o-mini fallback.
      DeepSeek models are free on GitHub Models and excellent for Russian.

IMPORTANT: The PAT needs the 'models' permission to access this API.
If the token doesn't have 'models' scope, this provider will be skipped
with a clear warning. The user must create a fine-grained PAT with
'GitHub Models' permission enabled.
"""
import logging
from typing import Any, Dict, List, Optional

import httpx

from ai.providers.base import AIResponse, BaseProvider, ProviderError

logger = logging.getLogger(__name__)

TEXT_MODELS = {
    "default": "DeepSeek-V3-0324",           # DeepSeek V3 — best for Russian, primary!
    "fast": "DeepSeek-V3-0324",               # Same DeepSeek for fast
    "reasoning": "DeepSeek-R1-0528",          # DeepSeek R1 for complex reasoning
}

VISION_MODEL = "gpt-4o-mini"

# Alternative model names to try if default fails
FALLBACK_MODELS = [
    "Meta-Llama-3.1-405B-Instruct",
    "Mistral-large",
    "gpt-4o-mini",
]


class GitHubModelsProvider(BaseProvider):
    """GitHub Models provider — free, reliable, uses GH_MODELS_TOKEN.

    GitHub Models provides free access to various AI models
    through the Azure AI inference API.

    v4.0: DeepSeek as primary model — best quality for Russian language,
          excellent conversation skills, free on GitHub Models.

    NOTE: Requires PAT with 'models' permission.
    If you get 'unauthorized' errors, create a new fine-grained PAT
    with the 'GitHub Models' permission enabled at:
    https://github.com/settings/tokens?type=beta
    """

    name: str = "github_models"
    supports_vision: bool = True

    def __init__(self, api_key: str = "", timeout: float = 30.0):
        super().__init__(api_key=api_key, timeout=timeout)
        self._auth_failed: bool = False

    async def init(self) -> None:
        self._client = httpx.AsyncClient(
            base_url="https://models.inference.ai.azure.com",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(self.timeout, connect=5.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )

    async def generate(self, prompt: str, **kwargs) -> AIResponse:
        if not self._client:
            await self.init()

        # If auth previously failed, don't keep retrying
        if self._auth_failed:
            raise ProviderError(self.name, "Auth previously failed (PAT needs 'models' permission)", retryable=False)

        image_base64 = kwargs.get("image_base64")
        model_key: str = kwargs.get("model_key", "default")
        model: str = kwargs.get("model", TEXT_MODELS.get(model_key, TEXT_MODELS["default"]))
        system_prompt: str = kwargs.get("system_prompt", "")
        temperature: float = kwargs.get("temperature", 0.7)
        max_tokens: int = kwargs.get("max_tokens", 4096)
        messages_history: Optional[List[Dict[str, Any]]] = kwargs.get("messages")

        # Use vision model if image provided
        if image_base64:
            model = VISION_MODEL

        messages = self._build_messages(prompt, system_prompt, messages_history)

        # Add image content to last user message if vision
        if image_base64 and messages:
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

        # Try primary model, then fallbacks
        models_to_try = [model]
        if not image_base64:
            for fb in FALLBACK_MODELS:
                if fb != model:
                    models_to_try.append(fb)

        last_error = None
        for try_model in models_to_try:
            payload: Dict[str, Any] = {
                "model": try_model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }

            try:
                response = await self._client.post("/chat/completions", json=payload)
                response.raise_for_status()
                data = response.json()

                choice = data["choices"][0]
                usage = data.get("usage", {})

                return AIResponse(
                    text=choice["message"]["content"],
                    provider=self.name,
                    model=f"github:{try_model}",
                    tokens_used=usage.get("total_tokens", 0),
                    finish_reason=choice.get("finish_reason", ""),
                    metadata={
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                        "vision": bool(image_base64),
                    },
                )

            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 401 or status == 403:
                    # Auth failure — PAT doesn't have 'models' permission
                    self._auth_failed = True
                    logger.error(
                        f"GitHub Models auth failed (HTTP {status}). "
                        f"PAT needs 'models' permission! Create a fine-grained PAT at "
                        f"https://github.com/settings/tokens?type=beta with 'GitHub Models' enabled."
                    )
                    raise ProviderError(self.name, f"Auth failed (HTTP {status}) — PAT needs 'models' permission", retryable=False)
                if status == 404:
                    logger.warning(f"GitHub model {try_model} not found, trying fallback")
                    last_error = ProviderError(self.name, f"Model {try_model} not found", retryable=True)
                    continue
                last_error = ProviderError(self.name, f"HTTP {status}: {exc.response.text[:200]}", retryable=status in (429, 500, 502, 503, 504))
                continue
            except httpx.TimeoutException as exc:
                last_error = ProviderError(self.name, f"Request timed out for {try_model}: {exc}", retryable=True)
                continue
            except Exception as exc:
                last_error = ProviderError(self.name, f"Unexpected error with {try_model}: {exc}", retryable=True)
                continue

        if last_error:
            raise last_error
        raise ProviderError(self.name, "All GitHub models failed", retryable=True)
