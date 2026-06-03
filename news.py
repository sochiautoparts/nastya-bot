"""Nastya News Engine 3.1 — RSS-only for DB, AI for channel posts!

v3.1 KEY CHANGES (v44):
  - News sources: ТОЛЬКО русскоязычные! Англоязычные УБРАНЫ!
  - Автомобильные новости: ТОЛЬКО sochiautoparts.ru/rss.xml!
  - Template commentary still used for DB storage (fast, no AI)
  - AI commentary for channel posts is handled by channel.py (v44!)
  - Added Russian news sources: РИА Новости, Лента.ру, Вести

Architecture:
  - Fetches RSS feeds from configured sources using feedparser (robust)
  - Falls back to XML parsing if feedparser fails
  - Extracts titles, summaries, and categories
  - Template-based Nastya commentary for DB — NO AI, instant and clean
  - AI-generated commentary for channel posts — handled separately
  - Stores in DB + JSON file for channel posting + conversation context
  - Runs periodically as background task
  - Picks interesting items by category priority (auto = highest!)
"""

import asyncio
import json
import logging
import time
import random
from pathlib import Path
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

# JSON file for news cache — bot can read this directly
NEWS_JSON_PATH = Path("data/news_cache.json")


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
# v44: auto = HIGHEST priority (sochiautoparts.ru — the bot's niche!)
CATEGORY_PRIORITY = {
    "auto": 8,           # ПРИОРИТЕТ — автомобильные новости от sochiautoparts.ru!
    "entertainment": 5,
    "gaming": 4,
    "internet": 4,
    "tech": 3,
    "science": 3,
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
        headers={"User-Agent": "Mozilla/5.0 (compatible; NastyaBot/3.0; RSS Reader)"},
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


# ── Template-Based Commentary — NO AI! ──────────────────────
# v3.0: Вместо AI-генерации комментариев используем шаблоны.
# Это быстрее (мгновенно), надёжнее (нет мусора), и не грузит CPU.

# Шаблоны комментариев по категориям — Настя говорит живо и коротко
COMMENTARY_TEMPLATES = {
    "auto": [
        "Прикинь, автоновости! Настя в курсе!",
        "О, про машины! Точняк, надо знать!",
        "Автомобильная тема! Настя следит!",
        "Круто, про тачки! Настя разбирается!",
        "Жесть, про авто! Запомни!",
    ],
    "tech": [
        "Офигеть, технологии! Настя в шоке!",
        "Капец, техно-новость! Кайф!",
        "Ничего себе, технологии! Настя впечатлена!",
        "Прикинь, что придумали! Будущее уже тут!",
        "Техно-жесть! Настя не верит!",
    ],
    "science": [
        "Офигеть, наука! Настя умная!",
        "Капец, открытие! Реально круто!",
        "Ничего себе, наука! Настя в шоке!",
        "Прикинь, учёные выяснили! Жесть!",
        "Умная новость! Настя оценила!",
    ],
    "gaming": [
        "О, гейминг! Настя играет!",
        "Круто, игровая новость! Кто в деле?",
        "Капец, про игры! Настя хочет!",
        "Жесть, геймерская тема! Точняк!",
        "Прикинь, про игры! Настя в деле!",
    ],
    "general": [
        "Прикинь, новость! Настя в курсе!",
        "Офигеть! Настя только что узнала!",
        "Капец, новость! Реально!",
        "Ничего себе! Настя в шоке!",
        "Жесть! Настя не верит!",
        "Круто! Настя следит!",
        "Точняк, интересно! Настя одобряет!",
        "Блин, новость! Настя в курсе!",
    ],
}

# Шаблоны для personality-постов — тоже без AI
PERSONALITY_COMMENTARY = [
    "Настя тут подумала... А вы как считаете?",
    "Прикинь, какая тема! Делитесь мнением!",
    "Офигеть, Настя не может молчать!",
    "Котятки, что думаете по этому поводу?",
    "Блин, Настя в шоке! А вы?",
]


def generate_template_commentary(title: str, category: str = "general") -> str:
    """Generate Nastya's commentary from TEMPLATES — NO AI!

    v3.0: Шаблонные комментарии вместо AI-генерации.
    - Мгновенная генерация (0 мс вместо 15-47 сек)
    - Нет мусора от маленьких моделей
    - Не грузит CPU — модель свободна для чата
    - Качество гарантировано — шаблоны написаны вручную
    """
    # Get templates for category, fallback to general
    templates = COMMENTARY_TEMPLATES.get(category, COMMENTARY_TEMPLATES["general"])
    comment = random.choice(templates)

    # Add a reaction based on keywords in the title
    title_lower = title.lower()

    # Interesting keywords get extra enthusiasm
    for keyword in ["кот", "котик", "собак", "щен"]:
        if keyword in title_lower:
            comment = random.choice([
                "Ой, ми-ми-ми! Настя тащится!",
                "Капец, мило! Настя не может!",
                "Офигеть, какие милые! Настя в восторге!",
            ])
            return comment

    for keyword in ["авто", "машин", "запчаст", "ремонт", "toyota", "honda"]:
        if keyword in title_lower:
            comment = random.choice([
                "О, про тачки! Настя разбирается в авто!",
                "Авто-тема! Точняк, Настя в курсе!",
                "Прикинь, про машины! Настя знает!",
            ])
            return comment

    for keyword in ["скидк", "распродаж", "акци", "free"]:
        if keyword in title_lower:
            comment = random.choice([
                "Скидки?! Настя бежит!",
                "Распродажа! Настя уже смотрит!",
                "Офигеть, акция! Надо брать!",
            ])
            return comment

    for keyword in ["айфон", "apple", "телефон", "гаджет"]:
        if keyword in title_lower:
            comment = random.choice([
                "О, Apple! Настя хочет!",
                "Гаджеты! Настя следит за техником!",
                "Капец,新技术! Настя в восторге!",
            ])
            return comment

    for keyword in ["кино", "фильм", "сериал", "netflix"]:
        if keyword in title_lower:
            comment = random.choice([
                "О, кино! Настя обожает!",
                "Сериал! Настя смотрит!",
                "Фильмы! Настя знает что смотреть!",
            ])
            return comment

    # Check for political content — return empty (skip)
    for kw in POLITICAL_KEYWORDS:
        if kw.lower() in title_lower:
            return ""

    return comment


# ── JSON Cache File ─────────────────────────────────────────

def save_news_to_json(items: List[Dict], max_items: int = 100) -> None:
    """Save recent news to a JSON file for easy access.

    The bot can read this file to get news context without querying the DB.
    This is useful for:
    - Quick access to recent news
    - Debugging and monitoring
    - Channel posting (no DB query needed)
    """
    try:
        NEWS_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)

        # Load existing data
        existing = []
        if NEWS_JSON_PATH.exists():
            try:
                with open(NEWS_JSON_PATH, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except (json.JSONDecodeError, IOError):
                existing = []

        # Merge new items (by link dedup)
        existing_links = {item.get("link", "") for item in existing}
        for item in items:
            if item.get("link", "") not in existing_links:
                existing.append(item)

        # Sort by interest score (if available) or just keep recent
        # Keep only max_items
        existing = existing[-max_items:]

        # Save
        with open(NEWS_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

        logger.info(f"Saved {len(existing)} news items to {NEWS_JSON_PATH}")

    except Exception as e:
        logger.error(f"Failed to save news JSON: {e}")


def load_news_from_json() -> List[Dict]:
    """Load recent news from JSON cache file.

    Returns list of news items with title, link, summary, category, etc.
    """
    try:
        if NEWS_JSON_PATH.exists():
            with open(NEWS_JSON_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Failed to load news JSON: {e}")
    return []


# ── Main News Cycle — NO AI! ────────────────────────────────

async def run_news_cycle(db, ai_router=None) -> int:
    """Full news cycle: fetch → store → generate template comments.

    v3.0: NO AI for news commentary!
    - RSS fetch → SQLite + JSON file
    - Template-based commentary — instant, no CPU load
    - AI is NOT called at all for news
    - ai_router parameter kept for compatibility but NOT used
    """
    logger.info("News cycle: fetching RSS feeds...")
    items = await fetch_all_news()
    if not items:
        logger.info("News cycle: no items fetched")
        return 0

    new_count = await store_news_items(db, items)
    logger.info(f"News cycle: {new_count} new items stored")

    # Save to JSON file for easy access
    try:
        save_news_to_json(items)
    except Exception as e:
        logger.warning(f"JSON save error: {e}")

    # Generate Nastya's commentary using TEMPLATES (NO AI!)
    commented = 0
    try:
        conn = await db._get_conn()
        async with conn.execute(
            """SELECT id, title, summary, category FROM news_items
            WHERE nastya_comment IS NULL OR nastya_comment = ''
            ORDER BY created_at DESC LIMIT 5""",
        ) as cur:
            uncommented = []
            async for row in cur:
                uncommented.append({
                    "id": row[0],
                    "title": row[1],
                    "summary": row[2] or "",
                    "category": row[3] or "general",
                })

        for item in uncommented:
            # Template-based commentary — NO AI, instant!
            comment = generate_template_commentary(item["title"], item["category"])
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

    logger.info(f"News cycle done: {new_count} new, {commented} commented (templates, no AI)")
    return commented


def format_news_for_context(news_items: List[Dict]) -> str:
    """Format recent news for injection into system prompt.

    v3.0: Short — only 2 headlines with links.
    Bot can use this to reference news naturally in chat.
    """
    if not news_items:
        return ""

    parts = []
    for item in news_items[:2]:
        title = item.get("title", "")
        link = item.get("link", "")
        if title:
            entry = title
            if link:
                entry += f" ({link})"
            parts.append(entry)

    if parts:
        return f"Новости: {'; '.join(parts)}."
    return ""
