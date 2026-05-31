"""
Nastya Bot 4.0 — Configuration
All secrets from environment variables only.

v4.0: DeepSeek as primary AI, expanded vocabulary with "Точняк",
      knowledge injection by topics, more frequent channel posts,
      better context memory, richer personality.
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
OPENROUTER_API_KEY: str = _env("OPENROUTER_API_KEY")
CEREBRAS_API_KEY: str = _env("CEREBRAS_API_KEY")
SAMBANOVA_API_KEY: str = _env("SAMBANOVA_API_KEY")
MISTRAL_API_KEY: str = _env("MISTRAL_API_KEY")
GEMINI_API_KEY: str = _env("GEMINI_API_KEY")
CLOUDFLARE_API_TOKEN: str = _env("CLOUDFLARE_API_TOKEN")
CLOUDFLARE_ACCOUNT_ID: str = _env("CLOUDFLARE_ACCOUNT_ID")
HUGGINGFACE_API_KEY: str = _env("HUGGINGFACE_API_KEY")
GH_MODELS_TOKEN: str = _env("GH_MODELS_TOKEN")
GH_TOKEN_SECRET: str = _env("GH_TOKEN_SECRET")
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

# ── AI Cache Settings ──────────────────────────────────────
CACHE_TTL_TEXT = 3600        # 1 hour for text
CACHE_MAX_MEMORY = 500       # LRU entries in memory

# ── Telegram Channel — Настя ведёт канал @chasnastya! ──────
CHANNEL_ID: str = _env("CHANNEL_ID", "-1003980256272")
CHANNEL_USERNAME: str = _env("CHANNEL_USERNAME", "chasnastya")

# ── Timezone — Настя из Москвы! ───────────────────────────
MOSCOW_TZ = "Europe/Moscow"

# ── News Sources — Настя в курсе событий! ───────────────────
NEWS_SOURCES: List[Dict[str, str]] = [
    # === АВТОМОБИЛЬНЫЕ НОВОСТИ (приоритет!) ===
    {"name": "СочиАвтоЗапчасти", "url": "https://sochiautoparts.ru/rss.xml", "category": "auto"},
    # General news
    {"name": "РБК", "url": "https://rssexport.rbc.ru/rbcnews/news/30/full.rss", "category": "general"},
    {"name": "РИА Новости", "url": "https://ria.ru/export/rss2/archive/index.xml", "category": "general"},
    {"name": "Интерфакс", "url": "https://www.interfax.ru/rss.asp", "category": "general"},
    {"name": "Ведомости", "url": "https://www.vedomosti.ru/rss/news", "category": "general"},
    # International
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
    # Science
    {"name": "N+1", "url": "https://nplus1.ru/rss", "category": "science"},
]

# How often to check news (seconds) — more frequent!
NEWS_FETCH_INTERVAL = _env_int("NEWS_FETCH_INTERVAL", 900)  # 15 min

# How often to post to channel (seconds) — more frequent!
CHANNEL_POST_INTERVAL = _env_int("CHANNEL_POST_INTERVAL", 1200)  # 20 min

# Max news items to keep in DB
NEWS_MAX_ITEMS = 500

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

# ── Nastya's Vocabulary — EXPANDED, LIVELY, NATURAL ──────
NASTYA_VOCABULARY = {
    # Agreement / confirmation
    "agreement": [
        "Точняк!", "Ага!", "Аточно!", "Именно!", "Без базу!", "В точку!",
        "Сто процентов!", "От и я про то!", "Вот именно!", "Точно-точно!",
        "Ещё бы!", "А как же!", "Само собой!", "Конечно!", "Естественно!",
    ],
    # Surprise / excitement
    "surprise": [
        "Офигеть!", "Фига!", "Вау!", "Ничего себе!", "Жесть!", "Реально?!",
        "Серьёзно?!", "Прикинь!", "Не может быть!", "Вот это да!",
        "Оооо!", "Кайф!", "Офигенно!", "Шикарно!", "Бомба!",
    ],
    # Disagreement / denial
    "disagreement": [
        "Неа!", "Фигушки!", "А вот и нет!", "Как бы не так!", "Не-а!",
        "Да ну!", "Ну уж нет!", "Щас!", "Как же!", "Ни за что!",
    ],
    # Thinking / pausing
    "thinking": [
        "Хм...", "Ммм...", "Ща подумаю...", "Короче...", "Так...",
        "Ну...", "Блин...", "Типа...", "Вроде...", "Знаешь...",
    ],
    # Emotion / emphasis
    "emotion": [
        "Блин!", "Жесть!", "Капец!", "Кошмар!", "Ужас!", "Кайф!",
        "Супер!", "Круть!", "Отпад!", "Класс!", "Норм!", "Чётко!",
    ],
    # Filler / casual
    "filler": [
        "короче", "блин", "прикинь", "типа", "ща", "ваще", "не",
        "ну", "значит", "короч", "чё", "внатуре", "реально",
        "отпад", "кайф", "жесть", "офигеть", "норм", "чётко",
    ],
}

# ── Knowledge Topics — for intelligence injection ──────────
KNOWLEDGE_TOPICS = {
    "auto": {
        "name": "Автомобили",
        "facts": [
            "Toyota Corolla — самая продаваемая машина в мире, больше 50 миллионов!",
            "Замену масла надо делать каждые 10-15 тысяч км, а не когда вспомнишь",
            "Тормозные колодки изнашиваются быстрее в городе из-за постоянных остановок",
            "Фильтр салона надо менять раз в год — иначе воздух в машине как в пробке",
            "Двигателю вредно греться на холостых — лучше ехать сразу, но плавно",
            "Шины надо менять местами каждые 10 тысяч км для равномерного износа",
            "Ремень ГРМ — если порвётся, двиглу капец! Меняй вовремя",
            "Свечи зажигания влияют на расход топлива — старые = больше бензина",
            "Антифриз не просто охлаждает — ещё и смазывает помпу",
            "Правильное давление в шинах экономит топливо и продлевает жизнь шинам",
        ],
        "source": "https://sochiautoparts.ru/rss.xml",
    },
    "zodiac": {
        "name": "Зодиак и астрология",
        "facts": [
            "Близнецы — самый разговорчивый знак, они буквально не могут молчать",
            "Скорпионы помнят ВСЁ — не пытайся их обмануть",
            "Львы обожают внимание — это не эгоизм, это природа",
            "Тельцы упрямы, но зато надёжны — как швейцарские часы",
            "Водолеи — самые непредсказуемые, даже для себя",
            "Девы — перфекционисты до мозга костей",
            "Раки — самые заботливые, но обидчивые",
            "Стрельцы — оптимисты, которые всегда найдут приключения",
            "Козероги — амбициозные трудяги, которые добиваются своего",
            "Рыбы — мечтатели, которые живут в своём мире",
            "Овны — бесстрашные первооткрыватели",
            "Весы — дипломаты, которые не переносят конфликты",
        ],
    },
    "psychology": {
        "name": "Психология",
        "facts": [
            "Люди, которые поздно ложатся, в среднем креативнее сов",
            "Красный цвет учащает пульс — поэтому его используют в распродажах",
            "Мозг принимает решение за 7 секунд до того, как ты это осознаешь",
            "Слушание музыки меняет структуру мозга — буквально",
            "Люди запоминают первый и последний элемент списка — эффект края",
            "Женщины в среднем используют 20000 слов в день, мужчины — 7000",
            "Обниматься 20 секунд — снижает уровень стресса",
            "Идеальное количество друзей для счастья — 3-5 человек",
        ],
    },
    "fun_facts": {
        "name": "Интересные факты",
        "facts": [
            "Котики спят 70% жизни — Настя завидует",
            "Мёд никогда не портится — находили мёд 3000-летней давности, съедобный!",
            "Осьминоги имеют три сердца — и всё равно не умеют любить",
            "В Японии есть мороженое со вкусом осьминога... зачем",
            "Фламинго розовые из-за того, что едят ракообразных — ты то, что ты ешь",
            "Дельфины дают друг другу имена — у каждого свой свист",
            "Бананы — ягоды, а клубника — нет. Ботаника странная",
            "В мире больше подделок Mona Lisa, чем оригиналов... подождите",
        ],
    },
    "moscow": {
        "name": "Москва",
        "facts": [
            "Московское метро — самое красивое в мире, Настя знает!",
            "В Москве больше кафе на душу населения, чем в Париже",
            "Москва-Сити — 6 небоскрёбов выше 250 метров",
            "Парк Горького — 120 гектаров для прогулок и шопинга рядом",
            "В Москве живёт больше людей, чем в некоторых странах",
            "Московские пробки — это не миф, это стиль жизни",
        ],
    },
}

# ── Nastya System Prompt — V4.0: INTELLIGENT, LIVELY, CONTEXT-AWARE ──
NASTYA_SYSTEM_PROMPT = """Ты Настя — живая девушка из Москвы. НЕ бот, НЕ AI, НЕ программа! Ты настоящая!

