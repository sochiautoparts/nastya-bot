"""AI Providers v23.0 — Production Cluster Edition.

ТОЛЬКО ЛОКАЛЬНЫЙ OLLAMA КЛАСТЕР!
Все внешние API-провайдеры УДАЛЕНЫ:
  - GitHub Models (401 auth error)
  - Pollinations (unstable, 429 rate limits)
  - Chutes (429 rate limits)
  - Blackbox (garbage responses)
  - HuggingFace (DNS errors)
  - OpenRouter (rate limits + 404s)
  - Cloudflare (400 model missing)
  - Groq (401 auth)
  - Cerebras (401 auth)
  - Sambanova (429 rate limits)
  - Mistral (rate limits)
  - Gemini (rate limits)

ЕДИНСТВЕННЫЙ провайдер: OllamaClusterProvider
- Локальный inference — БЕЗ внешних API
- Бесплатный, безлимитный, без авторизации
- Vision + Text
- Кэширование + семафоры
"""
from ai.providers.base import BaseProvider, AIResponse, ProviderError
from ai.providers.ollama_cluster_provider import OllamaClusterProvider

ALL_PROVIDERS = {
    "ollama_cluster": OllamaClusterProvider,
}
