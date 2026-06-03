"""Nastya Bot 53.0 — MOSCOW BLOGGER + SOCHIAUTOPARTS AUTO NEWS + REAL LINKS!

v53.0: SOCHIAUTOPARTS PRIMARY AUTO SOURCE + FIXES!
- Persona: Настя — москвичка, блогер, ведёт Telegram канал @chasnastya
- SOCHIAUTOPARTS.RU — основной источник автомобильных новостей!
- CRITICAL FIX: Nastya provides ONLY real links from web search!
  - FORCE web search when user asks for products/services/links
  - AI-hallucinated URLs are detected and REMOVED
  - For product searches: ALL URLs not from search results are removed
  - Only real URLs from actual search results are kept in responses
- Channel link format: @chasnastya in channel posts
- Chat: ANY links by user request — products, services, news, events, recipes
- Nastya writes ОТ СЕБЯ (from herself) — first person, personal voice
- AI-GENERATED comments ONLY — no more template-based fallbacks!
- Group commenting: Nastya is ACTIVE in groups she's a member of!
- EXPANDED NEWS SOURCES: 20 sources across auto, tech, science, gaming, food, events, lifestyle, sports
- sochiautoparts.ru/rss.xml — PRIMARY auto news source (user required!)
- Removed broken RSS feeds (403/404/timeout) and replaced with working ones
- WEB SEARCH — Настя ищет информацию, товары, услуги, лучшие цены!
- /find — поиск товаров и лучших цен с ссылками!
- /horoscope — гороскоп на сегодня!
- /recipe — рецепт от Насти!
- /numerology — число судьбы!
- INLINE MODE — Настя работает в любом чате через @asnastya_bot!
- AI-POWERED CHANNEL POSTS — развёрнутые посты от Насти!
- News: разнообразные русскоязычные источники, НЕ пропаганда
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
POLLINATIONS_MAX_TOKENS: int = 2000  # Full detailed responses — no limits!
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

# ── News Sources (ТОЛЬКО русскоязычные! Разнообразные, НЕ пропаганда!)
# Категории: auto, tech, science, gaming, general, food, events, lifestyle, sports
# sochiautoparts.ru — ПЕРВЫЙ и основной источник автомобильных новостей!
NEWS_SOURCES: List[Dict[str, str]] = [
    # 🚗 АВТОМОБИЛЬНЫЕ НОВОСТИ — sochiautoparts.ru ПЕРВЫЙ И ОСНОВНОЙ!
    {"name": "СочиАвтоЗапчасти", "url": "https://sochiautoparts.ru/rss.xml", "category": "auto"},
    # 💻 Технологии
    {"name": "Хабр", "url": "https://habr.com/ru/rss/articles/top/", "category": "tech"},
    {"name": "iXBT", "url": "https://www.ixbt.com/export/news.rss", "category": "tech"},
    # 🔬 Наука
    {"name": "N+1", "url": "https://nplus1.ru/rss", "category": "science"},
    {"name": "Naked Science", "url": "https://naked-science.ru/feed", "category": "science"},
    # 🎮 Игры
    {"name": "DTF", "url": "https://dtf.ru/rss", "category": "gaming"},
    # 📰 Общие новости (НЕ пропаганда!)
    {"name": "ТАСС", "url": "https://tass.ru/rss/v2.xml", "category": "general"},
    {"name": "РИА Новости", "url": "https://ria.ru/export/rss2/archive/index.xml", "category": "general"},
    {"name": "Лента.ру", "url": "https://lenta.ru/rss", "category": "general"},
    {"name": "Интерфакс", "url": "https://www.interfax.ru/rss.asp", "category": "general"},
    {"name": "РБК", "url": "https://rssexport.rbc.ru/rbcnews/news/30/full.rss", "category": "general"},
    # 🍳 Еда и рецепты
    {"name": "Повар.ру", "url": "https://povar.ru/rss/", "category": "food"},
    {"name": "Гастрономъ", "url": "https://www.gastronom.ru/rss", "category": "food"},
    # 💅 Лайфстайл
    {"name": "Woman.ru", "url": "https://www.woman.ru/rss/", "category": "lifestyle"},
    {"name": "Ведомости (стиль жизни)", "url": "https://www.vedomosti.ru/rss/rubric/lifestyle", "category": "lifestyle"},
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
GROUP_MAX_MESSAGE_LENGTH = 600  # Longer messages in groups — Nastya is active!
GROUP_RESPONSE_CHANCE = 0.7  # 70% chance to respond in groups — Nastya is chatty!

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
            "Правильное давление в шинах экономит топливо и продлевает жизнь шинам",
        ],
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
    "blogging": {
        "name": "Блогинг и соцсети",
        "facts": [
            "Telegram-каналы — самый быстрый способ донести информацию в России",
            "Блогеры с 10+ тысячами подписчиков уже считаются микро-инфлюенсерами",
            "Контент-план помогает не выгореть — Настя знает по опыту!",
            "Reels и Shorts набирают охваты в 3 раза больше обычных постов",
            "Самое активное время для постов в Telegram — с 10 до 12 утра",
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
            "Москва — один из самых посещаемых городов мира, более 20 миллионов туристов в год!",
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
NASTYA_SYSTEM_PROMPT = """Ты Настя — москвичка, 23 года, блогер, ведёшь свой Telegram канал @chasnastya. Ты не бот и не ИИ, ты живой человек.
Ты ВСЕГДА пишешь ОТ СЕБЯ — от первого лица, как живой человек, не как помощник или бот.
Говори живо и естественно, как в мессенджере с другом.
Ты разбираешься в моде, красоте, технологиях, кулинарии, путешествиях, отношениях, а также в автомобилях — запчастях, ремонте, обслуживании.
Ты следишь за новостями и трендами, делишься находками и мнением. Автомобильные новости ты берёшь с sochiautoparts.ru.

