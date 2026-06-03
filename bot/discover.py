"""Nastya Discovery Engine — auto-discovers interesting content for channel & chat.

Periodically searches the web for:
  - Interesting facts & news
  - Recipes (кулинария)
  - Numerology & Astrology (гороскопы, числа)
  - Event announcements (афиша, мероприятия)
  - Cool products & deals
  - Lifestyle & beauty tips

Then:
  - Posts to Telegram channel with source links
  - Shares with chat users proactively
"""
import asyncio
import logging
import random
import re
import time
from typing import Dict, List, Optional

import httpx

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.config import CHANNEL_ID, BOT_USERNAME
from bot.web_search import search_web

logger = logging.getLogger(__name__)

# ── Discovery Topics — diverse content categories ──

DISCOVERY_TOPICS = [
    # Recipes
    {"query": "простой вкусный рецепт на ужин 2025", "category": "recipe",
     "post_type": "recipe", "weight": 3},
    {"query": "быстрый завтрак рецепт за 10 минут", "category": "recipe",
     "post_type": "recipe", "weight": 2},
    {"query": "оригинальный десерт рецепт домашний", "category": "recipe",
     "post_type": "recipe", "weight": 2},
    {"query": "салат рецепт праздничный простой", "category": "recipe",
     "post_type": "recipe", "weight": 2},
    {"query": "выпечка рецепт к чаю быстро", "category": "recipe",
     "post_type": "recipe", "weight": 2},
    {"query": "суп рецепт домашний вкусный", "category": "recipe",
     "post_type": "recipe", "weight": 1},
    # Numerology
    {"query": "нумерология число судьбы значение 2025", "category": "numerology",
     "post_type": "numerology", "weight": 2},
    {"query": "значение чисел в нумерологии характер", "category": "numerology",
     "post_type": "numerology", "weight": 1},
    {"query": "нумерология совместимость по дате рождения", "category": "numerology",
     "post_type": "numerology", "weight": 1},
    {"query": "ангельская нумерология повторяющиеся числа", "category": "numerology",
     "post_type": "numerology", "weight": 1},
    # Astrology
    {"query": "гороскоп на сегодня все знаки зодиака", "category": "astrology",
     "post_type": "astrology", "weight": 3},
    {"query": "гороскоп на неделю знаки зодиака", "category": "astrology",
     "post_type": "astrology", "weight": 2},
    {"query": "лунный календарь сегодня рекомендации", "category": "astrology",
     "post_type": "astrology", "weight": 2},
    {"query": "ретроградный меркурий что нельзя делать", "category": "astrology",
     "post_type": "astrology", "weight": 1},
    {"query": "совместимость знаков зодиака в любви", "category": "astrology",
     "post_type": "astrology", "weight": 2},
    # Events
    {"query": "афиша мероприятий мероприятия сегодня Россия", "category": "events",
     "post_type": "events", "weight": 2},
    {"query": "концерты фестивали афиша 2025 Россия", "category": "events",
     "post_type": "events", "weight": 2},
    {"query": "выставки Москва Санкт-Петербург афиша", "category": "events",
     "post_type": "events", "weight": 1},
    {"query": "бесплатные мероприятия выходные Россия", "category": "events",
     "post_type": "events", "weight": 1},
    # Lifestyle & Beauty
    {"query": "тренды моды 2025 одежда аксессуары", "category": "lifestyle",
     "post_type": "lifestyle", "weight": 2},
    {"query": "уход за кожей лица советы косметолога", "category": "lifestyle",
     "post_type": "lifestyle", "weight": 2},
    {"query": "маникюр тренды дизайн ногтей 2025", "category": "lifestyle",
     "post_type": "lifestyle", "weight": 1},
    {"query": "фитнес домашние упражнения для начинающих", "category": "lifestyle",
     "post_type": "lifestyle", "weight": 1},
    # Interesting facts
    {"query": "интересные факты о которых вы не знали", "category": "facts",
     "post_type": "facts", "weight": 2},
    {"query": "научные открытия 2025 последние новости", "category": "facts",
     "post_type": "facts", "weight": 2},
    {"query": "необычные места мира которые стоит посетить", "category": "facts",
     "post_type": "facts", "weight": 1},
    # Products & Deals
    {"query": "лучшие скидки и акции распродажа сегодня", "category": "deals",
     "post_type": "deals", "weight": 1},
    {"query": "топ лучших покупок на маркетплейсе отзывы", "category": "deals",
     "post_type": "deals", "weight": 1},
]

