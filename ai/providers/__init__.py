"""AI Providers — Pollinations PRIMARY (local model disabled, restorable)."""

from ai.providers.base import BaseProvider, AIResponse, ProviderError
from ai.providers.pollinations_provider import PollinationsProvider

# Local model DISABLED — can be restored by uncommenting:
# try:
#     from ai.providers.llama_cpp_provider import LlamaCppProvider
#     _LLAMA_CPP_AVAILABLE = True
# except ImportError:
#     LlamaCppProvider = None
#     _LLAMA_CPP_AVAILABLE = False

ALL_PROVIDERS = {
    "pollinations": PollinationsProvider,
}
# if _LLAMA_CPP_AVAILABLE:
#     ALL_PROVIDERS["llama_cpp"] = LlamaCppProvider
