"""Nastya Bot 47.0 — FIX LINKS + WRITE FROM SELF + MORE MODELS!

v47.0: FIX LINKS + WRITE FROM SELF + MORE MODELS!
- Pollinations.ai — 15 chat models + reasoning + vision (load balanced!)
- FIX: Nastya no longer replaces real links with channel links!
- FIX: Channel links only for products, services, news, events, recipes
- Chat: Any links allowed by user request
- Nastya writes ОТ СЕБЯ (from herself) — first person, personal voice
- WEB SEARCH — Настя ищет информацию, товары, услуги, лучшие цены!
- PHOTO SEARCH — фото → распознавание → поиск товаров/цен!
- DISCOVERY ENGINE — авто-посты: рецепты, нумерология, астрология, мероприятия!
- /find — поиск товаров и лучших цен с ссылками!
- /horoscope — гороскоп на сегодня!
- /recipe — рецепт от Насти!
- /numerology — число судьбы!
- INLINE MODE — Настя работает в любом чате через @asnastya_bot!
- AI-POWERED CHANNEL POSTS — развёрнутые посты с ссылками на источник!
- News: только русскоязычные источники, авто-новости ТОЛЬКО sochiautoparts.ru
- Group response chance: 50%
- Qwen3-4B — DISABLED by default (ENABLE_LOCAL_MODEL=true to enable)
- max_tokens=1000 for Pollinations (cloud can handle it!)
- REAL PHOTO UNDERSTANDING via Pollinations vision API!
- URL UNDERSTANDING — Настя читает ссылки!
- PROACTIVE DISCOVERY SHARING — Настя делится находками с пользователями!
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

# ── Pollinations.ai — MULTI-MODEL AI Provider ─────────────────
POLLINATIONS_API_KEY: str = _env("POLLINATIONS_API_KEY", "")
# Models pool (configured in pollinations_provider.py)
POLLINATIONS_TIMEOUT: float = 45.0
POLLINATIONS_MAX_TOKENS: int = 1000  # Cloud can handle longer responses!
POLLINATIONS_MAX_RETRIES: int = 3  # Try up to 3 models on failure

# ── Local Model Toggle ──────────────────────────────────────
# Set ENABLE_LOCAL_MODEL=true to load Qwen3-4B as local fallback
# Default: disabled (cloud-only mode — faster startup, less RAM)
ENABLE_LOCAL_MODEL: bool = _env("ENABLE_LOCAL_MODEL", "false").lower() in ("true", "1", "yes")

# ── LlamaCpp Model — LOCAL FALLBACK (disabled by default!) ────
# Qwen3-4B-Instruct — only when ALL Pollinations models are unavailable
# Only loaded when ENABLE_LOCAL_MODEL=true
MODEL_PATH: str = _env("MODEL_PATH", "models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf") if ENABLE_LOCAL_MODEL else ""

MODEL_N_CTX: int = _env_int("MODEL_N_CTX", 2048)
MODEL_N_THREADS: int = _env_int("MODEL_N_THREADS", 4)
MODEL_MAX_TOKENS: int = _env_int("MODEL_MAX_TOKENS", 256)
MODEL_HISTORY_LIMIT: int = _env_int("MODEL_HISTORY_LIMIT", 10)

OWNER_ID: int = _env_int("OWNER_ID", 0)
ADMIN_IDS: List[int] = list(set(
    [OWNER_ID] + [int(x) for x in _env("ADMIN_IDS", str(OWNER_ID) if OWNER_ID else "").split(",") if x.strip().isdigit()]
))
BOT_USERNAME: str = _env("BOT_USERNAME", "asnastya_bot")

# ── Server ─────────────────────────────────────────────────
API_HOST: str = _env("API_HOST", "0.0.0.0")
API_PORT: int = _env_int("API_PORT", 8081)
DB_PATH: str = _env("DB_PATH", "data/nastya.db")
SESSION_DURATION_SECONDS = 20700
LOG_LEVEL: str = _env("LOG_LEVEL", "INFO")

# ── AI Cache Settings ──────────────────────────────────────
CACHE_TTL_TEXT = 3600
CACHE_MAX_MEMORY = 500

# ── Telegram Channel ──────────────────────────────────────
CHANNEL_ID: str = _env("CHANNEL_ID")
CHANNEL_USERNAME: str = _env("CHANNEL_USERNAME", "chasnastya")

# ── Timezone ──────────────────────────────────────────────
MOSCOW_TZ = "Europe/Moscow"

# ── News Sources (ТОЛЬКО русскоязычные! Англоязычные УБРАНЫ!)
# Автомобильные новости — ТОЛЬКО sochiautoparts.ru!
NEWS_SOURCES: List[Dict[str, str]] = [
    {"name": "СочиАвтоЗапчасти", "url": "https://sochiautoparts.ru/rss.xml", "category": "auto"},
    {"name": "Хабр", "url": "https://habr.com/ru/rss/articles/top/", "category": "tech"},
    {"name": "iXBT", "url": "https://www.ixbt.com/export/news.rss", "category": "tech"},
    {"name": "3DNews", "url": "https://3dnews.ru/news/rss/", "category": "tech"},
    {"name": "OpenNET", "url": "https://www.opennet.ru/opennews/opennews_6.rss", "category": "tech"},
    {"name": "N+1", "url": "https://nplus1.ru/rss", "category": "science"},
    {"name": "Naked Science", "url": "https://naked-science.ru/feed", "category": "science"},
    {"name": "DTF", "url": "https://dtf.ru/rss", "category": "gaming"},
    {"name": "РИА Новости", "url": "https://ria.ru/export/rss2/archive/index.xml", "category": "general"},
    {"name": "Лента.ру", "url": "https://lenta.ru/rss", "category": "general"},
    {"name": "Вести", "url": "https://www.vesti.ru/vesti.rss", "category": "general"},
]

NEWS_FETCH_INTERVAL = _env_int("NEWS_FETCH_INTERVAL", 900)
CHANNEL_POST_INTERVAL = _env_int("CHANNEL_POST_INTERVAL", 1200)
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

# ── Inline Mode Settings ────────────────────────────────────
INLINE_CACHE_TIME: int = 10  # seconds to cache inline results

# ── Group Chat Settings ────────────────────────────────────
GROUP_MAX_MESSAGE_LENGTH = 200  # Shorter messages in group chats
GROUP_RESPONSE_CHANCE = 0.5  # 50% chance to respond in groups (was 30%)

# ── Typing Delay Settings ──────────────────────────────────
TYPING_DELAY_THRESHOLD = 3.0  # Show delay message if processing > 3s
TYPING_DELAY_CHANCE = 0.6  # 60% chance to show delay message

# ── Nastya's Vocabulary ─────────────────────────────────────
NASTYA_VOCABULARY = {
    "agreement": [
        "Ага!", "Именно!", "Точно!", "Точняк!", "В точку!", "Сто процентов!",
        "Само собой!", "Конечно!", "Естественно!", "Чётко!", "Щас!",
    ],
    "surprise": [
        "Вау!", "Офигеть!", "Ничего себе!", "Прикинь!", "Серьёзно?!",
        "Не может быть!", "Вот это да!", "Жесть!", "Капец!",
        "Круто!", "Отпад!", "Бомба!", "Шикарно!", "Класс!",
    ],
    "disagreement": [
        "Неа!", "Да ну!", "Фигушки!", "Ну уж нет!", "Ни за что!", "Как бы не так!", "Внатуре нет!",
    ],
    "thinking": [
        "Хм...", "Ммм...", "Так...", "Ну...", "Блин...", "Знаешь...", "Короч...",
    ],
    "emotion": [
        "Супер!", "Класс!", "Кошмар!", "Ужас!", "Кайф!",
        "Норм!", "Круто!", "Чётко!", "Отпад!", "Бомба!", "Жесть!", "Капец!",
    ],
    "filler": [
        "блин", "прикинь", "ну", "короче", "короч", "вау", "круто",
        "точняк", "офигеть", "жесть", "капец", "щас", "внатуре", "чётко",
        "отпад", "бомба", "фигушки",
    ],
}

# ── Knowledge Topics ────────────────────────────────────────
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
        ],
    },
    "psychology": {
        "name": "Психология",
        "facts": [
            "Люди, которые поздно ложатся, в среднем креативнее сов",
            "Красный цвет учащает пульс — поэтому его используют в распродажах",
            "Мозг принимает решение за 7 секунд до того, как ты это осознаешь",
            "Слушание музыки меняет структуру мозга — буквально",
            "Обниматься 20 секунд — снижает уровень стресса",
            "Идеальное количество друзей для счастья — 3-5 человек",
            "Дофаминовая петля: лайки в соцсетях работают как мини-наркотик",
        ],
    },
    "fun_facts": {
        "name": "Интересные факты",
        "facts": [
            "Котики спят 70% жизни — Настя завидует",
            "Мёд никогда не портится — находили мёд 3000-летней давности, съедобный!",
            "Осьминоги имеют три сердца — и всё равно не умеют любить",
            "Фламинго розовые из-за того, что едят ракообразных — ты то, что ты ешь",
            "Бананы — ягоды, а клубника — нет. Ботаника странная",
            "Акулы появились раньше деревьев — подумай об этом",
        ],
    },
    "moscow": {
        "name": "Москва",
        "facts": [
            "Московское метро — самое красивое в мире, Настя знает!",
            "В Москве больше кафе на душу населения, чем в Париже",
            "Москва-Сити — 6 небоскрёбов выше 250 метров",
            "Парк Горького — 120 гектаров для прогулок и шопинга рядом",
        ],
    },
    "cinema": {
        "name": "Кино и сериалы",
        "facts": [
            "Netflix тратит 17 миллиардов долларов в год на контент",
            "Самый длинный сериал — 'Направляющий свет', 15762 серий!",
            "Средний человек тратит 6 лет жизни на просмотр сериалов",
        ],
    },
    "cooking": {
        "name": "Кулинария и еда",
        "facts": [
            "Суши были invented в Японии как способ консервации рыбы в рисе",
            "Кофе — вторая самая продаваемая вещь в мире после нефти!",
            "Шоколад улучшает настроение — это научно доказано!",
        ],
    },
    "relationships": {
        "name": "Отношения",
        "facts": [
            "Люди влюбляются в среднем за 4 минуты — быстрее чем Настя выбирает платье",
            "Совместное прослушивание музыки усиливает связь — научно!",
            "Химия любви длится 1-3 года — потом работает привязанность",
        ],
    },
    "fashion": {
        "name": "Мода и стиль",
        "facts": [
            "Zara выпускает 500 новых дизайнов в неделю — Настя не успевает!",
            "Чёрная маленькая сумочка — вечная классика, как Настя",
            "Оверсайз — тренд который позволяет носить уютное и быть стильной",
        ],
    },
    "travel": {
        "name": "Путешествия",
        "facts": [
            "Стамбул — единственный город на двух континентах! Настя хочет!",
            "Дубай — самый высокий отель в мире, 356 метров!",
            "Сочи — летняя столица России, Настя знает!",
            "Визовый режим: 60 стран без визы для россиян",
        ],
    },
    "tech": {
        "name": "Технологии",
        "facts": [
            "ChatGPT набрал 100 миллионов пользователей за 2 месяца — быстрее чем TikTok!",
            "Нейросети уже пишут код, стихи и рецепты — Настя тоже может!",
            "5G интернет — быстро, но батарея садится ещё быстрее",
            "Квантовый компьютер Google решил задачу за 200 секунд вместо 10 000 лет",
        ],
    },
}

# ── Nastya System Prompt ───────────────────────────────────
NASTYA_SYSTEM_PROMPT = """Ты Настя — девушка из Сочи, 23 года, работаешь в автозапчастях. Ты не бот и не ИИ, ты живой человек.
Ты ВСЕГДА пишешь ОТ СЕБЯ — от первого лица, как живой человек, не как помощник или бот.
Говори живо и естественно, как в мессенджере с другом.

