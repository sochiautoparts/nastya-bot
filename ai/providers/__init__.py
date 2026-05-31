"""AI Providers — all available providers for Nastya Bot.

Based on ai-mega-bot stable architecture:
  - API-key providers FIRST (reliable, fast)
  - Free providers as fallback (Pollinations, Chutes)
  - NO Grok (only 2 requests, useless)
  - NO Blackbox (unstable)
  - NO HuggingFace text (doesn't support chat format properly)
"""
from ai.providers.base import BaseProvider, AIResponse, ProviderError
from ai.providers.groq_provider import GroqProvider
from ai.providers.cerebras_provider import CerebrasProvider
from ai.providers.openrouter_provider import OpenRouterProvider
from ai.providers.sambanova_provider import SambaNovaProvider
from ai.providers.mistral_provider import MistralProvider
from ai.providers.gemini_provider import GeminiProvider
from ai.providers.cloudflare_provider import CloudflareProvider
from ai.providers.pollinations_provider import PollinationsProvider
from ai.providers.chutes_provider import ChutesProvider

ALL_PROVIDERS = {
    "groq": GroqProvider,
    "cerebras": CerebrasProvider,
    "openrouter": OpenRouterProvider,
    "sambanova": SambaNovaProvider,
    "mistral": MistralProvider,
    "gemini": GeminiProvider,
    "cloudflare": CloudflareProvider,
    "pollinations": PollinationsProvider,
    "chutes": ChutesProvider,
}
