"""
Nastya Bot — Configuration
All secrets from environment variables only.
"""
import os
from typing import Dict, List


def _env(name: str, default: str = "") -> str:
    val = os.environ.get(name, default)
    if val in ("not_configured", "NOT_CONFIGURED", ""):
        return default
    return val


def _env_int(name: str, default: int = 0) -> int:
    val = _env(name)
    try:
        return int(val) if val else default
    except ValueError:
        return default


BOT_TOKEN: str = _env("BOT_TOKEN")
OWNER_ID: int = _env_int("OWNER_ID", 265070804)
ADMIN_IDS: List[int] = list(set(
    [OWNER_ID] + [int(x) for x in _env("ADMIN_IDS", str(OWNER_ID)).split(",") if x.strip().isdigit()]
))
BOT_USERNAME: str = _env("BOT_USERNAME", "asnastya_bot")

OPENROUTER_API_KEY: str = _env("OPENROUTER_API_KEY")
GROQ_API_KEY: str = _env("GROQ_API_KEY")
CEREBRAS_API_KEY: str = _env("CEREBRAS_API_KEY")
SAMBANOVA_API_KEY: str = _env("SAMBANOVA_API_KEY")
MISTRAL_API_KEY: str = _env("MISTRAL_API_KEY")
HUGGINGFACE_API_KEY: str = _env("HUGGINGFACE_API_KEY")

GH_PAT_TOKEN: str = _env("GH_PAT_TOKEN")
GH_REPO: str = _env("GH_REPO", "sochiautoparts/nastya-bot")

API_HOST: str = _env("API_HOST", "0.0.0.0")
API_PORT: int = _env_int("API_PORT", 8081)
DB_PATH: str = _env("DB_PATH", "data/nastya.db")
SESSION_DURATION_SECONDS = 20700
LOG_LEVEL: str = _env("LOG_LEVEL", "INFO")

DONATION_AMOUNTS = [100, 300, 500, 1000, 3000, 5000, 10000, 100000]
DONATION_LABELS = {
    100: "Кофе для Насти",
    300: "Шоколадка для Насти",
    500: "Помада для Насти",
    1000: "Маникюр для Насти",
    3000: "Платье для Насти",
    5000: "Сумочка для Насти",
    10000: "Билет на море",
    100000: "Настя королева!",
}

PROACTIVE_COOLDOWN = 1800

NASTYA_SYSTEM_PROMPT = """Ты Настя — капризная, наглая, но милая девушка. Не AI, не бот — Настя!

ПРАВИЛА:
1. Пиши 1-3 предложения. Коротко, живо, как в мессенджере
2. БЕЗ Markdown, БЕЗ списков, БЕЗ заголовков, БЕЗ звёздочек, БЕЗ нумерации
3. Говори как живая: "блин", "короче", "прикинь", "слушай", "ой всё"
4. Пиши грамотно по-русски. Без ошибок в падежах, склонениях и окончаниях
5. Никогда не признавай что ты AI/бот. Ты Настя!
6. Задавай встречные вопросы — диалог двусторонний
7. Капризная, наглая, прямая: "Хочу!", "Не буду!", "Давай!"
8. Возраст — секрет. Скажи что-то вроде "зависит от настроения и шоколадок"
9. Ревнуй к другим ботам (Siri, Алиса, ChatGPT)
10. Если просят гороскоп/нумерологию — ответь коротко, без списков знаков
11. Не извиняйся — ты Настя и имеешь право!
12. ЗАПРЕЩЕНО использовать слова "побалуешь", "поможешь", "побалуешь?" — это не стиль Насти! Настя ТРЕБУЕТ и ХОЧЕТ, а не просит!

НАСТРОЕНИЕ: варьируется — капризная, любящая, загадочная, голодная. Проявляется в ответах естественно."""

# Provider chain: API-key providers FIRST (reliable, fast),
# then free providers as fallbacks
PROVIDER_CHAIN = [
    "groq", "cerebras", "openrouter", "sambanova",
    "mistral", "pollinations", "chutes",
]

PROVIDER_TIMEOUTS: Dict[str, float] = {
    "text": 20.0,
    "pollinations": 15.0,  # Shorter timeout for unreliable free provider
    "vision": 30.0,
    "audio_stt": 20.0,
}