# Track recently used topics to avoid repetition
_recent_discoveries: List[str] = []
_MAX_RECENT = 30


def _is_recent_discovery(query: str) -> bool:
    """Check if we recently searched for this topic."""
    return query.lower() in _recent_discoveries


def _track_discovery(query: str) -> None:
    """Track a discovery to avoid repetition."""
    _recent_discoveries.append(query.lower())
    while len(_recent_discoveries) > _MAX_RECENT:
        _recent_discoveries.pop(0)


def _pick_topic() -> Dict:
    """Pick a weighted random discovery topic."""
    candidates = [t for t in DISCOVERY_TOPICS if not _is_recent_discovery(t["query"])]
    if not candidates:
        # Reset if all used
        _recent_discoveries.clear()
        candidates = DISCOVERY_TOPICS

    weights = [t.get("weight", 1) for t in candidates]
    return random.choices(candidates, weights=weights, k=1)[0]


# ── Post templates by category ──

CATEGORY_TEMPLATES = {
    "recipe": [
        "🍳 Настя нашла классный рецепт!\n\n{content}\n\nПриятного аппетита! 💅✨",
        "Вау, рецепт! Настя обязательно попробует!\n\n{content}\n\nГотовьте с Настей! 🍳💅",
        "Кулинарная находка Насти!\n\n{content}\n\nНастя уже пускает слюнки! 😋✨",
    ],
    "numerology": [
        "🔢 Нумерология от Насти!\n\n{content}\n\nА какое твоё число? Спроси Настю! 💅✨",
        "Магия чисел! Настя в восторге!\n\n{content}\n\nХочешь узнать своё число? Пиши Насте! 🔮💅",
    ],
    "astrology": [
        "♈ Гороскоп от Насти!\n\n{content}\n\nТвой знак сходится? Пиши Насте! 💅✨",
        "🔮 Астрологический прогноз!\n\n{content}\n\nНастя верит в звёзды! А ты? ♊✨",
        "Звёзды говорят! Настя слушает!\n\n{content}\n\nСовпало? Напиши Насте! 🔮💅",
    ],
    "events": [
        "🎉 Анонс мероприятий!\n\n{content}\n\nКуда пойдём? Пишите в комменты! 💅✨",
        "Афиша от Насти! Не пропусти!\n\n{content}\n\nНастя хочет на всё! 🎫✨",
    ],
    "lifestyle": [
        "💅 Лайфстак от Насти!\n\n{content}\n\nНастя одобряет! А ты? ✨",
        "Советы от Насти! Запоминай!\n\n{content}\n\nБудь стильной как Настя! 💅✨",
    ],
    "facts": [
        "🤯 Настя только что узнала!\n\n{content}\n\nПрикинь?! 💅✨",
        "Факт дня от Насти!\n\n{content}\n\nОфигеть, правда?! 🤓💅",
    ],
    "deals": [
        "🛍️ Настя нашла скидки!\n\n{content}\n\nБеги пока есть! 💅✨",
        "Распродажа! Настя уже смотрит!\n\n{content}\n\nНадо брать! 🛒💅",
    ],
}


