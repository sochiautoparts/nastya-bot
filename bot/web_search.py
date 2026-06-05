"""Nastya Web Search v2.0 - MULTI-ENGINE SEARCH with fallbacks!

v2.0: ROBUST search with multiple engines:
  1. DuckDuckGo HTML (primary) - works most of the time
  2. Yandex HTML search (fallback #1) - Russian-focused, better for RU queries
  3. SearXNG public instances (fallback #2) - meta search engine
  4. DuckDuckGo API (fallback #3) - instant answers only

This ensures /find ALWAYS returns results even if one engine is blocked.

Enables Nastya to:
  - Search for real-time information when discussing events/news
  - Verify facts and find links to share with users
  - Find REAL product links instead of hallucinating URLs
  - Make conversations more lively with up-to-date knowledge
  - Always include source links when sharing information
"""
import logging
import re
import time
import random
from typing import Dict, List, Optional
from urllib.parse import unquote, quote_plus

import httpx

logger = logging.getLogger(__name__)

# Cache search results to avoid repeated queries
_search_cache: Dict[str, Dict] = {}
_CACHE_TTL = 1800  # 30 minutes
_MAX_CACHE = 100

# Common headers for web scraping
_SEARCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


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
    """Search the web using multiple search engines with fallbacks.

    Engine order:
      1. DuckDuckGo HTML - primary, fast
      2. Yandex HTML - Russian-focused, excellent for RU queries
      3. SearXNG public instance - meta search
      4. DuckDuckGo API - instant answers only (weak)

    Returns list of dicts with: title, snippet, url
    """
    _clean_cache()

    # Check cache
    cache_key = query.lower().strip()
    cached = _search_cache.get(cache_key)
    if cached and time.time() - cached.get("ts", 0) < _CACHE_TTL:
        return cached.get("results", [])[:num_results]

    results = []

    # ── Engine 1: DuckDuckGo HTML (primary) ──
    try:
        results = await _search_ddg_html(query, num_results)
        if results:
            logger.info(f"DDG HTML: found {len(results)} results for '{query[:50]}'")
    except Exception as e:
        logger.warning(f"DDG HTML search error: {e}")

    # ── Engine 2: Yandex HTML (fallback #1) ──
    if not results:
        try:
            results = await _search_yandex_html(query, num_results)
            if results:
                logger.info(f"Yandex HTML: found {len(results)} results for '{query[:50]}'")
        except Exception as e:
            logger.warning(f"Yandex HTML search error: {e}")

    # ── Engine 3: SearXNG (fallback #2) ──
    if not results:
        try:
            results = await _search_searxng(query, num_results)
            if results:
                logger.info(f"SearXNG: found {len(results)} results for '{query[:50]}'")
        except Exception as e:
            logger.warning(f"SearXNG search error: {e}")

    # ── Engine 4: DuckDuckGo instant answer API (fallback #3) ──
    if not results:
        try:
            results = await _search_ddg_api(query, num_results)
            if results:
                logger.info(f"DDG API: found {len(results)} results for '{query[:50]}'")
        except Exception as e:
            logger.warning(f"DDG API fallback error: {e}")

    if not results:
        logger.warning(f"ALL search engines failed for query: '{query[:50]}'")

    # Cache results
    if results:
        _search_cache[cache_key] = {"results": results, "ts": time.time()}

    return results[:num_results]


# ══════════════════════════════════════════════════════════════
#  ENGINE 1: DuckDuckGo HTML
# ══════════════════════════════════════════════════════════════

async def _search_ddg_html(query: str, num_results: int) -> List[Dict]:
    """Search using DuckDuckGo HTML endpoint."""
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(12.0, connect=5.0),
        follow_redirects=True,
        headers=_SEARCH_HEADERS,
    ) as client:
        response = await client.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query, "kl": "ru-ru", "b": ""},
        )

        if response.status_code == 200:
            return _parse_ddg_html(response.text, num_results)

    return []


def _parse_ddg_html(html: str, num_results: int) -> List[Dict]:
    """Parse DuckDuckGo HTML search results."""
    results = []

    # Primary pattern: result__a link + result__snippet
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
            url = _clean_ddg_url(url)
            if url:
                results.append({
                    "title": title[:200],
                    "snippet": snippet[:300],
                    "url": url,
                })

    # Fallback: simpler pattern (just links, no snippets)
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
                url = _clean_ddg_url(url)
                if url:
                    results.append({
                        "title": title[:200],
                        "snippet": "",
                        "url": url,
                    })

    return results


