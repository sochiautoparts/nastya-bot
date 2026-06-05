"""Channel Scanner v1.0 - Scan public Telegram channel for dedup.

Scrapes t.me/s/sochiautoparts (public web version) to get
the last 30-50 post texts. Used by Nastya (chief editor)
to prevent duplicate news in the channel.

HOW IT WORKS:
  - HTTP GET to https://t.me/s/{channel_username}
  - Parse HTML for div.tgme_widget_message_text elements
  - Extract text + message IDs for pagination
  - Support loading older posts via ?before= parameter
  - Cache results for 10 minutes to avoid excessive requests
"""

import asyncio
import hashlib
import logging
import re
import time
from typing import Dict, List, Optional, Set
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

# Cache settings
_CACHE_TTL = 600  # 10 minutes
_cached_posts: List[str] = []
_cached_fingerprints: Set[str] = set()
_cache_time: float = 0

# Target channel
CHANNEL_WEB_URL = "https://t.me/s/sochiautoparts"


def _compute_fingerprint(text: str) -> str:
    """Compute a fingerprint for dedup: lowercase, strip punctuation, first 8 words."""
    text_lower = text.lower().strip()
    # Remove common filler words
    text_lower = re.sub(r'\b(в|на|с|о|у|по|из|за|от|до|к|не|и|но|а|что|как|это|тот|этот|для|при|же|ли|бы|уже|ещё|еще|также|тоже|или)\b', '', text_lower)
    # Remove non-alpha
    text_lower = re.sub(r'[^a-zа-яё0-9\s]', '', text_lower)
    words = text_lower.split()[:8]
    return ' '.join(words)


async def fetch_channel_posts(max_posts: int = 50) -> List[str]:
    """Fetch recent post texts from the public web version of the channel.

    Returns list of post text strings (newest first).
    Caches results for 10 minutes.
    """
    global _cached_posts, _cached_fingerprints, _cache_time

    # Return cache if fresh
    if _cached_posts and (time.time() - _cache_time) < _CACHE_TTL:
        logger.info(f"Channel scanner: using cached posts ({len(_cached_posts)} posts)")
        return _cached_posts[:max_posts]

    posts: List[str] = []
    last_msg_id: Optional[int] = None

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0),
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; NastyaBot/62.0; Channel Scanner)",
                "Accept": "text/html",
            },
        ) as client:
            # Fetch up to 3 pages (each has ~20 posts)
            for page in range(3):
                url = CHANNEL_WEB_URL
                if last_msg_id:
                    url += f"?before={last_msg_id}"

                response = await client.get(url)
                if response.status_code != 200:
                    logger.warning(f"Channel scanner: HTTP {response.status_code} for {url}")
                    break

                html = response.text

                # Extract message texts using regex
                # Pattern: <div class="tgme_widget_message_text" ...>CONTENT</div>
                text_pattern = re.compile(
                    r'<div\s+class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
                    re.DOTALL | re.IGNORECASE
                )

                page_posts = []
                for match in text_pattern.finditer(html):
                    raw_text = match.group(1)
                    # Clean HTML tags
                    clean = re.sub(r'<[^>]+>', '', raw_text).strip()
                    # Decode HTML entities
                    clean = clean.replace('&amp;', '&').replace('&lt;', '<')
                    clean = clean.replace('&gt;', '>').replace('&quot;', '"')
                    clean = clean.replace('&#39;', "'").replace('&nbsp;', ' ')
                    clean = re.sub(r'\s+', ' ', clean).strip()
                    if clean and len(clean) > 10:  # Skip very short posts
                        page_posts.append(clean)

                posts.extend(page_posts)

                # Find the earliest message ID for pagination
                # Pattern: data-post="{channel}/{msg_id}"
                msg_id_pattern = re.compile(r'data-post="[^/]+/(\d+)"')
                msg_ids = [int(m) for m in msg_id_pattern.findall(html)]
                if msg_ids:
                    last_msg_id = min(msg_ids)
                else:
                    break  # No more pages

                if len(posts) >= max_posts:
                    break

                # Small delay between pages
                await asyncio.sleep(0.5)

    except Exception as e:
        logger.warning(f"Channel scanner error: {e}")

    # Update cache
    if posts:
        _cached_posts = posts
        _cached_fingerprints = {_compute_fingerprint(p) for p in posts}
        _cache_time = time.time()

    logger.info(f"Channel scanner: fetched {len(posts)} posts from {CHANNEL_WEB_URL}")
    return posts[:max_posts]


async def is_duplicate_in_channel(text: str, threshold: float = 0.65) -> bool:
    """Check if a text is a duplicate of recent channel posts.

    Uses fingerprint matching for fast comparison.
    Also does word-overlap comparison for semantic dedup.

    Args:
        text: The text to check
        threshold: Overlap threshold (0-1). 0.65 = 65% word overlap = duplicate.

    Returns:
        True if the text appears to be a duplicate
    """
    global _cached_fingerprints

    # Ensure we have cached posts
    if not _cached_posts or (time.time() - _cache_time) > _CACHE_TTL:
        await fetch_channel_posts()

    # Quick fingerprint check
    fp = _compute_fingerprint(text)
    if fp in _cached_fingerprints:
        logger.info(f"Channel dedup: fingerprint match for '{text[:60]}...'")
        return True

    # Word-overlap check against cached posts
    text_words = set(text.lower().split())
    if not text_words:
        return False

    for cached_post in _cached_posts:
        cached_words = set(cached_post.lower().split())
        if not cached_words:
            continue

        # Calculate Jaccard-like overlap
        intersection = text_words & cached_words
        union = text_words | cached_words
        if not union:
            continue

        overlap = len(intersection) / len(union)
        if overlap >= threshold:
            logger.info(
                f"Channel dedup: word overlap {overlap:.0%} with "
                f"'{cached_post[:60]}...'"
            )
            return True

    return False


async def get_channel_context_for_prompt(max_items: int = 10) -> str:
    """Get recent channel posts as context for AI prompt.

    This allows the AI to know what was recently posted
    and avoid creating duplicate content.

    Returns:
        String with recent post summaries for AI context.
    """
    posts = await fetch_channel_posts(max_posts=max_items)
    if not posts:
        return ""

    summaries = []
    for i, post in enumerate(posts[:max_items], 1):
        # Truncate each post to first 80 chars for context
        short = post[:80] + ("..." if len(post) > 80 else "")
        summaries.append(f"{i}. {short}")

    return (
        "ПОСЛЕДНИЕ ПОСТЫ В КАНАЛЕ (НЕ ПОВТОРЯЙ ЭТО!):\n"
        + "\n".join(summaries)
    )


async def scan_and_inject_context(system_prompt: str) -> str:
    """Scan channel and inject recent posts context into system prompt.

    Used before generating any channel post to ensure no duplication.
    """
    context = await get_channel_context_for_prompt(max_items=10)
    if context:
        return system_prompt + "\n\n" + context
    return system_prompt
