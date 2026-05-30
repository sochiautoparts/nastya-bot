"""Nastya Chat Handler 🎀 — main conversation with context memory."""
import logging
import random
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message
from bot.config import NASTYA_SYSTEM_PROMPT, OWNER_ID
from bot.nastya import should_ask_stars, get_stars_line
from ai.router import AllProvidersExhaustedError

logger = logging.getLogger(__name__)
router = Router()

# Track stars ask timing per user
_stars_tracker: dict = {}  # user_id -> {count, last_ask}


@router.message(Command("start"))
async def cmd_start(message: Message, db=None, ai_router=None) -> None:
    """Welcome message from Nastya."""
    user = message.from_user
    name = user.first_name or "незнакомец"

    if db:
        await db.get_or_create_user(
            user_id=user.id,
            username=user.username or "",
            first_name=name,
        )

    # Get a random mood
    mood_data = {"mood": "капризная", "emoji": "😤"}
    if db:
        mood_data = await db.get_random_mood()

    mood = mood_data.get("mood", "капризная")
    emoji = mood_data.get("emoji", "🎀")

    from bot.nastya import get_mood_greeting
    greeting = get_mood_greeting(mood)

    text = (
        f"{greeting}\n\n"
        f"Я Настя {emoji} — могу поболтать, дать совет по стилю, "
        f"рассказать гороскоп или психануть вместе с тобой!\n\n"
        f"🎀 /horoscope — гороскоп на сегодня\n"
        f"🔢 /numerology — нумерология\n"
        f"🛍️ /shop — совет по шопингу\n"
        f"🧠 /psych — психоанализ\n"
        f"📊 /mood — настроение Насти\n"
        f"💝 /donate — поддержать Настю\n"
        f"🗑️ /clear — забыть всё что было\n\n"
        f"Или просто пиши мне! Я всё равно отвечу... наверное 😤"
    )
    await message.answer(text)


@router.message(Command("clear"))
async def cmd_clear(message: Message, db=None, ai_router=None) -> None:
    if db:
        await db.clear_history(message.from_user.id)
    await message.answer("Что? Ничего не помню! 🤷‍♀️ Начнём сначала!")


@router.message(F.text, ~F.text.startswith("/"))
async def handle_chat(message: Message, db=None, ai_router=None) -> None:
    """Main chat handler — Nastya talks with context memory."""
    if not db or not ai_router:
        await message.answer("Ой, у Насти что-то сломалось... Попробуй позже 😤")
        return

    user_id = message.from_user.id
    text = message.text

    # Ensure user
    await db.get_or_create_user(
        user_id=user_id,
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
    )

    # Increment message count
    msg_count = await db.increment_messages(user_id)

    # Typing
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    # Get mood
    mood_data = await db.get_random_mood()
    mood = mood_data.get("mood", "капризная")

    # Build system prompt with current mood
    system_prompt = NASTYA_SYSTEM_PROMPT + f"\n\nТВОЁ ТЕКУЩЕЕ НАСТРОЕНИЕ: {mood}. Веди себя соответственно."

    # Get chat history
    history = await db.get_history(user_id, limit=20)

    # Save user message
    await db.add_message(user_id, "user", text)

    try:
        result = await ai_router.chat(
            prompt=text,
            system_prompt=system_prompt,
            messages=history,
        )

        response_text = result.text or "Ммм... Настя задумалась. Попробуй ещё раз 🤔"

        # Save assistant message
        await db.add_message(user_id, "assistant", response_text)

        # Check if should ask for stars
        tracker = _stars_tracker.get(user_id, {"count": 0, "last_ask": 0})
        tracker["count"] = msg_count

        if should_ask_stars(msg_count, tracker["last_ask"]):
            response_text += f"\n\n{get_stars_line()}"
            tracker["last_ask"] = __import__("time").time()

        _stars_tracker[user_id] = tracker

        # Send (split if long)
        if len(response_text) > 4096:
            for i in range(0, len(response_text), 4096):
                await message.answer(response_text[i:i + 4096])
        else:
            await message.answer(response_text)

    except AllProvidersExhaustedError:
        await message.answer(
            "Блин, у Насти всё сломалось! 😭 Все AI-провайдеры недоступны. "
            "Попробуй через пару минут, ладно?"
        )
    except Exception as e:
        logger.error(f"Chat error: {e}")
        await message.answer("Ой, что-то пошло не так... Настя запуталась 🌀 Попробуй ещё!")
