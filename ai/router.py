"""AI Router for Nastya Bot — routes to best available AI provider with fallback."""
import logging
import random
from typing import Any, Dict, List, Optional
from ai.providers.base import AIResponse, ProviderError
from ai.providers.pollinations_provider import PollinationsProvider
from ai.providers.openai_compat_provider import OpenAICompatProvider
from bot.config import (
    PROVIDER_CHAIN, PROVIDER_TIMEOUTS,
    OPENROUTER_API_KEY, GROQ_API_KEY, CEREBRAS_API_KEY,
    SAMBANOVA_API_KEY, MISTRAL_API_KEY,
)

logger = logging.getLogger(__name__)


class AllProvidersExhaustedError(Exception):
    pass


# Provider configs: (name, base_url, default_model, api_key)
PROVIDER_CONFIGS = [
    ("openrouter", "https://openrouter.ai", "google/gemma-4-31b-it:free", OPENROUTER_API_KEY),
    ("groq", "https://api.groq.com", "llama-3.3-70b-versatile", GROQ_API_KEY),
    ("cerebras", "https://api.cerebras.ai", "llama-4-scout-17b-16e-instruct", CEREBRAS_API_KEY),
    ("sambanova", "https://api.sambanova.ai", "Meta-Llama-3.3-70B-Instruct", SAMBANOVA_API_KEY),
    ("mistral", "https://api.mistral.ai", "mistral-small-latest", MISTRAL_API_KEY),
]


class AIRouter:
    def __init__(self):
        self.providers: Dict[str, Any] = {}
        self._chain: List[str] = []

    async def init(self) -> None:
        # Always add Pollinations (free, unlimited)
        pollinations = PollinationsProvider(timeout=PROVIDER_TIMEOUTS.get("text", 30.0))
        await pollinations.init()
        self.providers["pollinations"] = pollinations
        logger.info("Provider: pollinations (FREE, unlimited)")

        # Add OpenAI-compatible providers if keys available
        for name, base_url, model, api_key in PROVIDER_CONFIGS:
            if not api_key:
                continue
            provider = OpenAICompatProvider(
                name=name,
                api_key=api_key,
                base_url=base_url,
                model=model,
                timeout=PROVIDER_TIMEOUTS.get("text", 30.0),
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
            self._chain = ["pollinations"]
        logger.info(f"AI chain: {' → '.join(self._chain)}")

    async def close(self) -> None:
        for p in self.providers.values():
            try:
                await p.close()
            except Exception:
                pass

    async def chat(self, prompt: str, system_prompt: str = "", messages: Optional[List[Dict]] = None, **kwargs) -> AIResponse:
        """Route chat request through provider chain."""
        last_error = None
        for provider_name in self._chain:
            provider = self.providers.get(provider_name)
            if not provider:
                continue
            try:
                result = await provider.generate(
                    prompt,
                    system_prompt=system_prompt,
                    messages=messages,
                    **kwargs,
                )
                if result.text:
                    return result
            except ProviderError as e:
                last_error = e
                logger.warning(f"Provider {provider_name} failed: {e}")
                if not e.retryable:
                    break
            except Exception as e:
                last_error = e
                logger.error(f"Error from {provider_name}: {e}")

        raise AllProvidersExhaustedError(str(last_error))
