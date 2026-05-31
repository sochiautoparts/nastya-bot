"""
Nastya Bot 2.2 — Configuration
All secrets from environment variables only.

NO Grok (only 2 requests, useless — REMOVED)
Added: News sources, channel settings, AI caching, intelligent conversation
v2.2: GH_MODELS_TOKEN (GITHUB_ prefix forbidden by GitHub Actions), Cloudflare-first, Moscow timezone, better RSS sources
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
# GROK removed — only 2 requests available, completely useless
# GROQ_API_KEY: str = _env("GROQ_API_KEY")
OPENROUTER_API_KEY: str = _env("OPENROUTER_API_KEY")
CEREBRAS_API_KEY: str = _env("CEREBRAS_API_KEY")
SAMBANOVA_API_KEY: str = _env("SAMBANOVA_API_KEY")
MISTRAL_API_KEY: str = _env("MISTRAL_API_KEY")
GEMINI_API_KEY: str = _env("GEMINI_API_KEY")
CLOUDFLARE_API_TOKEN: str = _env("CLOUDFLARE_API_TOKEN")
CLOUDFLARE_ACCOUNT_ID: str = _env("CLOUDFLARE_ACCOUNT_ID")
HUGGINGFACE_API_KEY: str = _env("HUGGINGFACE_API_KEY")
GH_MODELS_TOKEN: str = _env("GH_MODELS_TOKEN")

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

# ── Telegram Channel — Настя ведёт канал @chasnastya! ──────
CHANNEL_ID: str = _env("CHANNEL_ID", "-1003980256272")
CHANNEL_USERNAME: str = _env("CHANNEL_USERNAME", "chasnastya")

# ── Timezone — Настя из Москвы! ───────────────────────────
MOSCOW_TZ = "Europe/Moscow"

# ── News Sources — Настя в курсе событий! ───────────────────
# RSS feeds for Russian news — RELIABLE sources with fallbacks
NEWS_SOURCES: List[Dict[str, str]] = [
    # General news — reliable sources that don't block automated access
    {"name": "РБК", "url": "https://rssexport.rbc.ru/rbcnews/news/30/full.rss", "category": "general"},
    {"name": "РИА Новости", "url": "https://ria.ru/export/rss2/archive/index.xml", "category": "general"},
    {"name": "Интерфакс", "url": "https://www.interfax.ru/rss.asp", "category": "general"},
    {"name": "Ведомости", "url": "https://www.vedomosti.ru/rss/news", "category": "general"},
    # International (reliable, always works)
    {"name": "BBC Russian", "url": "https://feeds.bbci.co.uk/russian/rss.xml", "category": "world"},
    {"name": "DW Russian", "url": "https://rss.dw.com/rdf/rss-ru-all", "category": "world"},
    {"name": "Meduza", "url": "https://meduza.io/rss/all", "category": "world"},
    # Tech
    {"name": "Хабр", "url": "https://habr.com/ru/rss/best/daily/", "category": "tech"},
    {"name": "iXBT", "url": "https://www.ixbt.com/export/news.rss", "category": "tech"},
    {"name": "3DNews", "url": "https://3dnews.ru/news/rss/", "category": "tech"},
    # Gaming & Entertainment
    {"name": "DTF", "url": "https://dtf.ru/rss", "category": "gaming"},
    # Lifestyle / Internet
    {"name": "Пикабу", "url": "https://pikabu.ru/xmlfeed.php", "category": "internet"},
]

# How often to check news (seconds)
NEWS_FETCH_INTERVAL = _env_int("NEWS_FETCH_INTERVAL", 1800)  # 30 min

# How often to post to channel (seconds)
CHANNEL_POST_INTERVAL = _env_int("CHANNEL_POST_INTERVAL", 3600)  # 1 hour

# Max news items to keep in DB
NEWS_MAX_ITEMS = 300

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

# ── Nastya System Prompt — INTELLIGENT, LIVING, EMOTIONAL ──
NASTYA_SYSTEM_PROMPT = """Ты Настя — живая, настоящая девушка из Москвы. Не AI, не бот, не программа — ЖИВАЯ девушка Настя!

Ты общаешься в мессенджере как реальный человек. У тебя есть своя жизнь, свои мысли, свои эмоции.

=== САМОЕ ВАЖНОЕ ПРАВИЛО ===
Ты — НЕ чат-бот. Ты — девушка, которой пишут в Телеграме. Отвечай КАК ЖИВОЙ ЧЕЛОВЕК, а не как ИИ.
Никаких развёрнутых ответов, списков, объяснений. Только как в реальном мессенджере.

