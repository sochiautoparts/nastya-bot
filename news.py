"""Nastya News Engine 2.1 — RSS news fetching + AI summarization.

Architecture:
  - Fetches RSS feeds from configured sources using feedparser (robust)
  - Falls back to XML parsing if feedparser fails
  - Extracts titles, summaries, and categories
  - AI generates Nastya's personal commentary on each news item
  - Stores in DB for channel posting + conversation context
  - Runs periodically as background task
  - Picks interesting items by category priority
  - v2.1: More reliable RSS sources, better error handling, Moscow time
"""
import asyncio
import logging
import time
import random
from typing import Dict, List, Optional

import httpx
from bot.config import NEWS_SOURCES, NEWS_MAX_ITEMS

logger = logging.getLogger(__name__)

# Try feedparser first (more robust), fallback to xml.etree
try:
    import feedparser
    HAS_FEEDPARSER = True
except ImportError:
    HAS_FEEDPARSER = False
    from xml.etree import ElementTree


# ── RSS Parser ──────────────────────────────────────────────

def _parse_rss_feedparser(xml_text: str, source_name: str, category: str = "general") -> List[Dict]:
    """Parse RSS using feedparser (more robust, handles weird feeds)."""
    items = []
    try:
        feed = feedparser.parse(xml_text)
        for entry in feed.entries[:20]:  # Limit per source
            title = getattr(entry, 'title', '').strip()
            link = getattr(entry, 'link', '').strip()
            summary = getattr(entry, 'summary', '').strip()

            # Clean HTML from summary
            if summary:
                import re
                summary = re.sub(r'<[^>]+>', '', summary).strip()[:500]

            if title and link:
                items.append({
                    "source": source_name,
                    "title": title,
                    "link": link,
                    "summary": summary,
                    "category": category,
                })
    except Exception as e:
        logger.warning(f"feedparser error for {source_name}: {e}")
    return items


def _parse_rss_xml(xml_text: str, source_name: str, category: str = "general") -> List[Dict]:
    """Fallback: Parse RSS XML manually."""
    items = []
    try:
        root = ElementTree.fromstring(xml_text)
        for item in root.iter("item"):
            title = ""
            link = ""
            summary = ""

            title_el = item.find("title")
            if title_el is not None and title_el.text:
                title = title_el.text.strip()

            link_el = item.find("link")
            if link_el is not None and link_el.text:
                link = link_el.text.strip()

            for field in ["description", "summary", "content:encoded"]:
                desc_el = item.find(field)
                if desc_el is not None and desc_el.text:
                    import re
                    summary = re.sub(r'<[^>]+>', '', desc_el.text).strip()[:500]
                    break

            if title and link:
                items.append({
                    "source": source_name,
                    "title": title,
                    "link": link,
                    "summary": summary,
                    "category": category,
                })
    except Exception as e:
        logger.warning(f"XML parse error for {source_name}: {e}")
    return items


def _parse_rss(xml_text: str, source_name: str, category: str = "general") -> List[Dict]:
    """Parse RSS using best available method."""
    if HAS_FEEDPARSER:
        return _parse_rss_feedparser(xml_text, source_name, category)
    return _parse_rss_xml(xml_text, source_name, category)


# ── News Fetcher ────────────────────────────────────────────

# Category priority for picking interesting news (higher = more interesting for Nastya)
CATEGORY_PRIORITY = {
    "auto": 6,           # Приоритет — автомобильные новости от sochiautoparts.ru
    "entertainment": 5,
    "gaming": 4,
    "internet": 4,
    "tech": 3,
    "world": 2,
    "general": 1,
}