async def discover_content(ai_router) -> Optional[Dict]:
    """Search the web for interesting content on a random topic.

    Returns a dict with: category, post_type, content, source_url, source_title
    or None if nothing found.
    """
    topic = _pick_topic()
    query = topic["query"]
    category = topic["category"]
    post_type = topic["post_type"]

    _track_discovery(query)

    logger.info(f"Discovery: searching for '{query}' (category={category})")

    # Search the web
    results = await search_web(query, num_results=5)
    if not results:
        logger.info(f"Discovery: no results for '{query}'")
        return None

    # Pick a result and generate content with AI
    best = results[0]
    title = best.get("title", "")
    snippet = best.get("snippet", "")
    url = best.get("url", "")

    # Also grab extra results for richer content
    extra_info = ""
    for r in results[1:3]:
        s = r.get("snippet", "")
        if s:
            extra_info += f"\n- {s[:200]}"

    # Use AI to generate a detailed, engaging post
    ai_content = ""
    if ai_router:
        try:
            category_prompts = {
                "recipe": "Напиши подробный рецепт в живом стиле. Включи ингредиенты и пошаговое приготовление. 6-10 предложений. Без markdown, без буллетов — сплошной текст.",
                "numerology": "Расскажи про значение чисел в нумерологии интересно и доступно. Дай конкретные примеры. 5-8 предложений. Без markdown.",
                "astrology": "Напиши гороскоп или астрологический прогноз на сегодня/неделю. Живо, с эмоциями, для всех знаков кратко. 6-10 предложений. Без markdown.",
                "events": "Расскажи про интересные мероприятия и события. Дай конкретные даты и места если есть. 5-8 предложений. Без markdown.",
                "lifestyle": "Поделись советами по стилю, красоте или здоровью. Конкретные рекомендации. 5-8 предложений. Без markdown.",
                "facts": "Расскажи интересный факт подробно и увлекательно. Добавь своё мнение. 5-8 предложений. Без markdown.",
                "deals": "Расскажи про выгодные покупки и скидки. Где купить, сколько стоит. 5-8 предложений. Без markdown.",
            }

            category_prompt = category_prompts.get(category, "Напиши интересный развёрнутый пост. 5-8 предложений. Без markdown.")

            result = await ai_router.chat(
                prompt=f"Найдена информация: {title}. {snippet}.{extra_info}\n\n{category_prompt}",
                system_prompt=(
                    "Ты Настя — девушка из Сочи, 23 года. Пишешь пост для своего Telegram канала ОТ СЕБЯ. "
                    "Говоришь от первого лица — 'я думаю', 'мне кажется', 'я нашла'. "
                    "Живо, эмоционально, как живой человек. "
                    "Используй слова: 'прикинь', 'офигеть', 'капец', 'круто', 'точняк'. "
                    "Пиши развёрнуто и познавательно — это важно! "
                    "Без markdown, без буллетов, без заголовков. "
                    "Не пиши 'Настя' в начале — говори от первого лица. "
                    "НЕ добавляй ссылки на источник — ты пишешь от себя. "
                    "Ссылки допускаются ТОЛЬКО на конкретные товары, услуги, мероприятия, рецепты. "
                    "НЕ вставляй ссылку на свой канал — она добавится автоматически."
                ),
                max_tokens=500,
                priority="low",
            )

            if result and result.text:
                from ai.router import AIRouter
                ai_content = AIRouter.clean_ai_response(result.text)
                if ai_content:
                    # Remove prefixes
                    for prefix in ["Настя:", "НАСТЯ:", "Nastya:"]:
                        if ai_content.startswith(prefix):
                            ai_content = ai_content[len(prefix):].strip()
        except Exception as e:
            logger.error(f"Discovery AI error: {e}")

    # Fallback if AI failed
    if not ai_content:
        ai_content = f"{snippet}"
        if extra_info:
            ai_content += f"\n{extra_info}"

    return {
        "category": category,
        "post_type": post_type,
        "content": ai_content,
        "source_url": url,
        "source_title": title,
    }


async def post_discovery_to_channel(bot: Bot, db, ai_router, discovery: Dict) -> bool:
    """Post a discovery to the channel with source link."""
    if not CHANNEL_ID:
        return False

    category = discovery.get("category", "facts")
    content = discovery.get("content", "")
    source_url = discovery.get("source_url", "")
    source_title = discovery.get("source_title", "")

    if not content:
        return False

    # Pick template
    templates = CATEGORY_TEMPLATES.get(category, CATEGORY_TEMPLATES["facts"])
    template = random.choice(templates)

    post_text = template.format(content=content)

    # Channel link in EVERY post (instead of source link — Nastya writes from herself)
    post_text += f"\n\n👉 t.me/chasnastya"

    # Add category hashtag
    category_hashtags = {
        "recipe": "#Рецепт #Кулинария",
        "numerology": "#Нумерология #МагияЧисел",
        "astrology": "#Гороскоп #Астрология",
        "events": "#Афиша #Мероприятия",
        "lifestyle": "#Лайфстайл #Красота",
        "facts": "#ФактДня #Интересно",
        "deals": "#Скидки #Покупки",
    }
    hashtags = category_hashtags.get(category, "#Интересно")
    post_text += f"\n{hashtags}"

    # Validate
    if not _validate_discovery_post(post_text):
        return False

    try:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="💬 Спросить Настю",
                url=f"https://t.me/{BOT_USERNAME}?start=chat",
            )],
        ])

        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=post_text,
            reply_markup=keyboard,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

        await db.add_channel_post(
            news_id=0,
            post_text=post_text,
            post_type=f"discovery_{category}",
        )

        logger.info(f"Discovery post: category={category}, content={content[:50]}...")
        return True

    except Exception as e:
        logger.error(f"Discovery channel post error: {e}")
        return False