def _clean_ddg_url(url: str) -> str:
    """Clean a DuckDuckGo redirect URL to get the actual URL."""
    if url.startswith("//duckduckgo.com/l/"):
        actual_url_match = re.search(r'uddg=([^&]+)', url)
        if actual_url_match:
            return unquote(actual_url_match.group(1))
        return ""  # Can't extract real URL
    if url.startswith("//"):
        url = "https:" + url
    return url


# ══════════════════════════════════════════════════════════════
#  ENGINE 2: Yandex HTML search
# ══════════════════════════════════════════════════════════════

async def _search_yandex_html(query: str, num_results: int) -> List[Dict]:
    """Search using Yandex HTML. Excellent for Russian-language queries."""
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(12.0, connect=5.0),
        follow_redirects=True,
        headers={
            **_SEARCH_HEADERS,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    ) as client:
        response = await client.get(
            "https://yandex.ru/search/",
            params={
                "text": query,
                "lr": 213,  # Moscow region
                "numdoc": num_results,
            },
        )

        if response.status_code == 200:
            return _parse_yandex_html(response.text, num_results)

    return []


def _parse_yandex_html(html: str, num_results: int) -> List[Dict]:
    """Parse Yandex search results HTML."""
    results = []

    # Yandex uses various class names, try multiple patterns
    # Pattern 1: Organic results with data attributes
    organic_pattern = re.compile(
        r'<a[^>]+class="[^"]*Link[^"]*"[^>]+href="((?:https?://)[^"]+)"[^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )

    seen_urls = set()
    for match in organic_pattern.finditer(html):
        if len(results) >= num_results:
            break
        url = match.group(1)
        title = _strip_html(match.group(2)).strip()

        # Skip Yandex internal URLs and duplicates
        if not title or not url.startswith("http"):
            continue
        if any(skip in url for skip in ["yandex.ru", "yandex.com", "ya.ru", "/search/"]):
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)

        results.append({
            "title": title[:200],
            "snippet": "",  # Yandex snippets are harder to extract reliably
            "url": url,
        })

    # Pattern 2: Try simpler href extraction if pattern 1 failed
    if not results:
        href_pattern = re.compile(
            r'href="(https?://(?!yandex\.(?:ru|com)|ya\.ru)[^"]+)"[^>]*>([^<]{10,}?)</a>',
            re.IGNORECASE,
        )
        for match in href_pattern.finditer(html):
            if len(results) >= num_results:
                break
            url = match.group(1)
            title = _strip_html(match.group(2)).strip()
            if title and url and url not in seen_urls:
                seen_urls.add(url)
                results.append({
                    "title": title[:200],
                    "snippet": "",
                    "url": url,
                })

    return results


# ══════════════════════════════════════════════════════════════
#  ENGINE 3: SearXNG public instance
# ══════════════════════════════════════════════════════════════

# List of public SearXNG instances to try
_SEARXNG_INSTANCES = [
    "https://search.sapti.me",
    "https://searx.be",
    "https://search.bus-hit.me",
    "https://searx.fmac.xyz",
]


async def _search_searxng(query: str, num_results: int) -> List[Dict]:
    """Search using SearXNG public instances. Meta search engine."""
    results = []

    for instance in _SEARXNG_INSTANCES:
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=5.0),
                follow_redirects=True,
                headers=_SEARCH_HEADERS,
            ) as client:
                response = await client.get(
                    f"{instance}/search",
                    params={
                        "q": query,
                        "format": "json",
                        "language": "ru",
                        "categories": "general",
                    },
                )

                if response.status_code == 200:
                    data = response.json()
                    for item in data.get("results", [])[:num_results]:
                        title = item.get("title", "").strip()
                        url = item.get("url", "").strip()
                        snippet = item.get("content", "").strip()

                        if title and url and url.startswith("http"):
                            results.append({
                                "title": title[:200],
                                "snippet": snippet[:300],
                                "url": url,
                            })

                    if results:
                        return results
        except Exception:
            continue

    return results


# ══════════════════════════════════════════════════════════════
#  ENGINE 4: DuckDuckGo instant answer API
# ══════════════════════════════════════════════════════════════

async def _search_ddg_api(query: str, num_results: int) -> List[Dict]:
    """Search using DuckDuckGo instant answer API (weakest - only for definitions)."""
    results = []

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

    return results


# ══════════════════════════════════════════════════════════════
#  UTILITY FUNCTIONS
# ══════════════════════════════════════════════════════════════

