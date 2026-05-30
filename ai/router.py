"""AI Router — stable routing with retries and fallback chains.
Supports text, vision (image understanding), and voice transcription.
"""
import logging
import asyncio
from typing import Any, Dict, List, Optional

from ai.providers.base import AIResponse, ProviderError
from ai.providers.chutes_provider import ChutesProvider
from ai.providers.pollinations_provider import PollinationsProvider
from ai.providers.openai_compat_provider import OpenAICompatProvider
from ai.voice import transcribe_voice_ogg
from bot.config import (
    PROVIDER_CHAIN, PROVIDER_TIMEOUTS,
    OPENROUTER_API_KEY, GROQ_API_KEY, CEREBRAS_API_KEY,
    SAMBANOVA_API_KEY, MISTRAL_API_KEY,
)

logger = logging.getLogger(__name__)


class AllProvidersExhaustedError(Exception):
    pass


# (name, base_url, model, api_key)
PROVIDER_CONFIGS = [
    ("groq", "https://api.groq.com", "llama-3.3-70b-versatile", GROQ_API_KEY),
    ("openrouter", "https://openrouter.ai", "google/gemma-4-31b-it:free", OPENROUTER_API_KEY),
    ("cerebras", "https://api.cerebras.ai", "llama-4-scout-17b-16e-instruct", CEREBRAS_API_KEY),
    ("sambanova", "https://api.sambanova.ai", "Meta-Llama-3.3-70B-Instruct", SAMBANOVA_API_KEY),
    ("mistral", "https://api.mistral.ai", "mistral-small-latest", MISTRAL_API_KEY),
]


class AIRouter:
    def __init__(self):
        self.providers: Dict[str, Any] = {}
        self._chain: List[str] = []
        self._vision_providers: List[str] = []

    async def init(self) -> None:
        # Chutes — primary, free, has vision (DeepSeek VL2)
        chutes = ChutesProvider(timeout=PROVIDER_TIMEOUTS.get("text", 30.0))
        await chutes.init()
        self.providers["chutes"] = chutes
        self._vision_providers.append("chutes")
        logger.info("Provider: chutes (DeepSeek V3 + VL2, FREE)")

        # Pollinations — always available fallback
        pollinations = PollinationsProvider(timeout=PROVIDER_TIMEOUTS.get("text", 30.0))
        await pollinations.init()
        self.providers["pollinations"] = pollinations
        logger.info("Provider: pollinations (FREE)")

        # OpenAI-compatible providers with API keys
        for name, base_url, model, api_key in PROVIDER_CONFIGS:
            if not api_key:
                continue
            provider = OpenAICompatProvider(
                name=name, api_key=api_key, base_url=base_url,
                model=model, timeout=PROVIDER_TIMEOUTS.get("text", 30.0),
            )
            if provider.is_available():
                try:
                    await provider.init()
                    self.providers[name] = provider
                    logger.info(f"Provider: {name} ({model})")
                except Exception as exc:
                    logger.error(f"Failed to init {name}: {exc}")

        # Build chain
        self._chain = [p for p in PROVIDER_CHAIN if p in self.providers]
        if not self._chain:
            self._chain = ["chutes", "pollinations"]
        logger.info(f"AI chain: {' -> '.join(self._chain)}")

    async def close(self) -> None:
        for p in self.providers.values():
            try:
                await p.close()
            except Exception:
                pass

    def _build_messages(self, prompt: str, system_prompt: str = "",
                        history: Optional[List[Dict]] = None) -> List[Dict]:
        """Build messages array, avoiding duplicate last user message."""
        messages: List[Dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if history:
            for msg in history:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})
        # Avoid duplicate: if last history message is same as prompt, skip
        last_is_current = (
            history and len(history) > 0
            and history[-1].get("role") == "user"
            and history[-1].get("content") == prompt
        )
        if not last_is_current:
            messages.append({"role": "user", "content": prompt})
        return messages

    async def chat(self, prompt: str, system_prompt: str = "",
                   messages: Optional[List[Dict]] = None, **kwargs) -> AIResponse:
        """Route text chat with fallback and retry."""
        image_base64 = kwargs.pop("image_base64", None)

        # If image provided, try vision providers first
        if image_base64:
            for vp_name in self._vision_providers:
                provider = self.providers.get(vp_name)
                if not provider:
                    continue
                try:
                    result = await provider.generate(
                        prompt, system_prompt=system_prompt,
                        messages=messages, image_base64=image_base64, **kwargs,
                    )
                    if result and result.text:
                        return result
                except Exception as e:
                    logger.warning(f"Vision provider {vp_name} failed: {e}")
                    # Retry once after brief pause
                    try:
                        await asyncio.sleep(1)
                        result = await provider.generate(
                            prompt, system_prompt=system_prompt,
                            messages=messages, image_base64=image_base64, **kwargs,
                        )
                        if result and result.text:
                            return result
                    except Exception as e2:
                        logger.warning(f"Vision retry {vp_name} failed: {e2}")

        # Regular text chain with retry logic
        last_error = None
        for attempt in range(2):  # 2 attempts through the chain
            for provider_name in self._chain:
                provider = self.providers.get(provider_name)
                if not provider:
                    continue
                try:
                    result = await provider.generate(
                        prompt, system_prompt=system_prompt,
                        messages=messages, **kwargs,
                    )
                    if result and result.text:
                        return result
                    logger.warning(f"Provider {provider_name} returned empty, trying next")
                except ProviderError as e:
                    last_error = e
                    logger.warning(f"Provider {provider_name} failed: {e}")
                    if not e.retryable:
                        break
                except Exception as e:
                    last_error = e
                    logger.error(f"Error from {provider_name}: {e}")

            if attempt == 0 and last_error:
                logger.info("First chain attempt failed, retrying...")
                await asyncio.sleep(2)

        raise AllProvidersExhaustedError(str(last_error))

    async def chat_with_image(self, prompt: str, image_base64: str,
                              system_prompt: str = "", **kwargs) -> AIResponse:
        """Chat with image understanding."""
        return await self.chat(prompt=prompt, system_prompt=system_prompt,
                               image_base64=image_base64, **kwargs)

    async def transcribe_voice(self, ogg_bytes: bytes) -> Optional[str]:
        """Transcribe voice message."""
        return await transcribe_voice_ogg(ogg_bytes)
