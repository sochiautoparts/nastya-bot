"""Nastya Fun Commands 🎀 — horoscope, numerology, psychology, shopping, mood."""
import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from bot.config import NASTYA_SYSTEM_PROMPT
from bot.nastya import (
    generate_daily_horoscope, calculate_numerology, get_zodiac_info,
    get_random_fact, get_psycho_phrase, get_shopping_advice,
    ZODIAC_SIGNS,
)
from ai.router import AllProvidersExhaustedError

logger = logging.getLogger(__name__)
router = Router()


@router.message(Command("horoscope"))
async def cmd_horoscope(message: Message, db=None, ai_router=None) -> None:
    """Daily horoscope in Nastya style."""
    # Build inline keyboard with zodiac signs
    buttons = []
    row = []
    for sign, emoji in ZODIAC_SIGNS.items():
        row.append(InlineKeyboardButton(text=f"{emoji} {sign.title()}", callback_data=f"zodiac_{sign}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    # Add "random" button
    buttons.append([InlineKeyboardButton(text="🎲 Случайный знак", callback_data="zodiac_random")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    horo = generate_daily_horoscope()
    text = f"🔮 Гороскоп от Насти на сегодня:\n\n{horo}\n\nВыбери свой знак для персонального гороскопа:"

    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("zodiac_"))
async def callback_zodiac(callback, db=None, ai_router=None) -> None:
    """Handle zodiac sign selection."""
    sign = callback.data.replace("zodiac_", "")

    if sign == "random":
        import random
        sign = random.choice(list(ZODIAC_SIGNS.keys()))

    info = get_zodiac_info(sign)
    if not info:
        await callback.answer("Не знаю такой знак 🤷‍♀️")
        return

    # Generate personalized horoscope via AI
    if ai_router:
        try:
            result = await ai_router.chat(
                prompt=f"Скажи гороскоп для знака {sign} на сегодня. Коротко, 2-3 предложения, в стиле Насти — капризно, с астрологией и советом.",
                system_prompt=NASTYA_SYSTEM_PROMPT + "\nТы составляешь гороскоп. Отвечай коротко, 2-3 предложения.",
            )
            horo_text = result.text
        except Exception:
            horo_text = generate_daily_horoscope()
    else:
        horo_text = generate_daily_horoscope()

    text = f"{info['emoji']} {sign.title()}\n\n{horo_text}"
    await callback.message.edit_text(text)
    await callback.answer()


@router.message(Command("numerology"))
async def cmd_numerology(message: Message, db=None, ai_router=None) -> None:
    """Calculate numerology."""
    text = message.text or ""
    parts = text.split()

    if len(parts) > 1:
        # User provided a number
        number_str = parts[1]
        result = calculate_numerology(number_str)
        text = (
            f"🔢 Нумерология от Насти:\n\n"
            f"Твоё число: {result['number']}\n"
            f"{result['meaning']}"
        )
    else:
        text = (
            "🔢 Нумерология от Насти!\n\n"
            "Пришли мне число или дату рождения, и я рассчитаю твою судьбу!\n"
            "Например: /numerology 15081999\n\n"
            "Настя верит в числа... иногда 🧮"
        )

    await message.answer(text)


@router.message(Command("shop"))
async def cmd_shop(message: Message, db=None, ai_router=None) -> None:
    """Shopping advice from Nastya."""
    advice = get_shopping_advice()

    if ai_router:
        try:
            result = await ai_router.chat(
                prompt="Дай один короткий совет по шопингу или стилю. Капризно, в стиле Насти. 1-2 предложения.",
                system_prompt=NASTYA_SYSTEM_PROMPT + "\nДай один конкретный совет по шопингу или стилю. Коротко!",
            )
            advice = result.text
        except Exception:
            pass

    await message.answer(f"🛍️ Совет от Насти:\n\n{advice}")


@router.message(Command("psych"))
async def cmd_psych(message: Message, db=None, ai_router=None) -> None:
    """Psychology 'analysis' from Nastya."""
    text = message.text or ""
    parts = text.split(maxsplit=1)

    if len(parts) > 1 and ai_router:
        # User asked about something specific
        try:
            result = await ai_router.chat(
                prompt=f"Сделай шуточный психоанализ: {parts[1]}. Как Настя — капризная психологиня. 2-3 предложения, смешно и в стиле стереотипов о Настях.",
                system_prompt=NASTYA_SYSTEM_PROMPT + "\nТы делаешь шуточный психоанализ. Отвечай коротко, 2-3 предложения, как капризная психологиня Настя.",
            )
            await message.answer(f"🧠 Психоанализ от Насти:\n\n{result.text}")
        except Exception:
            phrase = get_psycho_phrase()
            await message.answer(f"🧠 Настя-психолог говорит: {phrase}")
    else:
        phrase = get_psycho_phrase()
        fact = get_random_fact()
        await message.answer(
            f"🧠 Настя-психолог:\n\n{phrase}\n\n💡 Факт о Настях: {fact}\n\n"
            "Напиши /psych и тему, и Настя разберётся! Например:\n"
            "/psych почему я всё откладываю"
        )


@router.message(Command("mood"))
async def cmd_mood(message: Message, db=None, ai_router=None) -> None:
    """Check Nastya's current mood."""
    if db:
        mood_data = await db.get_random_mood()
    else:
        mood_data = {"mood": "капризная", "emoji": "😤", "description": "Настя в капризном настроении"}

    mood = mood_data.get("mood", "капризная")
    emoji = mood_data.get("emoji", "🎀")
    desc = mood_data.get("description", "")

    # Count user messages
    msg_count = 0
    if db:
        user = await db.get_or_create_user(
            user_id=message.from_user.id,
            username=message.from_user.username or "",
            first_name=message.from_user.first_name or "",
        )
        msg_count = user.get("total_messages", 0)

    text = (
        f"{emoji} Настроение Насти: {mood}\n\n"
        f"{desc}\n\n"
        f"💬 Сообщений от тебя: {msg_count}\n"
    )

    # Stars donated?
    if db:
        total_stars = await db.get_total_donated(message.from_user.id)
        if total_stars > 0:
            text += f"💝 Звёзд Насте: {total_stars}\n"

    await message.answer(text)


@router.message(Command("fact"))
async def cmd_fact(message: Message, db=None, ai_router=None) -> None:
    """Random fun fact about Nastyas."""
    fact = get_random_fact()
    await message.answer(f"🎀 Факт о Настях:\n\n{fact}")