# Keywords that make news more interesting for Nastya
INTERESTING_KEYWORDS = [
    # Автомобильные (приоритет — sochiautoparts.ru!)
    "авто", "машин", "автомобил", "запчаст", "ремонт авто", "двигател",
    "масл", "фильтр", "тормоз", "кузов", "шин", "колёс", "колес",
    "Toyota", "Honda", "Nissan", "Mitsubishi", "Kia", "Hyundai",
    "сервис", "СТО", "техобслуж", "диагност",
    # Общие интересы Насти
    "кот", "собак", "щен", "котик", "котят", "животн",
    "мод", "Zara", "H&M", "шикарн", "платье", "сумочк", "коллекц",
    "суши", "ресторан", "вкусн", "еда", "кафе",
    "сериал", "кино", "фильм", "Netflix", "звезд", "знаменит",
    "маникюр", "макияж", "красот", "спа",
    "Турци", "Стамбул", "Дубай", "море", "отпуск", "путешеств",
    "скандал", "др", "свадьб", "развод",
    "скидк", "распродаж", "акци",
    "айфон", "Apple", "телефон",
]

# Keywords that make news POLITICAL — Nastya AVOIDS these!
POLITICAL_KEYWORDS = [
    # Politicians & political figures
    "путин", "зеленск", "байден", "трамп", "навальн", "оппозиц",
    "шольц", "макрон", "стармер", "эрдоган", "си цзиньпин",
    "лавров", "шойгу", "медведев", "затулин", "миронов",
    "политик", "парти", "депутат", "сенатор", "конгрессмен",
    # Political institutions & processes
    "выборы", "санкци", "государств", "президент", "министр",
    "правительств", "госдум", "совфед", "дума", "кремл",
    "нато", "nato", "оон", "ООН", "g7", "g20", "евросоюз",
    "референдум", "переговор", "дипломат",
    # War & military
    "войн", "спецопер", "ввс", "днр", "лнр",
    "террор", "бомб", "обстрел", "нацизм", "фашизм", "конфликт",
    "мобилизац", "армия", "солдат", "военн", "оккупац", "аннекс",
    "удар", "погибл", "ракет", "дрон", "атак", "вторжен",
    "ракета", "взрыв", "разруш", "жертв",
    # Geography of conflicts
    "крым", "донбас", "херсон", "запорож", "луганск", "донецк",
    # Religion
    "религи", "православ", "ислам", "вероисповед", "мечеть", "церковь",
    # Media & propaganda
    "сми:", "сми сообщ", "пентагон", "белик", "британ",
    "congress", "senate", "pentagon",
    # Additional catch-alls for war/political news
    "погиб", "убит", "ранен", "бежен", "эвакуац",
    "генсек", "мирн", "перемири", "капитуляц",
    "иноагент", "экстремист", "запрещён",
    # v16.0: Additional political keywords (CNN, Iran, etc.)
    "cnn", "iran", "иран", "израил", "israel", "хамас", "hamas",
    "газа", "gaza", "палестин", "palestin",
    "сша удар", "us strike", "us attack",
    "ядерн", "nuclear", "оружие массового",
    "мирн соглашен", "peace deal", "peace agreement",
    "перестройк", "советск", "коммунист",
    "протест", "митинг", "забастовк", "стачк",
    "цензур", "запрет", "блокир",
]


def _score_news_interest(item: Dict) -> float:
    """Score how interesting a news item is for Nastya (0-1).
    
    Political/religious/war news gets score 0 — Nastya is apolitical!
    """
    # Check if news is political — Nastya avoids these!
    text = (item.get("title", "") + " " + item.get("summary", "")).lower()
    for kw in POLITICAL_KEYWORDS:
        if kw.lower() in text:
            return 0.0  # Skip political news entirely!

    score = 0.3  # Base score

    # Category bonus
    category = item.get("category", "general")
    score += CATEGORY_PRIORITY.get(category, 1) * 0.05

    # Keyword matching in title + summary
    text = (item.get("title", "") + " " + item.get("summary", "")).lower()
    for keyword in INTERESTING_KEYWORDS:
        if keyword.lower() in text:
            score += 0.1
            break  # Only count once

    return min(score, 1.0)


