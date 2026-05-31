"""AI Providers — all available providers for Nastya Bot 2.1.

NO Grok (only 2 requests, useless — REMOVED).
NO Blackbox (unstable).
NO HuggingFace text (doesn't support chat format properly).

v2.1 changes:
- Added GitHub Models provider (free, uses GITHUB_TOKEN)
- Cloudflare promoted with vision support
- Provider chain reordered: Cloudflare first (free + reliable)
"""
from ai.providers.base import BaseProvider, AIResponse, ProviderError
from ai.providers.cerebras_provider import CerebrasProvider
from ai.providers.openrouter_provider import OpenRouterProvider
from ai.providers.sambanova_provider import SambaNovaProvider
from ai.providers.mistral_provider import MistralProvider
from ai.providers.gemini_provider import GeminiProvider
from ai.providers.cloudflare_provider import CloudflareProvider
from ai.providers.github_provider import GitHubModelsProvider
from ai.providers.pollinations_provider import PollinationsProvider
from ai.providers.chutes_provider import ChutesProvider

ALL_PROVIDERS = {
    "cloudflare": CloudflareProvider,
    "github_models": GitHubModelsProvider,
    "cerebras": CerebrasProvider,
    "openrouter": OpenRouterProvider,
    "sambanova": SambaNovaProvider,
    "mistral": MistralProvider,
    "gemini": GeminiProvider,
    "pollinations": PollinationsProvider,
    "chutes": ChutesProvider,
}