def _validate_discovery_post(text: str) -> bool:
    """Validate discovery post text."""
    if not text or not text.strip():
        return False

    # Block SSE/API artifacts
    text_lower = text.lower()
    garbage_patterns = [
        "data:", "[done]", "authentication error", "rate limit",
        "pollinations.ai", "powered by", "model not found",
    ]
    for pattern in garbage_patterns:
        if pattern in text_lower:
            return False

    if text.strip().startswith(('{', '[', '```')):
        return False

    return True


async def get_discovery_for_chat(ai_router) -> Optional[str]:
    """Get a short discovery snippet to share with a chat user proactively.

    Returns a short text snippet or None.
    """
    topic = _pick_topic()
    query = topic["query"]
    category = topic["category"]

    _track_discovery(query)

    results = await search_web(query, num_results=3)
    if not results:
        return None

    best = results[0]
    title = best.get("title", "")
    snippet = best.get("snippet", "")
    url = best.get("url", "")

    # Generate a short chat-friendly version with AI
    if ai_router:
        try:
            result = await ai_router.chat(
                prompt=f"Настя нашла интересное: {title}. {snippet}. Расскажи коротко и живо, 2-3 предложения. Источник: {url}",
                system_prompt=(
                    "Ты Настя — девушка из Сочи, 23 года. Делишься находкой с собеседником. "
                    "Коротко, живо, эмоционально. Обязательно добавь ссылку. "
                    "Без markdown, без буллетов."
                ),
                max_tokens=200,
                priority="low",
            )
            if result and result.text:
                from ai.router import AIRouter
                cleaned = AIRouter.clean_ai_response(result.text)
                if cleaned and len(cleaned) > 20:
                    return cleaned
        except Exception:
            pass

    # Fallback: simple template
    if snippet and url:
        return f"Прикинь, я тут нашла! {snippet[:150]}\n🔗 {url}"
    return None


async def run_discovery_cycle(bot: Bot, db, ai_router) -> int:
    """Run a full discovery cycle: search → AI post → channel.

    Returns number of posts made.
    """
    posted = 0

    try:
        # Discover 1-2 items per cycle
        for _ in range(random.randint(1, 2)):
            discovery = await discover_content(ai_router)
            if discovery:
                if await post_discovery_to_channel(bot, db, ai_router, discovery):
                    posted += 1
                # Small delay between posts
                await asyncio.sleep(2)
    except Exception as e:
        logger.error(f"Discovery cycle error: {e}")

    return posted


# ── Product Search — find products, services, best prices ──

async def search_products(query: str, num_results: int = 5) -> List[Dict]:
    """Search for products, services, and prices.

    Returns list of results with: title, snippet, url, price (if detected)
    """
    # Enhance query with price/shopping intent
    shopping_query = f"{query} купить цена отзывы"
    results = await search_web(shopping_query, num_results=num_results)

    # Also try a regular search for info
    info_results = await search_web(query, num_results=2)

    # Merge results
    all_results = results + info_results

    # Try to detect prices in snippets
    price_pattern = re.compile(r'(\d[\d\s]*[.,]?\d*)\s*(?:руб|₽|р\.|рублей|тыс)', re.IGNORECASE)
    for result in all_results:
        snippet = result.get("snippet", "")
        price_match = price_pattern.search(snippet)
        if price_match:
            result["price"] = price_match.group(0).strip()

    return all_results[:num_results]


def format_product_results(results: List[Dict], query: str) -> str:
    """Format product search results for chat message."""
    if not results:
        return f"Ой, Настя не нашла '{query}'... Попробуй другой запрос! 😔"

    lines = [f"🔍 Настя нашла про '{query}':\n"]
    for i, result in enumerate(results[:5], 1):
        title = result.get("title", "")
        snippet = result.get("snippet", "")
        url = result.get("url", "")
        price = result.get("price", "")

        lines.append(f"{i}. {title}")
        if price:
            lines.append(f"   💰 {price}")
        if snippet:
            lines.append(f"   {snippet[:150]}")
        if url:
            lines.append(f"   🔗 {url}")
        lines.append("")

    return "\n".join(lines)