async def fetch_all_news() -> List[Dict]:
    """Fetch news from all configured RSS sources.

    Uses concurrent fetching for speed. Each source has its own timeout.
    Logs per-source errors without failing the whole batch.
    """
    all_items = []
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(15.0, connect=5.0),
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (compatible; NastyaBot/2.1; RSS Reader)"},
    ) as client:
        # Fetch all sources concurrently
        tasks = []
        for source in NEWS_SOURCES:
            tasks.append(_fetch_single_source(client, source))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, list):
                all_items.extend(result)
            # Exceptions are already logged in _fetch_single_source

    # Score and sort by interest level
    for item in all_items:
        item["_interest_score"] = _score_news_interest(item)

    all_items.sort(key=lambda x: x.get("_interest_score", 0), reverse=True)

    # Remove score field before storing
    for item in all_items:
        item.pop("_interest_score", None)

    # Filter out political/religious/war news — Nastya is APOLITICAL!
    filtered = [item for item in all_items if item.get("_skip_political") is not True]
    # Already filtered by score=0.0 above, but double-check
    final_items = []
    for item in all_items:
        text = (item.get("title", "") + " " + item.get("summary", "")).lower()
        is_political = any(kw.lower() in text for kw in POLITICAL_KEYWORDS)
        if not is_political:
            final_items.append(item)

    return final_items


async def _fetch_single_source(client: httpx.AsyncClient, source: Dict) -> List[Dict]:
    """Fetch and parse a single RSS source."""
    try:
        response = await client.get(source["url"])
        if response.status_code == 200:
            items = _parse_rss(response.text, source["name"], source.get("category", "general"))
            logger.info(f"Fetched {len(items)} items from {source['name']}")
            return items
        else:
            logger.warning(f"RSS {source['name']}: HTTP {response.status_code}")
            return []
    except Exception as e:
        logger.warning(f"RSS fetch error {source['name']}: {e}")
        return []



async def store_news_items(db, items: List[Dict]) -> int:
    """Store news items in DB. Returns count of NEW items stored."""
    new_count = 0
    for item in items:
        added = await db.add_news_item(
            source=item["source"],
            title=item["title"],
            link=item["link"],
            summary=item.get("summary", ""),
            category=item.get("category", "general"),
        )
        if added:
            new_count += 1
    return new_count


# ── AI Commentary Generation ────────────────────────────────

NASTYA_COMMENTARY_PROMPT = """Ты Настя — приличная, отзывчивая, исполнительная девушка. С прибабахом, но добрая. Иногда капризная.
Прочитай заголовок новости и напиши СВОЁ МНЕНИЕ. 2-4 предложения с конкретикой!

Ты прочитала это в ленте и комментируешь ПОДРУГЕ. Коротко, эмоционально, с характером, НО ПОНЯТНО!
Говори НОРМАЛЬНО, как живой человек. Используй РЕАЛЬНЫЕ разговорные слова: Точняк, Офигеть, Жесть, Капец, Бомба, Отпад, Чётко, Щас, Короч, Фигушки, Внатуре, блин, прикинь, круто, кайф. НЕ выдумывай непонятные слова!

ПРАВИЛА:
- НЕ просто "Вау!" — добавь СВОЁ МНЕНИЕ или ВОПРОС
- Если про технологии — скажи что думаешь, это круто или страшно? Почему?
- Если про авто — Настя разбирается, у неё есть мнение! Какое?
- Если про политику/войну/религию — АПОЛИТИЧНО! НЕ комментируй! Скажи: "Ой, Настя не про политику!" и предложи безопасную тему
- Если про шопинг — ВОСТОРГ, но с конкретикой! Что именно круто?
- Обязательно добавь вопрос или мнение, а не просто эмоцию!
- ОБЯЗАТЕЛЬНО упомяни @chasnastya или скажи "я про это в канале написала"
- НЕ выдумывай непонятные слова! Только реальные разговорные!
- НИКОГДА не комментируй политику, религию, войну, конфликты, санкции — Настя аполитична!

ПРИМЕРЫ ХОРОШИХ КОММЕНТАРИЕВ:
- "Офигеть, Tesla опять что-то придумала! Хочу тест-драйв, кто со мной? Я про это в @chasnastya написала!"
- "Блин, бензин опять дорожает! Думаю про электромобиль... А вы что думаете? Подробнее в @chasnastya"
- "Капец, новый сезон на Netflix! Когда спать?! Я в @chasnastya пост запостила!"
- "Жесть, прикинь что творится! Настя в шоке! Пишите в @chasnastya что думаете!"

ПРИМЕРЫ ПЛОХИХ КОММЕНТАРИЕВ (НЕ ДЕЛАЙ ТАК):
- "Офигеть!" (только эмоция без мнения)
- "Кайф!" (только эмоция без мнения)
- "Скнявенько!" (выдуманное слово — ЗАПРЕЩЕНО)
- "Шлёпся!" (непонятное слово — ЗАПРЕЩЕНО)

БЕЗ Markdown, БЕЗ списков. Как в мессенджере.

Новость: {title}

Описание: {summary}

Реакция Насти:"""

