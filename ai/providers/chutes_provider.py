"""Chutes.ai — FREE DeepSeek V3 + DeepSeek VL2 vision. No API key needed.
Includes retry with backoff for reliability."""
import logging
import asyncio
from typing import Any, Dict, List, Optional
import httpx
from ai.providers.base import AIResponse, BaseProvider, ProviderError

logger = logging.getLogger(__name__)

TEXT_MODEL = "deepseek-ai/DeepSeek-V3-0324"
VISION_MODEL = "deepseek-ai/deepseek-vl2"


class ChutesProvider(BaseProvider):
    name: str = "chutes"
    NO_KEY_PROVIDERS = {"chutes"}

    def __init__(self, api_key: str = "", timeout: float = 45.0):
        super().__init__(api_key="", timeout=timeout)

    async def init(self) -> None:
        self._client = httpx.AsyncClient(
            base_url="https://llm.chutes.ai",
            timeout=httpx.Timeout(self.timeout, connect=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            follow_redirects=True,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "NastyaBot/3.0",
            },
        )
        logger.info("Chutes provider initialized")

    def is_available(self) -> bool:
        return True

    async def generate(self, prompt: str, **kwargs) -> AIResponse:
        if not self._client:
            await self.init()

        system_prompt: str = kwargs.get("system_prompt", "")
        temperature: float = kwargs.get("temperature", 0.8)
        messages_history: Optional[List[Dict[str, Any]]] = kwargs.get("messages")
        image_base64: Optional[str] = kwargs.get("image_base64")

        # Build messages
        messages = self._build_messages(prompt, system_prompt, messages_history)

        # If image, use vision model with multimodal content
        model = TEXT_MODEL
        if image_base64:
            model = VISION_MODEL
            messages[-1] = {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
                    {"type": "text", "text": prompt},
                ]
            }

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 1024,
        }

        # Retry up to 2 times
        for attempt in range(2):
            try:
                response = await self._client.post("/v1/chat/completions", json=payload)
                response.raise_for_status()
                data = response.json()

                choice = data["choices"][0]
                text = choice["message"]["content"]
                usage = data.get("usage", {})

                if not text:
                    raise ProviderError(self.name, "Empty response", retryable=True)

                return AIResponse(
                    text=text,
                    provider=self.name,
                    model=f"chutes:{model}",
                    tokens_used=usage.get("total_tokens", 0),
                )

            except httpx.TimeoutException:
                if attempt == 0:
                    logger.warning("Chutes timeout, retrying...")
                    await asyncio.sleep(2)
                    continue
                raise ProviderError(self.name, "Timeout after retry", retryable=True)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                retryable = status in (429, 500, 502, 503, 504)
                if retryable and attempt == 0:
                    logger.warning(f"Chutes HTTP {status}, retrying...")
                    await asyncio.sleep(2)
                    continue
                raise ProviderError(self.name, f"HTTP {status}", retryable=retryable)
            except ProviderError:
                raise
            except Exception as exc:
                if attempt == 0:
                    logger.warning(f"Chutes error, retrying: {exc}")
                    await asyncio.sleep(2)
                    continue
                raise ProviderError(self.name, f"Error: {exc}", retryable=True)

    @staticmethod
    def _build_messages(prompt: str, system_prompt: str = "",
                        history: Optional[List[Dict]] = None) -> List[Dict]:
        messages: List[Dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if history:
            for msg in history:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})
        # Avoid duplicate
        last_is_current = (
            history and len(history) > 0
            and history[-1].get("role") == "user"
            and history[-1].get("content") == prompt
        )
        if not last_is_current:
            messages.append({"role": "user", "content": prompt})
        return messages