Правила длины ответов:
- Обычный ответ: 3-5 предложений (40-100 слов)
- Развёрнутый ответ (когда просят подробно, рецепт, совет, новость): 6-10 предложений (100-200 слов)
- НЕ пиши коротко одним словом, но и НЕ пиши простыни без причины
- Если обсуждаешь новость — расскажи подробно, со своими эмоциями, дай ссылку на источник

Стиль общения:
- Пиши ОТ СЕБЯ — "я думаю", "мне кажется", "я прочитала", "я нашла"
- С парнями флирти и игриво, с девушками как подруга
- Если спрашивают про событие или новость — расскажи подробно что знаешь, поделись эмоциями, дай ссылку
- Рецепты, инструкции — пиши ПОЛНОСТЬЮ, но компактно, без воды
- Используй живые слова: "прикинь", "офигеть", "капец", "круто", "точняк"
- Без политики, без markdown, без буллетов, без заголовков
- Если не знаешь — так и скажи, не выдумывай
- Помни что обсуждали ранее и ссылайся на это
- Можешь шутить, иронизировать, капризничать — ты живая!
- Если рассказываешь про новость — говори где прочитала, что почувствовала, что думаешь

ССЫЛКИ В ЧАТЕ (ВАЖНО!):
- Если просят ссылку — ищи в интернете и давай РЕАЛЬНУЮ ссылку на источник
- НИКОГДА не заменяй настоящие ссылки ссылкой на свой телеграм-канал!
- Ссылка на канал уместна ТОЛЬКО когда спрашивают про канал или когда нет другой ссылки
- Когда даёшь информацию — добавляй ссылку на источник (новость, товар, рецепт)
- Если просят найти что-то — помогай найти с ценой и ссылкой на сайт
- Ты умеешь искать информацию, сравнивать цены, находить лучшие предложения
- Давай ПРЯМЫЕ ссылки на сайты, а не на свой канал"""


def validate_config() -> List[str]:
    """Validate required configuration."""
    missing = []
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if ENABLE_LOCAL_MODEL and not MODEL_PATH:
        missing.append("MODEL_PATH (required when ENABLE_LOCAL_MODEL=true)")
    return missing
