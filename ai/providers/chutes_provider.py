"""Chutes.ai — FREE DeepSeek V3 + DeepSeek VL2 vision. No API key needed.
Includes retry with backoff for reliability.
Used as FALLBACK provider after API-key providers."""
import logging
import asyncio
from typing import Any, Dict, List, Optional
import httpx
from ai.providers.base import AIResponse, BaseProvider, ProviderError

logger = logging.getLogger(__name__)

# Updated model names — Chutes changes models frequently
TEXT_MODEL = "deepseek-ai/DeepSeek-V3-0324"
VISION_MODEL = "deepseek-ai/deepseek-vl2"
# Fallback text model if primary is unavailable
FALLBACK_TEXT_MODEL = "deepseek-ai/DeepSeek-R1"


class ChutesProvider(BaseProvider):
    name: str = "chutes"

    def __init__(self, api_key: str = "", timeout: float = 20.0):
        super().__init__(api_key="", timeout=timeout)

    async def init(self) -> None:
        self._client = httpx.AsyncClient(
            base_url="https://llm.chutes.ai",
            timeout=httpx.Timeout(self.timeout, connect=8.0),
            limits=httpx.Limits(max_connections=15, max_keepalive_connections=5),
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

        # Build messages using base class (with dedup fix)
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

        # Try primary model, then fallback
        models_to_try = [model]
        if model == TEXT_MODEL:
            models_to_try.append(FALLBACK_TEXT_MODEL)

        for current_model in models_to_try:
            payload = {
                "model": current_model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": 1024,
            }

            # Retry up to 2 times per model
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
                        model=f"chutes:{current_model}",
                        tokens_used=usage.get("total_tokens", 0),
                    )

                except httpx.TimeoutException:
                    if attempt == 0:
                        logger.warning(f"Chutes timeout on {current_model}, retrying...")
                        await asyncio.sleep(2)
                        continue
                    raise ProviderError(self.name, f"Timeout after retry on {current_model}", retryable=True)
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    retryable = status in (429, 500, 502, 503, 504)
                    if retryable and attempt == 0:
                        logger.warning(f"Chutes HTTP {status} on {current_model}, retrying...")
                        await asyncio.sleep(2)
                        continue
                    # If primary model fails with 404, try fallback model
                    if status == 404 and current_model != models_to_try[-1]:
                        logger.warning(f"Chutes model {current_model} not found, trying fallback")
                        break  # Break inner loop, try next model
                    raise ProviderError(self.name, f"HTTP {status} on {current_model}", retryable=retryable)
                except ProviderError:
                    raise
                except Exception as exc:
                    if attempt == 0:
                        logger.warning(f"Chutes error on {current_model}, retrying: {exc}")
                        await asyncio.sleep(2)
                        continue
                    raise ProviderError(self.name, f"Error on {current_model}: {exc}", retryable=True)

        raise ProviderError(self.name, "All Chutes models failed", retryable=True)