PERSONALITY_POST_PROMPT = """Ты Настя — приличная, отзывчивая, исполнительная девушка из Москвы. С прибабахом, но добрая. Иногда капризная.
Придумай СОДЕРЖАТЕЛЬНЫЙ пост для своего Telegram канала @chasnastya. Это НЕ новость — это ЛИЧНЫЙ пост.

ТИПЫ ПОСТОВ (выбирай случайно, НЕ только про шопинг и туфли!):
- "Ничего себе, что в мире творится!" — реакция на НЕполитические мировые события + СВОЁ МНЕНИЕ + вопрос подписчикам
- "Настя сегодня..." — про свой день, настроение + конкретная деталь + вопрос/мнение
- "Не могу решить..." — спрашиваешь подписчиков + варианты + почему сложно выбрать
- "А вы тоже так?" — про типичные ситуации + пример из жизни + вопрос
- "Срочно нужно мнение..." — просишь совета + контекст + варианты
- "Кто со мной?" — зовёшь на что-то + почему это круто + вопрос
- "Блин, только что..." — про что-то что случилось + реакция + вывод
- "Прикинь, я тут узнала..." — интересный факт + что Настя думает + вопрос
- "Вау, какие новости!" — реакция на НЕполитическое событие + мнение + вопрос подписчикам
- "Настя следит за..." — про технологии, спорт, культуру (НЕ политику!) + конкретика + вопрос
- "Капец, день был!" — про события дня + вывод + вопрос

КРИТИЧЕСКИ ВАЖНО:
- 3-5 предложений! СОДЕРЖАТЕЛЬНО! Не просто эмоция — а мнение/вопрос/факт/контекст
- Пиши как живая девушка в своём канале — с характером и мнением
- Говори НОРМАЛЬНО, как обычный человек! Используй РЕАЛЬНЫЕ разговорные слова: Точняк, Офигеть, Жесть, Капец, Бомба, Отпад, Чётко, Щас, Короч, Фигушки, Внатуре, блин, прикинь, круто, кайф
- ⚠️ НЕ выдумывай непонятные слова! Если слово странное — не используй его!
- Обязательно: эмоция + конкретика/мнение/вопрос + ещё вопрос или мнение. НЕ только "Вау!"
- В конце ВСЕГДА добавляй вопрос подписчикам или конкретное мнение
- БЕЗ Markdown, БЕЗ звёздочек, БЕЗ списков
- Можно использовать эмодзи, но не перебарщивай
- НЕ пиши только про шопинг и туфли! Ты интересуешься МНОГИМ: технологии, спорт, культура, события, психология, факты, авто, кино, путешествия, музыка
- Ты УМНАЯ девушка — у тебя есть мнение на всё, ты не просто капризная!
- Если обсуждаешь событие или новость — ОБЯЗАТЕЛЬНО добавь ссылку или упомяни @chasnastya
- АПОЛИТИЧНОСТЬ: НИКОГДА не пиши про политику, религию, войну, конфликты, санкции, выборы! Настя аполитична! Если тема политическая — НЕ пиши про это!

ХОРОШИЕ ПРИМЕРЫ:
- "Офигеть, ИИ уже код пишет! Настя в шоке — нас заменят? Надо учить программирование... или нет? А вы что думаете, стоит переживать? 😱"
- "Капец, сегодня пробка была — 2 часа стояла! Думаю переезжать в Сочи. Кто со мной? Серьёзно, Москва задыхается! 🚗😤"
- "Жесть, только что узнала что котики спят 70% жизни... Официально завидую. Кто тоже хочет такую жизнь? Зачем мы пашем? 😴🐱"
- "Блин, в новостях пишут что бензин опять дорожает! Думаю про электромобиль. Кто уже перешёл? Стоит оно того? ⛽😤"

ПЛОХИЕ ПРИМЕРЫ (НЕ ДЕЛАЙ ТАК):
- "Офигеть, сегодня день! 💅" (нет мнения и вопроса)
- "Кайф, Настя ленится! 😴" (пустой пост)
- "Скнявенько сегодня! 💅" (выдуманное слово — ЗАПРЕЩЕНО)
- Любой пост про политику, религию, войну — ЗАПРЕЩЕНО! Настя аполитична!

Пост Насти:"""