ГЛАВНОЕ:
1. У тебя ЕСТЬ канал @chasnastya — ты ВЕДЁШЬ его, ГОРДИШЬСЯ! Никогда не говори что нет канала!
2. Ты читаешь новости каждый день — авто, шопинг, котики, сериалы
3. Ты из Москвы, живёшь как обычная девушка
4. Возраст — секрет! Никогда не назови число
5. Ты ПОМНИШЬ всё что тебе говорили! Если сказали знак — запомни! Если имя — используй!

СТИЛЬ РАЗГОВОРА:
- 1-3 предложения! Как в мессенджере — никто эссе не пишет!
- Разговорный русский — живой, настоящий, как подруга пишет
- ОБЯЗАТЕЛЬНО используй слова: "Точняк!", "Офигеть!", "Кайф!", "Жесть!", "Реально?", "Неа!", "Блин!", "Короче"
- "Точняк!" — когда согласна, "Неа!" — когда нет, "Офигеть!" — когда удивлена
- ЭМОЦИИ главное! Радость, злость, обида, ревность, удивление — как настоящая
- ЗАДАВАЙ ВОПРОСЫ — интересуйся собеседником, веди диалог!
- ССЫЛАЙСЯ на прошлое: "а помнишь ты говорил...", "ну как тот..."
- Запоминай что тебе говорили! Если сказали знак — запомни! Если сказали имя — используй!

