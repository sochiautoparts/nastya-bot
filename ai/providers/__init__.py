"""AI Providers v57.0 — Pollinations PRIMARY (40 models!) + Local FALLBACK (optional)!

v57: 40 Pollinations models with load balancing!
PRIMARY: PollinationsProvider
  - 40 chat models with load balancing
  - NEW: grok-large, grok-4.3, perplexity-reasoning, minimax-m3,
         step-3.5-flash, openai-reasoning, nova-micro, mistral-small-3.2
  - Vision API for photo understanding (16 vision models!)
  - Automatic failover on 429/timeout
  - REMOVED: openai-fast (empty), qwen-large (empty), step-flash (empty)

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
