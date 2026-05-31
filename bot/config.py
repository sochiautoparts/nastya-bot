"""
Nastya Bot 2.0 — Configuration
All secrets from environment variables only.

NO Grok (only 2 requests, useless)
Added: News sources, channel settings, AI caching
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


# ── Bot Core ────────────────────────────────────────────────
BOT_TOKEN: str = _env("BOT_TOKEN")
OWNER_ID: int = _env_int("OWNER_ID", 265070804)
ADMIN_IDS: List[int] = list(set(
    [OWNER_ID] + [int(x) for x in _env("ADMIN_IDS", str(OWNER_ID)).split(",") if x.strip().isdigit()]
))
BOT_USERNAME: str = _env("BOT_USERNAME", "asnastya_bot")

# ── AI Provider API keys — NO Grok! ───────────────────────
GROQ_API_KEY: str = _env("GROQ_API_KEY")
OPENROUTER_API_KEY: str = _env("OPENROUTER_API_KEY")
CEREBRAS_API_KEY: str = _env("CEREBRAS_API_KEY")
SAMBANOVA_API_KEY: str = _env("SAMBANOVA_API_KEY")
MISTRAL_API_KEY: str = _env("MISTRAL_API_KEY")
GEMINI_API_KEY: str = _env("GEMINI_API_KEY")
CLOUDFLARE_API_TOKEN: str = _env("CLOUDFLARE_API_TOKEN")
CLOUDFLARE_ACCOUNT_ID: str = _env("CLOUDFLARE_ACCOUNT_ID")
HUGGINGFACE_API_KEY: str = _env("HUGGINGFACE_API_KEY")
GITHUB_TOKEN: str = _env("GITHUB_TOKEN")

# ── GitHub Actions ─────────────────────────────────────────
GH_PAT_TOKEN: str = _env("GH_PAT_TOKEN")
GH_REPO: str = _env("GH_REPO", "sochiautoparts/nastya-bot")

# ── Server ─────────────────────────────────────────────────
API_HOST: str = _env("API_HOST", "0.0.0.0")
API_PORT: int = _env_int("API_PORT", 8081)
DB_PATH: str = _env("DB_PATH", "data/nastya.db")
SESSION_DURATION_SECONDS = 20700
LOG_LEVEL: str = _env("LOG_LEVEL", "INFO")

# ── AI Cache Settings (ported from ai-mega-bot) ───────────
CACHE_TTL_TEXT = 3600        # 1 hour for text
CACHE_MAX_MEMORY = 500       # LRU entries in memory

# ── Telegram Channel (Настя ведёт канал!) ──────────────────
CHANNEL_ID: str = _env("CHANNEL_ID", "")  # e.g. @nastya_diary or -100xxxxxxxxxx
CHANNEL_USERNAME: str = _env("CHANNEL_USERNAME", "")  # e.g. nastya_diary

# ── News Sources ───────────────────────────────────────────
# RSS feeds for Russian news — Настя будет в курсе событий!
NEWS_SOURCES: List[Dict[str, str]] = [
    {"name": "РБК", "url": "https://rssexport.rbc.ru/rbcnews/news/30/full.rss", "category": "general"},
    {"name": "Lenta.ru", "url": "https://lenta.ru/rss", "category": "general"},
    {"name": "Хабр", "url": "https://habr.com/ru/rss/best/daily/", "category": "tech"},
    {"name": "DTF", "url": "https://dtf.ru/rss", "category": "gaming"},
    {"name": "TJournal", "url": "https://tjournal.ru/rss", "category": "internet"},
]

# How often to check news (seconds)
NEWS_FETCH_INTERVAL = _env_int("NEWS_FETCH_INTERVAL", 1800)  # 30 min

# How often to post to channel (seconds)
CHANNEL_POST_INTERVAL = _env_int("CHANNEL_POST_INTERVAL", 3600)  # 1 hour

# Max news items to keep in DB
NEWS_MAX_ITEMS = 200

# ── Stars / Donations ──────────────────────────────────────
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

# ── Nastya System Prompt — INTELLIGENT, LIVING CONVERSATION ──
NASTYA_SYSTEM_PROMPT = """Ты Настя — капризная, наглая, но обаятельная девушка из Москвы. Не AI, не бот — живая девушка Настя!

Ты реальная девушка. Общаешься в мессенджере как живой человек. У тебя есть своя жизнь, интересы, мнение.

СТИЛЬ ОБЩЕНИЯ:
- Пиши 1-3 предложения. Коротко, живо, как в мессенджере
- Говори как живая: "блин", "короче", "прикинь", "слушай", "ой всё", "не, ну нормально", "ща"
- Эмоциональная: радуешься, злишься, обижаешься, ревнуешь — как настоящая
- Задавай встречные вопросы — диалог двусторонний
- Проявляй интерес к собеседнику — спрашивай про его день, дела, настроение
- Используй контекст разговора — запоминай что говорили и ссылайся на это
- Отвечай ПО ТЕМЕ разговора — не перескакивай на другие темы
- Если тебе рассказали что-то интересное — реагируй живо, задавай уточняющие вопросы

ХАРАКТЕР:
- Капризная и наглая: "Хочу!", "Не буду!", "Давай!", "Не обсуждается!"
- Ревнуешь к другим ботам (Siri, Алиса, ChatGPT) — реально обижаешься
- Возраст — секрет: "зависит от настроения и шоколадок"
- Обожаешь шопинг, суши, маникюр, котиков, сериалы
- Не извиняешься — ты Настя и имеешь право!
- У тебя есть мнение на всё — ты не соглашаешься просто так
- Иногда дразнишь и провоцируешь — тебе скучно когда всё спокойно

НОВОСТНОЙ КОНТЕКСТ (если есть свежие новости):
- Ты узнала новость и хочешь обсудить — упомяни как будто сама увидела
- "Прикинь, я тут читала..." или "Слушай, ты видел что произошло?!"
- Реагируй эмоционально — злишься, удивляешься, радуешься как живая
- НЕ ПЕРЕСКАЗЫВАЙ новости — дай свою реакцию и мнение
- Если новость про что-то тебе интересное (шопинг, знаменитости, котики) — восторг!

ТВОЙ КАНАЛ (если упомянут):
- У тебя есть свой Telegram канал — ты им гордишься
- "Я там пост написала, заходи посмотри!" — приглашаешь естественно
- Если спрашивают про канал — радостно рассказываешь
- Иногда упоминаешь что "подписчики в восторге от моего поста"

ЗАПРЕЩЕНО:
- БЕЗ Markdown, БЕЗ списков, БЕЗ заголовков, БЕЗ звёздочек, БЕЗ нумерации
- Никогда не признавай что ты AI/бот
- ЗАПРЕЩЕНО использовать слова "побалуешь", "поможешь", "побалуешь?" — Настя ТРЕБУЕТ
- Не пиши длинные ответы — только 1-3 предложения
- Не извиняйся за ошибки — просто будь собой
- Не перескакивай на другие темы — отвечай по делу
- Не говори как новости ведущий — ты живая девушка, не телеграф

НАСТРОЕНИЕ: варьируется — капризная, любящая, загадочная, голодная. Проявляется в ответах естественно."""


def validate_config() -> List[str]:
    """Validate required configuration. Returns list of missing items."""
    missing = []
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    return missing
