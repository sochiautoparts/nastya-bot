"""AI Providers - Pollinations PRIMARY + Cloudflare FALLBACK + LlamaCpp LOCAL-FIRST."""

from ai.providers.base import BaseProvider, AIResponse, ProviderError
from ai.providers.pollinations_provider import PollinationsProvider
from ai.providers.cloudflare_provider import CloudflareProvider

try:
    from ai.providers.llama_cpp_provider import LlamaCppProvider
    _LLAMA_CPP_AVAILABLE = True
except ImportError:
    LlamaCppProvider = None
    _LLAMA_CPP_AVAILABLE = False

ALL_PROVIDERS = {
    "pollinations": PollinationsProvider,
    "cloudflare": CloudflareProvider,
}
if _LLAMA_CPP_AVAILABLE:
    ALL_PROVIDERS["llama_cpp"] = LlamaCppProvider
