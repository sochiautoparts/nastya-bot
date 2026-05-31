"""Nastya Web Search — search the web for information during conversations.

Enables Nastya to:
  - Search for real-time information when discussing events/news
  - Verify facts and find links to share with users
  - Make conversations more lively with up-to-date knowledge
  - Always include source links when sharing information

Uses DuckDuckGo HTML search (no API key needed) with fallback.
Results are injected into the AI context so Nastya can reference them naturally.
"""
import logging
import re
import time
import random
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# Cache search results to avoid repeated queries
_search_cache: Dict[str, Dict] = {}
_CACHE_TTL = 1800  # 30 minutes
_MAX_CACHE = 100


def _clean_cache():
    """Remove expired cache entries."""
    now = time.time()
    expired = [k for k, v in _search_cache.items() if now - v.get("ts", 0) > _CACHE_TTL]
    for k in expired:
        del _search_cache[k]
    while len(_search_cache) > _MAX_CACHE:
        oldest = min(_search_cache.items(), key=lambda x: x[1].get("ts", 0))
        del _search_cache[oldest[0]]


async def search_web(query: str, num_results: int = 3) -> List[Dict]:
    """Search the web using DuckDuckGo HTML. Returns list of results.

    Each result has: title, snippet, url
    """
    _clean_cache()

    # Check cache
    cache_key = query.lower().strip()
    cached = _search_cache.get(cache_key)
    if cached and time.time() - cached.get("ts", 0) < _CACHE_TTL:
        return cached.get("results", [])[:num_results]

    results = []

    # Try DuckDuckGo HTML search
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0),
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        ) as client:
            # DuckDuckGo HTML search
            response = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query, "kl": "ru-ru"},
            )

            if response.status_code == 200:
                results = _parse_ddg_html(response.text, num_results)

    except Exception as e:
        logger.warning(f"DuckDuckGo search error: {e}")

    # Fallback: try DuckDuckGo instant answer API
    if not results:
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(8.0, connect=4.0),
            ) as client:
                response = await client.get(
                    "https://api.duckduckgo.com/",
                    params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
                )
                if response.status_code == 200:
                    data = response.json()
                    # Try abstract
                    abstract = data.get("Abstract", "")
                    abstract_url = data.get("AbstractURL", "")
                    abstract_title = data.get("Heading", "")
                    if abstract and abstract_url:
                        results.append({
                            "title": abstract_title,
                            "snippet": abstract[:300],
                            "url": abstract_url,
                        })
                    # Try related topics
                    for topic in data.get("RelatedTopics", [])[:3]:
                        if isinstance(topic, dict) and topic.get("Text") and topic.get("FirstURL"):
                            results.append({
                                "title": topic.get("Text", "")[:100],
                                "snippet": topic.get("Text", "")[:300],
                                "url": topic.get("FirstURL", ""),
                            })
        except Exception as e:
            logger.warning(f"DuckDuckGo API fallback error: {e}")

    # Cache results
    if results:
        _search_cache[cache_key] = {"results": results, "ts": time.time()}

    return results[:num_results]