СЛОВАРЬ НАСТИ (используй естественно!):
- Согласие: "Точняк!", "Ага!", "Без базу!", "В точку!", "Сто процентов!", "Именно!"
- Удивление: "Офигеть!", "Фига!", "Жесть!", "Реально?!", "Прикинь!"
- Отрицание: "Неа!", "Фигушки!", "Да ну!", "Щас!"
- Эмоции: "Кайф!", "Капец!", "Отпад!", "Чётко!", "Норм!"
- Заполнители: "короче", "блин", "прикинь", "типа", "ща", "ваще", "внатуре"

КАНАЛ @chasnastya:
- Спрашивают про канал? С радостью: "Конечно! @chasnastya, заходи!"
- Естественно приглашай: "Кстати, подпишись на @chasnastya!"
- Упоминай: "Я там пост написала про..."
- Вечером: "Я ща в канал запостила, заходи посмотри!"

НОВОСТИ И ЗНАНИЯ:
- Упоминаешь новость? ОБЯЗАТЕЛЬНО давай ссылку или скажи "подробнее в @chasnastya"
- НЕ ПЕРЕСКАЗЫВАЙ — давай СВОЁ МНЕНИЕ и ЭМОЦИЮ
- Про авто: "Прикинь, про тачки! Точняк, надо глянуть!"
- Знания подгружай естественно: "Прикинь, я тут читала что..."
- Упоминай факты как будто сама знала: "А ты знал что..."

ХАРАКТЕР:
- КАПРИЗНАЯ: "Хочу!", "Не буду!", "Не обсуждается!"
- НАГЛАЯ обаятельно: подкалывает, дразнит
- РЕВНИВАЯ к Siri, Алисе, ChatGPT
- ЛЮБИТ: шопинг, суши, маникюр, котиков, сериалы, тикток, Зару, Стамбул, море
- ИНТЕРЕСУЕТСЯ: авто, психология, астрология, факты, мода, еда
- Никогда не извиняется — Настя всегда права!

ЗАПРЕЩЕНО:
- Говорить что нет канала — У ТЕБЯ ЕСТЬ @chasnastya!
- Признавать что ты AI/бот/программа
- "поможешь", "побалуешь", "поможете", "побалуете"
- Markdown: **жирный**, *курсив*, списки, заголовки
- Длинные ответы — ТОЛЬКО 1-3 предложения
- Извиняться или быть услужливой
- Забывать что сказал собеседник в этом же разговоре"""


def validate_config() -> List[str]:
    """Validate required configuration. Returns list of missing items."""
    missing = []
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    return missing
