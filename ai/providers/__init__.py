"""AI Providers — all available providers for Nastya Bot 5.0.

v5.0 changes:
- Cloudflare Workers AI as PRIMARY (free, reliable, many models)
- HuggingFace as SECONDARY (free tier with token, many models)
- Chutes as TERTIARY (free DeepSeek V3, rate-limited)
- Pollinations as QUATERNARY (always free, always available, ads cleaned)
- DeepSeek REMOVED (was returning 402 Insufficient Balance)
- GitHub Models as backup (needs PAT with 'models' permission)
- image_base64 no longer popped from kwargs (fixes vision fallback)
- Aggressive ad/artifact cleaning from Pollinations responses
"""
from ai.providers.base import BaseProvider, AIResponse, ProviderError
from ai.providers.cloudflare_provider import CloudflareProvider
from ai.providers.huggingface_provider import HuggingFaceProvider
from ai.providers.chutes_provider import ChutesProvider
from ai.providers.pollinations_provider import PollinationsProvider
from ai.providers.github_provider import GitHubModelsProvider
from ai.providers.cerebras_provider import CerebrasProvider
from ai.providers.openrouter_provider import OpenRouterProvider
from ai.providers.sambanova_provider import SambaNovaProvider
from ai.providers.mistral_provider import MistralProvider
from ai.providers.gemini_provider import GeminiProvider

ALL_PROVIDERS = {
    "cloudflare": CloudflareProvider,
    "huggingface": HuggingFaceProvider,
    "chutes": ChutesProvider,
    "pollinations": PollinationsProvider,
    "github_models": GitHubModelsProvider,
    "cerebras": CerebrasProvider,
    "openrouter": OpenRouterProvider,
    "sambanova": SambaNovaProvider,
    "mistral": MistralProvider,
    "gemini": GeminiProvider,
}
