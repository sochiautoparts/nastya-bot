"""Nastya News Engine 5.2 — AI-ONLY COMMENTS + SOCHIAUTOPARTS PRIMARY + EXPANDED SOURCES!

v5.2 KEY CHANGES:
  - sochiautoparts.ru/rss.xml — PRIMARY auto news source!
  - auto category = HIGHEST priority for Nastya's channel!
  - Added more auto sources: kolesa.ru, auto.mail.ru
  - Added more general sources: iz.ru, dzen.ru
  - Added tech: vc.ru, sports: euro-football.ru
  - Removed vesti.ru (404)
  - AI-GENERATED Nastya commentary for news — ALWAYS AI, NO templates!
  - Each news item gets a unique, personality-rich comment from AI
  - NO MORE template-based fallbacks — AI only! If AI fails, use generic comment
  - News sources: ТОЛЬКО русскоязычные! Англоязычные УБРАНЫ!
  - AI commentary for channel posts is handled by channel.py

Architecture:
  - Fetches RSS feeds from configured sources using feedparser (robust)
  - Falls back to XML parsing if feedparser fails
  - Extracts titles, summaries, and categories
  - AI-generated Nastya commentary — ALWAYS unique, personal, lively!
  - Generic comment only when ALL AI providers fail (no more templates!)
  - Stores in DB + JSON file for channel posting + conversation context
  - Runs periodically as background task
  - Picks interesting items by category priority (auto = HIGHEST!)
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
# v53: auto = HIGHEST priority (sochiautoparts.ru — PRIMARY source!)
CATEGORY_PRIORITY = {
    "auto": 10,           # 🚗 sochiautoparts.ru — ОСНОВНОЙ источник!
    "food": 7,            # Рецепты и еда — Настя любит готовить!
    "events": 6,          # Мероприятия — Настя хочет везде!
    "lifestyle": 5,       # Стиль и красота — Настина тема!
    "entertainment": 5,
    "gaming": 4,
    "internet": 4,
    "sports": 3,          # Спорт — Настя тоже следит!
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


# v51: REMOVED all template-based commentary — AI ONLY!
# Templates were: COMMENTARY_TEMPLATES and PERSONALITY_COMMENTARY
# Now AI generates ALL comments. If AI fails, we use a generic comment.

# Generic fallback when ALL AI providers fail
_GENERIC_FALLBACK_COMMENTS = [
    "Интересно...",
    "Настя прочитала...",
    "О, новость!",
    "Прикинь...",
]


async def generate_ai_commentary(title: str, summary: str = "", category: str = "general", ai_router=None) -> str:
    """Generate Nastya's commentary using AI — unique and personal!

    v4.0: AI-generated comments instead of templates.
    Falls back to templates only if AI is unavailable.
    """
    if ai_router:
        try:
            category_context = {
                "auto": "Это автомобильная новость — Настя разбирается в автомобилях, фанат BMW! Делись экспертным мнением.",
                "tech": "Это технологическая новость — Настя интересуется гаджетами и технологиями.",
                "science": "Это научная новость — Настя любит науку и удивительные факты.",
                "gaming": "Это игровая новость — Настя играет в игры и следит за индустрией.",
                "food": "Это кулинарная новость — Настя обожает готовить и пробовать новое!",
                "events": "Это анонс мероприятия — Настя хочет everywhere и делится находками!",
                "lifestyle": "Это лайфстал-новость — Настя разбирается в моде, красоте и стиле!",
                "sports": "Это спортивная новость — Настя следит за спортом!",
            }.get(category, "")

            prompt_parts = [f"Новость: {title}"]
            if summary:
                import re as _re
                clean_summary = _re.sub(r'<[^>]+>', '', summary).strip()[:300]
                if clean_summary:
                    prompt_parts.append(f"Краткое содержание: {clean_summary}")
            prompt = "\n".join(prompt_parts)

            result = await ai_router.chat(
                prompt=prompt,
                system_prompt=(
                    "Ты Настя — москвичка, 23 года, блогер. "
                    "Напиши КОРОТКИЙ комментарий к этой новости от себя, от первого лица. "
                    f"{category_context} "
                    "1-2 предложения, живо и эмоционально. "
                    "Используй слова: 'прикинь', 'офигеть', 'капец', 'круто'. "
                    "Без markdown, без буллетов. Не пиши 'Настя' — пиши 'я'. "
                    "НЕ добавляй ссылки."
                ),
                max_tokens=150,
                priority="low",
            )
            if result and result.text:
                comment = result.text.strip()
                # Clean artifacts
                import re as _re2
                comment = _re2.sub(r'<[^>]+>', '', comment)
                comment = _re2.sub(r'^/no_think\s*', '', comment)
                for prefix in ["Настя:", "НАСТЯ:", "Nastya:"]:
                    if comment.startswith(prefix):
                        comment = comment[len(prefix):].strip()
                if len(comment) > 200:
                    comment = comment[:197] + "..."
                if comment and len(comment) > 5:
                    return comment
        except Exception as e:
            logger.warning(f"AI commentary failed, using template: {e}")

    # Fallback: generic comment when ALL AI providers fail
    logger.warning(f"AI commentary failed for: {title[:50]}... Using generic fallback")
    return random.choice(_GENERIC_FALLBACK_COMMENTS)


# v51: generate_template_commentary REMOVED — AI ONLY!


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


# ── Main News Cycle — AI-POWERED! ──────────────────────────────

async def run_news_cycle(db, ai_router=None) -> int:
    """Full news cycle: fetch → store → generate AI comments.

    v4.0: AI-GENERATED commentary — unique, personal, lively!
    - RSS fetch → SQLite + JSON file
    - AI-generated commentary — unique per news item!
    - Template-based fallback only if AI is unavailable
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

    # Generate Nastya's commentary using AI (with template fallback)
    commented = 0
    ai_comments = 0
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
            # AI-generated commentary — unique and personal!
            comment = await generate_ai_commentary(
                title=item["title"],
                summary=item.get("summary", ""),
                category=item["category"],
                ai_router=ai_router,
            )
            if comment:
                await db.update_news_comment(item["id"], comment)
                commented += 1
                # Check if it was AI-generated or generic fallback
                if comment not in _GENERIC_FALLBACK_COMMENTS:
                    ai_comments += 1

    except Exception as e:
        logger.error(f"Commentary generation cycle error: {e}")

    # Cleanup old news
    try:
        await db.cleanup_old_news(NEWS_MAX_ITEMS)
    except Exception:
        pass

    logger.info(f"News cycle done: {new_count} new, {commented} commented ({ai_comments} AI-generated)")
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
