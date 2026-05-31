"""AI Providers — all available providers for Nastya Bot 4.0.

v4.1 changes:
- DeepSeek API as PRIORITY #1 (best Russian quality, direct API)
- Provider chain: DeepSeek → Chutes (DeepSeek V3 free) → GitHub Models → Cloudflare → Pollinations → others
- image_base64 no longer popped from kwargs (fixes vision fallback)
- GitHub Models handles auth failure gracefully (PAT needs 'models' permission)
- Aggressive ad/artifact cleaning from Pollinations responses
"""
from ai.providers.base import BaseProvider, AIResponse, ProviderError
from ai.providers.deepseek_provider import DeepSeekProvider
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
    "deepseek": DeepSeekProvider,
    "chutes": ChutesProvider,
    "cloudflare": CloudflareProvider,
    "github_models": GitHubModelsProvider,
    "cerebras": CerebrasProvider,
    "openrouter": OpenRouterProvider,
    "sambanova": SambaNovaProvider,
    "mistral": MistralProvider,
    "gemini": GeminiProvider,
    "pollinations": PollinationsProvider,
}