def _parse_ddg_html(html: str, num_results: int) -> List[Dict]:
    """Parse DuckDuckGo HTML search results."""
    results = []

    # Extract result blocks
    # DDG HTML uses class="result" divs
    result_pattern = re.compile(
        r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
        r'<(?:a|td)[^>]+class="result__snippet"[^>]*>(.*?)</(?:a|td)>',
        re.DOTALL | re.IGNORECASE,
    )

    for match in result_pattern.finditer(html):
        if len(results) >= num_results:
            break
        url = match.group(1)
        title = _strip_html(match.group(2)).strip()
        snippet = _strip_html(match.group(3)).strip()

        if title and url:
            # Clean DDG redirect URL
            if url.startswith("//duckduckgo.com/l/"):
                # Extract actual URL from DDG redirect
                actual_url_match = re.search(r'uddg=([^&]+)', url)
                if actual_url_match:
                    from urllib.parse import unquote
                    url = unquote(actual_url_match.group(1))

            results.append({
                "title": title[:200],
                "snippet": snippet[:300],
                "url": url,
            })

    # Fallback: try a simpler pattern if no results
    if not results:
        link_pattern = re.compile(
            r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            re.DOTALL | re.IGNORECASE,
        )
        for match in link_pattern.finditer(html):
            if len(results) >= num_results:
                break
            url = match.group(1)
            title = _strip_html(match.group(2)).strip()
            if title and url and not url.startswith("#"):
                if url.startswith("//duckduckgo.com/l/"):
                    actual_url_match = re.search(r'uddg=([^&]+)', url)
                    if actual_url_match:
                        from urllib.parse import unquote
                        url = unquote(actual_url_match.group(1))
                results.append({
                    "title": title[:200],
                    "snippet": "",
                    "url": url,
                })

    return results


def _strip_html(text: str) -> str:
    """Remove HTML tags from text."""
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def should_search(text: str) -> Optional[str]:
    """Determine if the user message warrants a web search.

    Returns the search query if search is needed, None otherwise.
    Only searches when the topic is clearly about factual/news content.
    """
    text_lower = text.lower()

    # Direct search triggers
    search_triggers = [
        "поищи", "найди", "search", "find", "узнай", "проверь",
        "что такое", "кто такой", "кто такая", "что значит", "что означает",
        "правда ли", "действительно ли", "это правда", "подтверд",
        "сколько стоит", "какой курс", "какая погода", "какая температура",
        "когда будет", "где находится", "где купить", "как доехать",
        "последние новости", "свежие новости", "что нового в",
        "что случилось", "что произошло", "какие события",
    ]

    for trigger in search_triggers:
        if trigger in text_lower:
            # Extract the actual query after the trigger
            idx = text_lower.find(trigger)
            query = text[idx + len(trigger):].strip().rstrip("?!.،")
            if len(query) > 2:
                return query[:100]  # Limit query length
            return text[:100]

    # Question detection — search for factual questions
    question_patterns = [
        r"кто (?:создал|изобрёл|написал|построил|основал)",
        r"когда (?:состоялся|произошёл|начался|закончился|изобрели)",
        r"где (?:находится|проходит|состоится|пройдёт)",
        r"сколько (?:стоит|стоит|человек|жителей|население)",
        r"какой (?:рекорд|результат|счёт|курс|прогноз)",
        r"что (?:произошло|случилось|нового|известно)",
    ]

    for pattern in question_patterns:
        if re.search(pattern, text_lower):
            return text[:100]

    # Event/news discussion triggers — lower threshold
    news_triggers = [
        "в новости", "в новостях", "прочитала что", "говорят что",
        "в интернете пишут", "сегодня произошло", "недавно было",
        "вы слышали про", "ты знаешь про", "расскажи про",
    ]

    for trigger in news_triggers:
        if trigger in text_lower:
            idx = text_lower.find(trigger)
            query = text[idx + len(trigger):].strip().rstrip("?!.،")
            if len(query) > 3:
                return query[:100]

    return None


def format_search_results_for_prompt(results: List[Dict], query: str) -> str:
    """Format search results for injection into AI system prompt.

    Nastya should use these results naturally in her conversation,
    always including the source URL when sharing information.
    """
    if not results:
        return ""

    lines = [
        f"🔍 Настя только что нашла в интернете про '{query}':",
        "ОБЯЗАТЕЛЬНО используй эту информацию и ПРИКРЕПИ ССЫЛКИ! Это правило!",
    ]

    for i, result in enumerate(results[:3], 1):
        title = result.get("title", "")
        snippet = result.get("snippet", "")
        url = result.get("url", "")
        entry = f"{i}. {title}"
        if snippet:
            entry += f" — {snippet[:200]}"
        if url:
            entry += f" [Ссылка: {url}]"
        lines.append(entry)

    lines.append(
        "⛔ Когда обсуждаешь эту информацию — ОБЯЗАТЕЛЬНО добавь ссылку на источник! "
        "НЕТ ССЫЛКИ = НАРУШЕНИЕ! Пиши URL из результатов выше!"
    )

    return "\n".join(lines)