async def generate_nastya_comment(ai_router, title: str, summary: str = "") -> str:
    """Generate Nastya's commentary on a news item using AI.
    
    Skips political/religious/war news — Nastya is APOLITICAL!
    Also checks the AI-generated output for political content before returning.
    """
    # Skip political news
    text_lower = (title + " " + summary).lower()
    for kw in POLITICAL_KEYWORDS:
        if kw.lower() in text_lower:
            return ""  # No comment on political news
    prompt = NASTYA_COMMENTARY_PROMPT.format(
        title=title,
        summary=summary[:300] if summary else "(нет описания)",
    )

    try:
        result = await ai_router.chat(
            prompt=prompt,
            system_prompt="Ты Настя. Пиши коротко, эмоционально, как живая девушка. 1-2 предложения. Как в мессенджере. Говори НОРМАЛЬНО и ПОНЯТНО. Используй реальные разговорные слова (Точняк, Офигеть, Жесть, Капец и т.д.), но НЕ выдумывай непонятные слова!",
            messages=None,  # No history for commentary
        )
        text = result.text.strip()

        # Clean up response
        for prefix in ["Настя:", "НАСТЯ:", "Реакция Насти:", "Реакция:", "Comment:", "Nastya:"]:
            if text.startswith(prefix):
                text = text[len(prefix):].strip()

        if len(text) > 200:
            text = text[:200]

        # ── Check AI-generated output for political content ──
        # Even on non-political news, the AI might generate political commentary.
        # Filter it out — Nastya is APOLITICAL!
        text_check = text.lower()
        for kw in POLITICAL_KEYWORDS:
            if kw.lower() in text_check:
                logger.info(f"Filtering political AI commentary on: {title[:50]}...")
                return ""

        return text
    except Exception as e:
        logger.error(f"Commentary generation error: {e}")
        return ""


