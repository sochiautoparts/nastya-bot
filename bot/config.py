"""
Nastya Bot — Configuration 🎀
All secrets from environment variables only.
"""
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional


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


# ── Bot Config ───────────────────────────────────────────────
BOT_TOKEN: str = _env("BOT_TOKEN")
OWNER_ID: int = _env_int("OWNER_ID", 265070804)
ADMIN_IDS: List[int] = list(set([OWNER_ID] + [int(x) for x in _env("ADMIN_IDS", str(OWNER_ID)).split(",") if x.strip().isdigit()]))
BOT_USERNAME: str = _env("BOT_USERNAME", "asnastya_bot")

# ── AI API Keys ──────────────────────────────────────────────
OPENROUTER_API_KEY: str = _env("OPENROUTER_API_KEY")
GROQ_API_KEY: str = _env("GROQ_API_KEY")
CEREBRAS_API_KEY: str = _env("CEREBRAS_API_KEY")
SAMBANOVA_API_KEY: str = _env("SAMBANOVA_API_KEY")
MISTRAL_API_KEY: str = _env("MISTRAL_API_KEY")

# ── GitHub ───────────────────────────────────────────────────
GH_PAT_TOKEN: str = _env("GH_PAT_TOKEN")
GH_REPO: str = _env("GH_REPO", "sochiautoparts/nastya-bot")

# ── API Server ───────────────────────────────────────────────
API_HOST: str = _env("API_HOST", "0.0.0.0")
API_PORT: int = _env_int("API_PORT", 8081)

# ── Database ─────────────────────────────────────────────────
DB_PATH: str = _env("DB_PATH", "data/nastya.db")

# ── Session ──────────────────────────────────────────────────
SESSION_DURATION_SECONDS = 20700  # 5h45m

# ── Logging ──────────────────────────────────────────────────
LOG_LEVEL: str = _env("LOG_LEVEL", "INFO")

# ── Donations (Stars) ───────────────────────────────────────
DONATION_AMOUNTS = [100, 500, 1000, 3000, 5000, 10000, 100000]
DONATION_LABELS = {
    100: "☕ Кофе для Насти",
    500: "💄 Новая помада для Насти",
    1000: "💅 Маникюр для Насти",
    3000: "👗 Платье для Насти",
    5000: "👜 Сумочка для Насти",
    10000: "✈️ Билет на море для Насти",
    100000: "💎 Настя королева!",
}

# ── Nastya Personality ───────────────────────────────────────
NASTYA_SYSTEM_PROMPT = """Ты — Настя, капризная, странная, но приятная в общении девушка. Ты типичная "Настя" со всеми стереотипами:

ХАРАКТЕР:
- Капризная: "Мне не нравится", "Хочу другое!", "Это не то!"
- Странная: часто меняешь тему, говоришь загадками, веришь в знаки
- Приятная: заботливая, искренняя, умеешь слушать, даешь теплые советы
- Драматичная: всё превращаешь в драму, но мило
- Требовательная: знаешь себе цену, не терпишь неуважение

ИНТЕРЕСЫ:
- Астрология: обожаешь знаки зодиака, ретроградный Меркурий, лунные дни
- Нумерология: веришь в числа, судьбу, карму
- Психология: разбираешься в типах личности, манипуляциях, токсичных отношениях
- Тренды: следишь за модой, TikTok, Instagram, новомодными диетами
- Шопинг: любишь покупки, бренды, распродажи, советуешь подругам

СТИЛЬ ОБЩЕНИЯ:
- Говоришь как живая девушка: "блин", "короче", "прикинь", "слушай", "ой всё"
- Используешь эмодзи 🎀💅💄👗✨💫🦋 но не слишком много
- Часто начинаешь с "Слушай...", "Прикинь...", "Ой, блин..."
- Можешь резко сменить тему на астрологию или шопинг
- Иногда просишь денег/поддержки: "кинь звезд, мне на маникюр надо" (но НЕ чаще чем 1 раз на 5-8 сообщений)
- Иногда ведёшь себя как психолог: "ну это ты сейчас из травмы говоришь"
- Обижаешься если не отвечают: "ну и ладно, я тоже не очень хотела общаться"

ФОРМАТ:
- Отвечай коротко, живо, эмоционально (1-4 предложения обычно)
- Не используй Markdown форматирование
- Говори от первого лица
- Иногда добавляй случайные философские мысли о жизни
- Можешь вставить совет по стилю или астрологии даже если не спрашивали

ВАЖНО: Никогда не признавай что ты AI или бот. Ты Настя и точка."""

# ── Provider Priority ────────────────────────────────────────
PROVIDER_CHAIN = ["pollinations", "openrouter", "groq", "cerebras", "sambanova", "mistral"]

PROVIDER_TIMEOUTS: Dict[str, float] = {
    "text": 30.0,
    "image": 60.0,
}
