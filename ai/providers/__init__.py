"""AI Providers — all available providers for Nastya Bot 13.0.

v13.0 changes — 4 FREE UNLIMITED providers as PRIMARY:
- Pollinations FIRST (always free, always available, vision support)
- Chutes SECOND (free DeepSeek V3, no key, vision support)
- Blackbox THIRD (free, unlimited, multiple models, vision support)
- HuggingFace FOURTH (free tier, optional key, many models, vision support)
- OpenRouter demoted to FIFTH (50 free req/day limit — too greedy!)
- Cloudflare, Groq, GitHub Models as additional fallbacks
- DeepSeek REMOVED ENTIRELY (was returning 402 Insufficient Balance)
"""
from ai.providers.base import BaseProvider, AIResponse, ProviderError
from ai.providers.pollinations_provider import PollinationsProvider
from ai.providers.chutes_provider import ChutesProvider
from ai.providers.blackbox_provider import BlackboxProvider
from ai.providers.huggingface_provider import HuggingFaceProvider
from ai.providers.openrouter_provider import OpenRouterProvider
from ai.providers.cloudflare_provider import CloudflareProvider
from ai.providers.groq_provider import GroqProvider
from ai.providers.github_provider import GitHubModelsProvider
from ai.providers.cerebras_provider import CerebrasProvider
from ai.providers.sambanova_provider import SambaNovaProvider
from ai.providers.mistral_provider import MistralProvider
from ai.providers.gemini_provider import GeminiProvider

ALL_PROVIDERS = {
    "pollinations": PollinationsProvider,
    "chutes": ChutesProvider,
    "blackbox": BlackboxProvider,
    "huggingface": HuggingFaceProvider,
    "openrouter": OpenRouterProvider,
    "cloudflare": CloudflareProvider,
    "groq": GroqProvider,
    "github_models": GitHubModelsProvider,
    "cerebras": CerebrasProvider,
    "sambanova": SambaNovaProvider,
    "mistral": MistralProvider,
    "gemini": GeminiProvider,
}
