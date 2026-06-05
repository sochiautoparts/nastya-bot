"""Nastya Inline Mode Handler v1.0 - Настя в любом чате!

Когда пользователь пишет @bot_username запрос в любом чате,
Настя отвечает коротким сообщением прямо в инлайн-режиме.

Особенности:
  - Короткие ответы (макс 200 символов) для инлайн-режима
  - AI-генерация через Pollinations API
  - Кэширование результатов на 5 минут
  - Fallback на шаблонные ответы если AI недоступен
  - Кнопка "Подробнее" с диплинком в приватный чат
"""
import asyncio
import hashlib
import logging
import time
from aiogram import Router, F
from aiogram.types import (
    InlineQuery, InlineQueryResultArticle,
    InputTextMessageContent, InlineKeyboardMarkup, InlineKeyboardButton,
)
from bot.config import BOT_USERNAME, INLINE_CACHE_TIME

logger = logging.getLogger(__name__)
router = Router()

# Cache for inline responses
_inline_cache: dict = {}  # query_hash -> {"text": str, "time": float}
_INLINE_CACHE_TTL = 300  # 5 minutes

# Quick template responses for when AI is unavailable
QUICK_RESPONSES = [
    "Настя тут! 💅",
    "Привет от Насти! ✨",
    "Ой, Настя занята... Но привет! 💋",
    "Настя слышит! 😏",
    "Капец, Настя тут! 🔥",
    "Ну что, спрашивай! 💅✨",
    "Настя на связи! 💋",
    "О, Настю вызывают! 💅",
    "Привет! Настя уже здесь! ✨",
    "Слушаю! 😏💅",
    "Настя в деле! 💪✨",
    "Ну наконец-то! Настя! 💅",
    "Ой, меня позвали! Бегу! 🏃‍♀️💅",
    "Настя готова! Давай! 💋✨",
    "Котятки, Настя тут! 🐱💅",
    "Ау! Настя слышит! 👂✨",
    "Ну? Настя ждёт! 😤💅",
    "Привеееет! Настя! 🌸",
    "Настя на проводе! 📞💅",
    "О! Настя пришла! 🎉✨",
]


def _get_cache_key(query: str, user_id: int) -> str:
    """Generate cache key from query and user."""
    raw = f"{query.lower().strip()}:{user_id}"
    return hashlib.md5(raw.encode()).hexdigest()


def _cleanup_cache() -> None:
    """Remove expired cache entries."""
    now = time.time()
    expired = [k for k, v in _inline_cache.items() if now - v["time"] > _INLINE_CACHE_TTL]
    for k in expired:
        del _inline_cache[k]


@router.inline_query(F.query != "")
async def handle_inline_query(inline_query: InlineQuery, db=None, ai_router=None) -> None:
    """Handle inline query with AI response or template fallback.

    When user types @bot_username query in any chat, Nastya responds
    with a short, sassy answer.
    """
    query = inline_query.query.strip()
    user_id = inline_query.from_user.id

    if not query:
        return

    # Check cache first
    _cleanup_cache()
    cache_key = _get_cache_key(query, user_id)
    if cache_key in _inline_cache:
        cached = _inline_cache[cache_key]
        result_text = cached["text"]
    else:
        result_text = None

    # Try AI generation if not cached
    if result_text is None:
        result_text = await _generate_inline_response(query, user_id, ai_router)
        # Cache the result
        _inline_cache[cache_key] = {"text": result_text, "time": time.time()}

    # Build inline result
    # Deep link to bot private chat for "Подробнее"
    detail_url = f"https://t.me/{BOT_USERNAME}?start=chat"

    results = [
        InlineQueryResultArticle(
            id=f"nastya_{hashlib.md5(query.encode()).hexdigest()[:12]}",
            title=f"Настя: {query[:50]}",
            description=result_text[:80] if len(result_text) > 80 else result_text,
            input_message_content=InputTextMessageContent(
                message_text=result_text,
            ),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="💬 Подробнее у Насти",
                    url=detail_url,
                )],
            ]),
        )
    ]

    await inline_query.answer(
        results=results,
        cache_time=INLINE_CACHE_TIME,
        is_personal=True,
    )


@router.inline_query()
async def handle_empty_inline_query(inline_query: InlineQuery, db=None, ai_router=None) -> None:
    """Handle empty inline query with suggestions.

    When user just types @bot_username without a query,
    show some helpful suggestions.
    """
    suggestions = [
        ("Настя, привет! 💅", "Поприветствуй Настю!"),
        ("Как дела, Настя? ✨", "Спроси как дела"),
        ("Настя, расскажи шутку! 😂", "Попроси шутку"),
        ("Что нового, Настя? 📰", "Узнай новости"),
    ]

    results = []
    for i, (text, desc) in enumerate(suggestions):
        results.append(
            InlineQueryResultArticle(
                id=f"nastya_suggest_{i}",
                title=text,
                description=desc,
                input_message_content=InputTextMessageContent(
                    message_text=text,
                ),
            )
        )

    await inline_query.answer(
        results=results,
        cache_time=60,
        is_personal=False,
    )


async def _generate_inline_response(query: str, user_id: int, ai_router) -> str:
    """Generate a short Nastya-style response for inline mode.

    Uses Pollinations API for AI generation.
    Falls back to template responses on failure.
    Max 200 characters for inline mode.
    """
    # Try AI generation
    if ai_router and ai_router._pollinations and ai_router._pollinations.is_available():
        try:
            from bot.config import NASTYA_SYSTEM_PROMPT

            inline_system = (
                "Ты Настя - москвичка, 23 года, блогер. Отвечай ОЧЕНЬ КОРОТКО, "
                "максимум 1-2 предложения, до 200 символов. "
                "Живо, эмоционально, как в мессенджере. "
                "Без markdown, без буллетов. "
                "Используй слова: 'прикинь', 'офигеть', 'капец', 'круто'. "
                "Если вопрос сложный - ответь коротко и предложи обсудить подробнее."
            )

            result = await asyncio.wait_for(
                ai_router._pollinations.generate(
                    prompt=query,
                    system_prompt=inline_system,
                    max_tokens=80,
                    temperature=0.9,
                ),
                timeout=8.0,
            )

            if result and result.text:
                # Clean and truncate
                from ai.router import AIRouter
                text = AIRouter.clean_ai_response(result.text)
                if text:
                    # Truncate to 200 chars at sentence boundary
                    if len(text) > 200:
                        for sep in ['. ', '! ', '? ', '\n', ', ']:
                            idx = text[:200].rfind(sep)
                            if idx > 50:
                                text = text[:idx + len(sep)].strip()
                                break
                        else:
                            text = text[:197] + "..."
                    return text

        except asyncio.TimeoutError:
            logger.warning(f"Inline AI timeout for query: {query[:50]}")
        except Exception as e:
            logger.warning(f"Inline AI error: {e}")

    # Fallback: template response
    import random
    return random.choice(QUICK_RESPONSES)