def get_search_link_for_response(results: List[Dict]) -> Optional[str]:
    """Get the most relevant URL from search results for appending to response."""
    if not results:
        return None
    for result in results:
        url = result.get("url", "")
        if url and url.startswith("http"):
            return url
    return None


# ── Poll topic suggestions for channel ──

POLL_TOPICS = [
    {
        "question": "Котятки, какой знак зодиака самый капризный? 🤔",
        "options": ["Овен 🔥", "Близнецы ♊", "Скорпион 🦂", "Настя! 💅"],
    },
    {
        "question": "Срочно! Суши или пицца? 🍣🍕",
        "options": ["Суши! 🍣", "Пицца! 🍕", "И то и другое! 😍", "Настя не выбирает — Настя хочет всё! 💅"],
    },
    {
        "question": "Кто лучше: Настя или Алиса? 💅🤖",
        "options": ["Настя! 💅✨", "Алиса 🤖", "Обе! 😍", "Siri! 😤 (шутка)"],
    },
    {
        "question": "Какой сериал смотреть вечером? 📺",
        "options": ["Эмили в Париже 💋", "Игра престолов 🐉", "Друзья ☕", "Что Настя скажет! 💅"],
    },
    {
        "question": "Кофе или чай? ☕🍵",
        "options": ["Кофе! ☕", "Чай! 🍵", "Матча! 🍵✨", "Коктейль! 🍹"],
    },
    {
        "question": "Шопинг онлайн или в магазине? 🛍️",
        "options": ["Онлайн! 📱", "В магазине! 🏬", "И то и другое! 💅", "Настя не шопинг — Настя искусство! ✨"],
    },
    {
        "question": "Котики или собачки? 🐱🐶",
        "options": ["Котики! 🐱", "Собачки! 🐶", "Обоих! 😍", "Хомячки! 🐹"],
    },
    {
        "question": "Маникюр: гель или обычный? 💅",
        "options": ["Гель! 💅", "Обычный! 🎨", "Оба! ✨", "Настя за гель, точняк! 💅💅"],
    },
    {
        "question": "Куда поехать в отпуск? ✈️",
        "options": ["Стамбул! 🇹🇷", "Дубай! 🇦🇪", "Бали! 🌴", "Сочи! 🏖️"],
    },
    {
        "question": "Какой цвет круче? 🎨",
        "options": ["Розовый! 💖", "Красный! ❤️", "Чёрный! 🖤", "Бежевый! 🤍"],
    },
    {
        "question": "Настя сегодня в каком настроении? 🤔",
        "options": ["Капризная! 😤", "Любящая! 🥰", "Голодная! 🍽️", "Ленивая! 😴"],
    },
    {
        "question": "Лучший завтрак? 🍳",
        "options": ["Кофе и всё! ☕", "Панкейки! 🥞", "Смузи! 🥤", "Кровать — лучший завтрак! 🛏️"],
    },
    {
        "question": "Какой мессенджер лучше? 📱",
        "options": ["Telegram! 💙", "WhatsApp! 💚", "VK! 🔵", "Настя общается лично! 💅"],
    },
    {
        "question": "Смотреть фильмы: дома или в кино? 🎬",
        "options": ["Дома! 🛋️", "В кино! 🍿", "Оба! ✨", "Настя смотрит в телефоне! 📱"],
    },
    {
        "question": "Настина тема дня? 💅",
        "options": ["Шопинг! 🛍️", "Сон! 😴", "Кофе! ☕", "Всё сразу! ✨"],
    },
]
