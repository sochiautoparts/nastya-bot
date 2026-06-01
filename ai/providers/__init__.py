"""AI Providers v36.0 — llama-cpp-python Native!

v36: ПЕРЕХОД НА LLAMA-CPP-PYTHON!
Убран Ollama сервер — модель загружается ПРЯМО в процесс.

PRIMARY: LlamaCppProvider
  - Qwen3-4B-Instruct GGUF (Q4_K_M, ~2.4GB)
  - AVX2/AVX512 ускорение — в 2-3x быстрее Ollama
  - Нет HTTP-сервера — нулевая задержка
  - Полный контроль над параметрами

FALLBACK: PollinationsProvider
  - Бесплатный, без API ключа
  - Если локальная модель недоступна
"""
from ai.providers.base import BaseProvider, AIResponse, ProviderError
from ai.providers.llama_cpp_provider import LlamaCppProvider

ALL_PROVIDERS = {
    "llama_cpp": LlamaCppProvider,
}
