"""AI Providers — all available providers for Nastya Bot 15.0.

v15.0 changes — LOCAL model as PRIMARY:
- Ollama FIRST (local Qwen3-VL-2B — free, unlimited, no external dependency!)
- GitHub Models SECOND (free DeepSeek-V3 via PAT — reliable backup)
- Pollinations THIRD (free, no key, vision)
- Chutes FOURTH (free DeepSeek V3, no key, vision)
- Blackbox FIFTH (free, unlimited, multiple models, vision)
- HuggingFace SIXTH (free tier, optional key, many models, vision)
- Other API-key providers as additional fallbacks
- NEVER leaks SSE artifacts — local model returns clean JSON
- NEVER has auth errors — local inference, no external API
"""
from ai.providers.base import BaseProvider, AIResponse, ProviderError
from ai.providers.ollama_provider import OllamaProvider
from ai.providers.github_provider import GitHubModelsProvider
from ai.providers.pollinations_provider import PollinationsProvider
from ai.providers.chutes_provider import ChutesProvider
from ai.providers.blackbox_provider import BlackboxProvider
from ai.providers.huggingface_provider import HuggingFaceProvider
from ai.providers.openrouter_provider import OpenRouterProvider
from ai.providers.cloudflare_provider import CloudflareProvider
from ai.providers.groq_provider import GroqProvider
from ai.providers.cerebras_provider import CerebrasProvider
from ai.providers.sambanova_provider import SambaNovaProvider
from ai.providers.mistral_provider import MistralProvider
from ai.providers.gemini_provider import GeminiProvider

ALL_PROVIDERS = {
    "ollama": OllamaProvider,
    "github_models": GitHubModelsProvider,
    "pollinations": PollinationsProvider,
    "chutes": ChutesProvider,
    "blackbox": BlackboxProvider,
    "huggingface": HuggingFaceProvider,
    "openrouter": OpenRouterProvider,
    "cloudflare": CloudflareProvider,
    "groq": GroqProvider,
    "cerebras": CerebrasProvider,
    "sambanova": SambaNovaProvider,
    "mistral": MistralProvider,
    "gemini": GeminiProvider,
}
