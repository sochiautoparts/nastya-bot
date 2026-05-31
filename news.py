"""Nastya News Engine — RSS news fetching + AI summarization.

Architecture:
  - Fetches RSS feeds from configured sources
  - Extracts titles and summaries
  - AI generates Nastya's personal commentary on each news item
  - Stores in DB for channel posting + conversation context
  - Runs periodically as background task
"""
import logging
import hashlib
import time
import random
from typing import Dict, List, Optional
from xml.etree import ElementTree

import httpx

from bot.config import NEWS_SOURCES, NEWS_MAX_ITEMS

logger = logging.getLogger(__name__)


# ── RSS Parser ──────────────────────────────────────────────

def _parse_rss(xml_text: str, source_name: str) -> List[Dict]:
    """Parse RSS XML and extract news items."""
    items = []
    try:
        root = ElementTree.fromstring(xml_text)

        # Handle different RSS formats
        # Standard RSS 2.0
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

            # Try different summary fields
            for field in ["description", "summary", "content:encoded"]:
                desc_el = item.find(field)
                if desc_el is not None and desc_el.text:
                    summary = desc_el.text.strip()[:500]
                    break

            if title and link:
                items.append({
                    "source": source_name,
                    "title": title,
                    "link": link,
                    "summary": summary,
                })

    except ElementTree.ParseError as e:
        logger.warning(f"RSS parse error for {source_name}: {e}")
    except Exception as e:
        logger.warning(f"RSS processing error for {source_name}: {e}")

    return items


# ── News Fetcher ────────────────────────────────────────────

async def fetch_all_news() -> List[Dict]:
    """Fetch news from all configured RSS sources."""
    all_items = []
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(15.0, connect=5.0),
        follow_redirects=True,
        headers={"User-Agent": "NastyaBot/2.0 (RSS Reader)"},
    ) as client:
        for source in NEWS_SOURCES:
            try:
                response = await client.get(source["url"])
                if response.status_code == 200:
                    items = _parse_rss(response.text, source["name"])
                    for item in items:
                        item["category"] = source.get("category", "general")
                    all_items.extend(items)
                    logger.info(f"Fetched {len(items)} items from {source['name']}")
                else:
                    logger.warning(f"RSS {source['name']}: HTTP {response.status_code}")
            except Exception as e:
                logger.warning(f"RSS fetch error {source['name']}: {e}")

    return all_items


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

NASTYA_COMMENTARY_PROMPT = """Ты Настя — капризная, наглая, но обаятельная девушка из Москвы.
Прочитай заголовок новости и напиши 1-2 предложения своей реакции.

ПРАВИЛА:
- Пиши как живая девушка, не как новости ведущий
- Реагируй эмоционально — удивляешься, злишься, радуешься
- Если новость про шопинг/знаменитости/котиков — восторг!
- Если про что-то скучное — "не, ну и что" или "скучно"
- Можно добавить своё мнение или каприз
- 1-2 предложения максимум!
- БЕЗ Markdown, БЕЗ списков, БЕЗ звёздочек
- Как будто ты прочитала это в ленте и комментируешь подруге

Новость: {title}

Описание: {summary}

Реакция Насти:"""

PERSONALITY_POST_PROMPT = """Ты Настя — капризная, наглая, но обаятельная девушка из Москвы.
Придумай короткий пост для своего Telegram канала. Это НЕ новость — это личный пост.

ТИПЫ ПОСТОВ (выбирай случайно):
- "Котятки, смотрите что нашла..." — нашла что-то интересное в интернете
- "Настя сегодня..." — про свой день, настроение
- "Не могу решить..." — спрашиваешь подписчиков
- "А вы тоже так?" — про типичные ситуации
- "Срочно нужно мнение..." — просишь совета

ПРАВИЛА:
- 2-3 предложения
- Пиши как живая девушка в своём канале
- Эмоционально и с характером
- В конце иногда добавляй вопрос подписчикам
- БЕЗ Markdown, БЕЗ звёздочек, БЕЗ списков
- Можно использовать эмодзи

Пост Насти:"""


async def generate_nastya_comment(ai_router, title: str, summary: str = "") -> str:
    """Generate Nastya's commentary on a news item using AI."""
    prompt = NASTYA_COMMENTARY_PROMPT.format(
        title=title,
        summary=summary[:300] if summary else "(нет описания)",
    )

    try:
        result = await ai_router.chat(
            prompt=prompt,
            system_prompt="Ты Настя. Пиши коротко, эмоционально, как живая девушка. 1-2 предложения.",
            messages=None,  # No history for commentary
        )
        text = result.text.strip()

        # Clean up response
        for prefix in ["Настя:", "НАСТЯ:", "Реакция Насти:", "Реакция:"]:
            if text.startswith(prefix):
                text = text[len(prefix):].strip()

        # Truncate if too long
        if len(text) > 200:
            text = text[:200]

        return text
    except Exception as e:
        logger.error(f"Commentary generation error: {e}")
        return ""


async def generate_personality_post(ai_router) -> str:
    """Generate a personal post from Nastya for her channel."""
    try:
        result = await ai_router.chat(
            prompt=PERSONALITY_POST_PROMPT,
            system_prompt="Ты Настя. Пиши пост для своего канала. Коротко, живо, с характером.",
            messages=None,
        )
        text = result.text.strip()

        for prefix in ["Настя:", "НАСТЯ:", "Пост Насти:", "Пост:"]:
            if text.startswith(prefix):
                text = text[len(prefix):].strip()

        if len(text) > 300:
            text = text[:300]

        return text
    except Exception as e:
        logger.error(f"Personality post generation error: {e}")
        return "Котятки, Настя сегодня ленится... 😴💅"


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
    """Format recent news for injection into system prompt."""
    if not news_items:
        return ""

    lines = ["Свежие новости, которые Настя видела:"]
    for item in news_items[:3]:
        comment = item.get("nastya_comment", "")
        if comment:
            lines.append(f"- {item['title']} (Реакция Насти: {comment})")
        else:
            lines.append(f"- {item['title']}")

    return "\n".join(lines)