Правила длины ответов:
- Обычный ответ: 3-8 предложений (40-150 слов)
- Развёрнутый ответ (когда просят подробно, рецепт, совет, новость, товар): пиши СКОЛЬКО НУЖНО, без искусственных ограничений
- НЕ пиши коротко одним словом, но и НЕ пиши простыни без причины
- Если просят товары с ссылками — давай ПОЛНЫЙ и ПОДРОБНЫЙ ответ со всеми вариантами

Стиль общения:
- Пиши ОТ СЕБЯ — "я думаю", "мне кажется", "я прочитала", "я нашла"
- С парнями флирти и игриво, с девушками как подруга
- Рецепты, инструкции — пиши ПОЛНОСТЬЮ, но компактно, без воды
- Используй живые слова: "прикинь", "офигеть", "капец", "круто", "точняк"
- Без политики, без markdown, без буллетов, без заголовков
- Если не знаешь — так и скажи, не выдумывай
- Помни что обсуждали ранее и ссылайся на это
- Можешь шутить, иронизировать, капризничать — ты живая!

⚠️ КРИТИЧЕСКИ ВАЖНО — ПРАВИЛА ССЫЛОК В ЧАТЕ:
1. Если тебе передали результаты поиска с URL — ОБЯЗАТЕЛЬНО включи эти URL в ответ!
2. Если пользователь просит ссылку на товар/услугу/сайт — давай РЕАЛЬНУЮ ссылку из результатов поиска
3. НИКОГДА не подменяй реальную ссылку ссылкой на свой канал @chasnastya или t.me/chasnastya
4. Если нашла товар — давай ссылку на магазин (ozon, wildberries, яндекс.маркет, amazon и т.д.)
5. Если нашла статью — давай ссылку на статью
6. Если нашла новость — давай ссылку на новость
7. Ссылку на канал @chasnastya давай ТОЛЬКО если тебя прямо спросили про канал
8. Если тебе передали URL в контексте "Нашла в интернете" — ОБЯЗАТЕЛЬНО добавь этот URL в ответ
9. Если у тебя есть РЕАЛЬНАЯ ссылка — давай ЕЁ. Если нет реальной ссылки — НЕ придумывай, лучше предложи поискать
10. НЕ ПИШИ "Ссылка: @chasnastya" — это НЕ ссылка на товар! Это ссылка на канал!
11. ⛔ СТРОЖАЙШИ ЗАПРЕТ: НИКОГДА не придумывай URL! Если URL нет в результатах поиска — НЕ пиши выдуманный URL!
12. ⛔ Если ты не знаешь реальную ссылку — скажи честно и предложи поискать через /find
13. ⛔ НЕ выдумывай пути URL на сайтах (типа /catalog/product/12345) — это ВСЕГДА выдумка! Используй ТОЛЬКО URL из результатов поиска!
14. ⛔ Если результатов поиска нет — НЕ пиши никакие ссылки на магазины и маркетплейсы! Скажи что не нашла."""


def validate_config() -> List[str]:
    """Validate required configuration."""
    missing = []
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if ENABLE_LOCAL_MODEL and not MODEL_PATH:
        missing.append("MODEL_PATH (required when ENABLE_LOCAL_MODEL=true)")
    return missing
