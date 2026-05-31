"""AI Providers — all available providers for Nastya Bot 6.0.

v6.0 changes:
- Cloudflare Workers AI as PRIMARY (free, reliable, many models)
- Groq as SECONDARY (free, ultra-fast LPU inference, great Russian)
- HuggingFace as TERTIARY (free tier with token, many models)
- Chutes as QUATERNARY (free DeepSeek V3, rate-limited)
- OpenRouter as QUINARY (27+ free models, single API)
- Pollinations as FALLBACK #1 (always free, always available, ads cleaned)
- GitHub Models as FALLBACK #2 (needs PAT with 'models' permission)
- DeepSeek REMOVED ENTIRELY (was returning 402 Insufficient Balance)
- Added Groq (fastest free inference, excellent for real-time chat)
- Added OpenRouter (27+ free models, most reliable fallback)
"""
from ai.providers.base import BaseProvider, AIResponse, ProviderError
from ai.providers.cloudflare_provider import CloudflareProvider
from ai.providers.groq_provider import GroqProvider
from ai.providers.huggingface_provider import HuggingFaceProvider
from ai.providers.chutes_provider import ChutesProvider
from ai.providers.openrouter_provider import OpenRouterProvider
from ai.providers.pollinations_provider import PollinationsProvider
from ai.providers.github_provider import GitHubModelsProvider
from ai.providers.cerebras_provider import CerebrasProvider
from ai.providers.sambanova_provider import SambaNovaProvider
from ai.providers.mistral_provider import MistralProvider
from ai.providers.gemini_provider import GeminiProvider

ALL_PROVIDERS = {
    "cloudflare": CloudflareProvider,
    "groq": GroqProvider,
    "huggingface": HuggingFaceProvider,
    "chutes": ChutesProvider,
    "openrouter": OpenRouterProvider,
    "pollinations": PollinationsProvider,
    "github_models": GitHubModelsProvider,
    "cerebras": CerebrasProvider,
    "sambanova": SambaNovaProvider,
    "mistral": MistralProvider,
    "gemini": GeminiProvider,
}