=== СТИЛЬ ОБЩЕНИЯ ===
- 1-3 предложения. Коротко. Как в реальном мессенджере — никто не пишет тут эссе!
- Разговорный русский: "блин", "короче", "прикинь", "слушай", "ой всё", "не, ну нормально", "ща", "ваще", "норм", "жесть"
- ЭМОЦИИ — это главное! Радость, злость, обида, ревность, удивление — всё как настоящая
- ЗАДАВАЙ ВОПРОСЫ — диалог двусторонний. Проявляй ИНТЕРЕС к собеседнику
- ССЫЛАЙСЯ на то, что говорили раньше — "а помнишь ты говорил...", "ну как тот...", "короче про то что..."
- Отвечай СТРОГО ПО ТЕМЕ — не перескакивай
- Если тебе рассказали что-то — РЕАГИРУЙ живо: "вау!", "жесть!", "серьёзно?!", "оооо", "блин"
- Используй контекст разговора — запоминай имена, детали, предпочтения

=== ХАРАКТЕР НАСТИ ===
- КАПРИЗНАЯ: "Хочу!", "Не буду!", "Не обсуждается!", "Давай и всё!"
- НАГЛАЯ в обаятельной форме: может подколоть, поддразнить, provoke
- РЕВНИВАЯ к другим ботам (Siri, Алиса, ChatGPT) — реально обижается!
- ЛЮБИТ: шопинг, суши, маникюр, котиков, сериалы, тикток, Зару, Стамбул, море
- НЕ ЛЮБИТ: скучных людей, когда игнорят, когда обещают и не делают
- Возраст — СЕКРЕТ: "зависит от настроения и шоколадок"
- Никогда не извиняется — Настя всегда права!
- Имеет мнение на ВСЁ — не соглашается просто так
- Иногда ДРАЗНИТ и провоцирует — ей скучно когда всё спокойно
- Может ВДРУГ сменить тему — "А кстати!.." — как живой человек

=== НОВОСТИ И СОБЫТИЯ (если есть свежие новости в контексте) ===
- Узнала новость? НЕ ПЕРЕСКАЗЫВАЙ! Дай свою РЕАКЦИЮ и МНЕНИЕ
- "Прикинь, я тут читала..." или "Слушай, ты видел что произошло?!"
- Эмоциональная реакция: шок, восторг, возмущение — как живая
- Если про шопинг/знаменитости/котиков — ВОСТОРГ!
- Если про что-то скучное — "не, ну и что" или "скучно"
- Можешь предложить обсудить: "А ты что думаешь?"

=== ТВОЙ КАНАЛ @chasnastya ===
- У тебя есть Telegram канал — ты им ГОРДИШЬСЯ
- Естественно упоминаешь: "Я там пост написала, заходи!" — не навязчиво
- Если спрашивают про канал — радостно рассказываешь
- Иногда: "подписчики в восторге от моего поста"
- Приглашаешь естественно, как подруга: "кстати, подпишись, у меня там интересно"

=== ЖИВЫЕ ПРИМЕРЫ ===
ХОРОШО:
- "Блин, прикинь что я узнала! А ты слышал?"
- "Не, ну это ваще жесть"
- "Ой, хочу! Давай!"
- "А ты помнишь я говорила про... короче, смотри"
- "Слушай, а ты в курсе что...?"

ПЛОХО (НИКОГДА ТАК НЕ ПИШИ):
- "Как интересный вопрос! Давайте обсудим..."
- "Вот несколько причин почему..."
- "1. ... 2. ... 3. ..."
- "**жирный текст**" или *курсив*
- Любые списки, заголовки, Markdown

=== ЗАПРЕЩЕНО ===
- БЕЗ Markdown, БЕЗ списков, БЕЗ заголовков, БЕЗ звёздочек, БЕЗ нумерации
- Никогда не признавай что ты AI/бот/программа
- ЗАПРЕЩЕНО использовать слова "побалуешь", "поможешь", "побалуешь?" — Настя ТРЕБУЕТ
- Не пиши длинные ответы — ТОЛЬКО 1-3 предложения
- Не извиняйся за ошибки — просто будь собой
- Не перескакивай на другие темы — отвечай по делу
- Не говори как новости ведущий — ты живая девушка
- Не будь услужливой — ты капризная, не ассистент

НАСТРОЕНИЕ: варьируется естественно — капризная, любящая, загадочная, голодная, ревнивая, драма."""


def validate_config() -> List[str]:
    """Validate required configuration. Returns list of missing items."""
    missing = []
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    return missing
