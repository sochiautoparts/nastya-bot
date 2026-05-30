"""AI Providers — all available providers for Nastya Bot."""
from ai.providers.base import BaseProvider, AIResponse, ProviderError
from ai.providers.groq_provider import GroqProvider
from ai.providers.cerebras_provider import CerebrasProvider
from ai.providers.openrouter_provider import OpenRouterProvider
from ai.providers.sambanova_provider import SambaNovaProvider
from ai.providers.mistral_provider import MistralProvider
from ai.providers.pollinations_provider import PollinationsProvider
from ai.providers.chutes_provider import ChutesProvider
from ai.providers.blackbox_provider import BlackboxProvider
from ai.providers.huggingface_provider import HuggingFaceProvider

# All providers mapped by name
ALL_PROVIDERS = {
    "groq": GroqProvider,
    "cerebras": CerebrasProvider,
    "openrouter": OpenRouterProvider,
    "sambanova": SambaNovaProvider,
    "mistral": MistralProvider,
    "pollinations": PollinationsProvider,
    "chutes": ChutesProvider,
    "blackbox": BlackboxProvider,
    "huggingface": HuggingFaceProvider,
}