def _strip_html(text: str) -> str:
    """Remove HTML tags from text."""
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def should_search(text: str) -> Optional[str]:
    """Determine if the user message warrants a web search.

    Returns the search query if search is needed, None otherwise.
    Only searches when the topic is clearly about factual/news content.

    v33: Убраны триггеры "что за" и "расскажи про" - это эмоциональные
    выражения, не поисковые запросы. "Что за бред" не должно искать "бред"!
    """
    text_lower = text.lower()

    # v33: ИСКЛЮЧЕНИЯ - эмоциональные выражения которые НЕ должны вызывать поиск
    emotional_expressions = [
        "что за бред", "что за фигня", "что за хрень", "что за хуйня",
        "че за бред", "че за фигня", "какой бред", "полный бред",
        "что это за", "какого хера", "на хуя",
    ]
    for expr in emotional_expressions:
        if expr in text_lower:
            return None

    # Direct search triggers
    search_triggers = [
        "поищи", "найди", "search", "find", "узнай", "проверь",
        "что такое", "кто такой", "кто такая", "что значит", "что означает",
        "правда ли", "действительно ли", "это правда", "подтверд",
        "сколько стоит", "какой курс", "какая погода", "какая температура",
        "когда будет", "где находится", "где купить", "как доехать",
        "последние новости", "свежие новости", "что нового в",
        "что случилось", "что произошло", "какие события",
        # v46: Product/service/price search triggers
        "где найти", "где заказать", "где продается", "лучшая цена",
        "подешевле", "поиск товара", "ищу товар", "купить", "заказать",
        "цена на", "стоимость", "сколько стоит", "рейтинг", "топ лучших",
        "отзывы о", "сравнение", "какой лучше", "что выбрать",
        "рекомендуй", "посоветуй", "что купить", "какой выбрать",
        "аналог", "замена", "альтернатива",
        "кто победил", "кто выиграл", "кто стал",
        "какой результат", "какой счёт", "сколько",
    ]

    for trigger in search_triggers:
        if trigger in text_lower:
            idx = text_lower.find(trigger)
            query = text[idx + len(trigger):].strip().rstrip("?!.،")
            if len(query) > 2:
                return query[:100]
            return text[:100]

    # Question detection - search for factual questions
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

    # Event/news discussion triggers - lower threshold
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
            entry += f" - {snippet[:200]}"
        if url:
            entry += f" [Ссылка: {url}]"
        lines.append(entry)

    lines.append(
        "⛔ Когда обсуждаешь эту информацию - ОБЯЗАТЕЛЬНО добавь ссылку на источник! "
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
        "options": ["Суши! 🍣", "Пицца! 🍕", "И то и другое! 😍", "Настя не выбирает - Настя хочет всё! 💅"],
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
        "options": ["Онлайн! 📱", "В магазине! 🏬", "И то и другое! 💅", "Настя не шопинг - Настя искусство! ✨"],
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
        "options": ["Кофе и всё! ☕", "Панкейки! 🥞", "Смузи! 🥤", "Кровать - лучший завтрак! 🛏️"],
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
    {
        "question": "Какой формат контента хотите в канале? 📺",
        "options": ["Новости! 📰", "Факты! 🤓", "Опросы! 📊", "Личное! 💅"],
    },
    {
        "question": "Настя хочет купить... Что посоветуете? 🛒",
        "options": ["Айфон! 📱", "Наушники! 🎧", "Сумочку! 👜", "Котика! 🐱"],
    },
    {
        "question": "Какой день недели самый тяжёлый? 😤",
        "options": ["Понедельник! 😫", "Среда! 😐", "Пятница (ожидание)! 🥱", "Воскресенье (завтра работать)! 😱"],
    },
    {
        "question": "Настя учится готовить! Что приготовить? 🍳",
        "options": ["Пасту! 🍝", "Суши дома! 🍣", "Блины! 🥞", "Лучше заказать! 📱"],
    },
    {
        "question": "Какой язык программирования самый крутой? 💻",
        "options": ["Python! 🐍", "JavaScript! 🌐", "C++! ⚡", "Настя не программист! 💅"],
    },
    {
        "question": "Лучшая социальная сеть? 📱",
        "options": ["Telegram! 💙", "TikTok! 🎵", "Instagram! 📸", "YouTube! ▶️"],
    },
    {
        "question": "Что делать в выходные? 🎉",
        "options": ["Шопинг! 🛍️", "Сериал! 📺", "Гулять! 🚶‍♀️", "Спать! 😴"],
    },
    {
        "question": "Какое время года лучшее? 🌈",
        "options": ["Лето! ☀️", "Осень! 🍂", "Зима! ❄️", "Весна! 🌸"],
    },
    {
        "question": "Настя идёт в спортзал... Шутка! Но если бы? 💪",
        "options": ["Йога! 🧘‍♀️", "Бег! 🏃‍♀️", "Плавание! 🏊‍♀️", "Лежать - тоже спорт! 😴"],
    },
]