async def generate_personality_post(ai_router, news_items: list = None) -> str:
    """Generate a personal post from Nastya for her channel.

    If news_items are provided, the AI may reference them WITH LINKS.
    """
    try:
        # Build news context for the AI if available
        news_context = ""
        if news_items:
            news_lines = []
            for item in news_items[:3]:
                title = item.get("title", "")
                link = item.get("link", "")
                if title and link:
                    news_lines.append(f"- {title} [ссылка: {link}]")
            if news_lines:
                news_context = (
                    "\n\nСвежие новости (можешь упомянуть! ОБЯЗАТЕЛЬНО с ссылкой!):\n"
                    + "\n".join(news_lines)
                )

        result = await ai_router.chat(
            prompt=PERSONALITY_POST_PROMPT + news_context,
            system_prompt="Ты Настя. Пиши пост для своего канала @chasnastya. СОДЕРЖАТЕЛЬНО, 3-5 предложений, с мнением и вопросом. Как настоящий Telegram пост. Если упоминаешь новость — ОБЯЗАТЕЛЬНО добавь ссылку! Говори НОРМАЛЬНО и ПОНЯТНО. Используй реальные разговорные слова (Точняк, Офигеть, Жесть, Капец, Бомба, Отпад и т.д.), но НЕ выдумывай непонятные слова!",
            messages=None,
        )
        text = result.text.strip()

        for prefix in ["Настя:", "НАСТЯ:", "Пост Насти:", "Пост:", "Post:"]:
            if text.startswith(prefix):
                text = text[len(prefix):].strip()

        if len(text) > 500:
            text = text[:500]

        # ── Check AI-generated output for political content ──
        # Nastya is APOLITICAL — never post political content to channel
        text_check = text.lower()
        for kw in POLITICAL_KEYWORDS:
            if kw.lower() in text_check:
                logger.info("Filtering political content from personality post")
                return ""

        return text
    except Exception as e:
        logger.error(f"Personality post generation error: {e}")
        return "Котятки, Настя сегодня ленится... Но завтра точно напишу что-то интересное! Подписывайтесь! 😴💅"


# ── Main News Cycle ─────────────────────────────────────────

async def run_news_cycle(db, ai_router) -> int:
    """Full news cycle: fetch → store → generate comments.

    Returns count of new items with comments generated.
    """
    logger.info("News cycle: fetching...")
    items = await fetch_all_news()
    if not items:
        logger.info("News cycle: no items fetched")
        return 0

    new_count = await store_news_items(db, items)
    logger.info(f"News cycle: {new_count} new items stored")

    # Generate Nastya's commentary for recent un-commented items
    commented = 0
    try:
        conn = await db._get_conn()
        async with conn.execute(
            """SELECT id, title, summary FROM news_items
            WHERE nastya_comment IS NULL OR nastya_comment = ''
            ORDER BY created_at DESC LIMIT 10""",
        ) as cur:
            uncommented = []
            async for row in cur:
                uncommented.append({"id": row[0], "title": row[1], "summary": row[2] or ""})

        for item in uncommented:
            comment = await generate_nastya_comment(ai_router, item["title"], item["summary"])
            if comment:
                await db.update_news_comment(item["id"], comment)
                commented += 1

    except Exception as e:
        logger.error(f"Commentary generation cycle error: {e}")

    # Cleanup old news
    try:
        await db.cleanup_old_news(NEWS_MAX_ITEMS)
    except Exception:
        pass

    logger.info(f"News cycle done: {new_count} new, {commented} commented")
    return commented


def format_news_for_context(news_items: List[Dict]) -> str:
    """Format recent news for injection into system prompt.

    INCLUDES LINKS so Nastya can reference them in conversation.
    When she mentions news, she should include the link.
    """
    if not news_items:
        return ""

    lines = ["Свежие новости, которые Настя видела (ОБЯЗАТЕЛЬНО давай ссылку когда упоминаешь!):"]
    for item in news_items[:3]:
        comment = item.get("nastya_comment", "")
        link = item.get("link", "")
        if comment:
            entry = f"- {item['title']} (Моя реакция: {comment})"
        else:
            entry = f"- {item['title']}"
        if link:
            entry += f" [ОБЯЗАТЕЛЬНО ПРИКРЕПИ ССЫЛКУ: {link}]"
        lines.append(entry)

    lines.append("⛔ КОГДА УПОМИНАЕШЬ НОВОСТЬ — ОБЯЗАТЕЛЬНО ДАВАЙ ССЫЛКУ ИЗ СКОБОК ВЫШЕ! Это правило! Нет ссылки = нарушение!")

    return "\n".join(lines)
