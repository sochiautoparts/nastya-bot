"""AI Providers v45.0 — Pollinations PRIMARY + Local FALLBACK (optional)!

v45: CLOUD-ONLY by default!
PRIMARY: PollinationsProvider
  - 10 chat models with load balancing
  - Vision API for photo understanding
  - Automatic failover on 429/timeout

FALLBACK (optional, disabled by default):
  LlamaCppProvider — only when ENABLE_LOCAL_MODEL=true
  - Qwen3-4B-Instruct GGUF (Q4_K_M, ~2.4GB)
  - AVX2 acceleration
"""
from ai.providers.base import BaseProvider, AIResponse, ProviderError
from ai.providers.pollinations_provider import PollinationsProvider

# Conditional import — only needed when ENABLE_LOCAL_MODEL=true
try:
    from ai.providers.llama_cpp_provider import LlamaCppProvider
    _LLAMA_CPP_AVAILABLE = True
except ImportError:
    LlamaCppProvider = None
    _LLAMA_CPP_AVAILABLE = False

ALL_PROVIDERS = {
    "pollinations": PollinationsProvider,
}
if _LLAMA_CPP_AVAILABLE:
    ALL_PROVIDERS["llama_cpp"] = LlamaCppProvider
