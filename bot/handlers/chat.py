"""Nastya Chat Handler — main conversation with context memory, image & voice support."""
import logging
import random
import base64
import asyncio
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile
from bot.config import NASTYA_SYSTEM_PROMPT, OWNER_ID, PROACTIVE_MIN_MESSAGES, PROACTIVE_CHANCE, PROACTIVE_COOLDOWN
from bot.nastya import should_ask_stars, get_stars_line, should_send_proactive, get_proactive_message, get_random_want
from ai.router import AllProvidersExhaustedError

logger = logging.getLogger(__name__)
router = Router()

# Track per-user state
_stars_tracker: dict = {}     # user_id -> {count, last_ask}
_proactive_tracker: dict = {}  # user_id -> {count, last_proactive, chat_id}
_user_moods: dict = {}        # user_id -> mood string (changes periodically)


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

    mood_data = {"mood": "капризная", "emoji": "😤"}
    if db:
        mood_data = await db.get_random_mood()

    mood = mood_data.get("mood", "капризная")
    emoji = mood_data.get("emoji", "🎀")
    _user_moods[user.id] = mood

    from bot.nastya import get_mood_greeting
    greeting = get_mood_greeting(mood)

    text = (
        f"{greeting}\n\n"
        f"Я Настя {emoji} — могу поболтать, дать совет по стилю, "
        f"рассказать гороскоп, обсудить ремонт или психануть вместе с тобой!\n\n"
        f"Присылай фото — я всё увижу и раскритикую!\n"
        f"Присылай голосовые — я послушаю!\n\n"
        f"/horoscope — гороскоп на сегодня\n"
        f"/numerology — нумерология\n"
        f"/shop — совет по шопингу\n"
        f"/psych — психоанализ\n"
        f"/repair — совет по ремонту\n"
        f"/mood — настроение Насти\n"
        f"/want — чего Настя хочет\n"
        f"/donate — поддержать Настю\n"
        f"/clear — забыть всё что было\n\n"
        f"Или просто пиши мне! Я всё равно отвечу... наверное"
    )
    await message.answer(text)


@router.message(Command("clear"))
async def cmd_clear(message: Message, db=None, ai_router=None) -> None:
    if db:
        await db.clear_history(message.from_user.id)
    await message.answer("Что? Ничего не помню! Начнём сначала!")


@router.message(Command("want"))
async def cmd_want(message: Message, db=None, ai_router=None) -> None:
    """What does Nastya want right now?"""
    want = get_random_want()
    await message.answer(want)


# ── Voice message handler ────────────────────────────────────
@router.message(F.voice)
async def handle_voice(message: Message, db=None, ai_router=None) -> None:
    """Handle voice messages — transcribe and respond."""
    if not db or not ai_router:
        await message.answer("Ой, у Насти что-то сломалось... Попробуй позже")
        return

    user_id = message.from_user.id
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    try:
        # Download voice file
        voice = message.voice
        file = await message.bot.get_file(voice.file_id)

        # Download to bytes
        import io
        buf = io.BytesIO()
        await message.bot.download_file(file.file_path, buf)
        ogg_bytes = buf.getvalue()

        # Transcribe
        from ai.voice import transcribe_voice_ogg
        transcript = await transcribe_voice_ogg(ogg_bytes)

        if not transcript:
            await message.answer(
                "Ой, Настя не расслышала... Говори чётче! Или напиши текстом, я не глухая... ну почти"
            )
            return

        # Save transcript as user message
        await db.get_or_create_user(
            user_id=user_id,
            username=message.from_user.username or "",
            first_name=message.from_user.first_name or "",
        )
        msg_count = await db.increment_messages(user_id)
        await db.add_message(user_id, "user", f"[Голосовое] {transcript}")

        # Get response
        mood = _user_moods.get(user_id, "капризная")
        if db:
            mood_data = await db.get_random_mood()
            mood = mood_data.get("mood", mood)
            _user_moods[user_id] = mood

        system_prompt = NASTYA_SYSTEM_PROMPT + f"\n\nТВОЁ ТЕКУЩЕЕ НАСТРОЕНИЕ: {mood}. Веди себя соответственно."
        history = await db.get_history(user_id, limit=20)

        result = await ai_router.chat(
            prompt=transcript,
            system_prompt=system_prompt,
            messages=history,
        )

        response_text = result.text or "Ммм... Настя задумалась. Попробуй ещё раз"
        await db.add_message(user_id, "assistant", response_text)

        # Maybe ask for stars
        tracker = _stars_tracker.get(user_id, {"count": 0, "last_ask": 0})
        tracker["count"] = msg_count
        if should_ask_stars(msg_count, tracker["last_ask"]):
            response_text += f"\n\n{get_stars_line()}"
            tracker["last_ask"] = __import__("time").time()
        _stars_tracker[user_id] = tracker

        await message.answer(response_text)

    except AllProvidersExhaustedError:
        await message.answer("Блин, у Насти всё сломалось! Все AI-провайдеры недоступны. Попробуй через пару минут")
    except Exception as e:
        logger.error(f"Voice handler error: {e}")
        await message.answer("Ой, что-то пошло не так... Настя не смогла послушать. Напиши текстом!")


# ── Photo message handler ────────────────────────────────────
@router.message(F.photo)
async def handle_photo(message: Message, db=None, ai_router=None) -> None:
    """Handle photo messages — understand and respond."""
    if not db or not ai_router:
        await message.answer("Ой, у Насти что-то сломалось... Попробуй позже")
        return

    user_id = message.from_user.id
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    try:
        # Get the largest photo
        photo = message.photo[-1]
        file = await message.bot.get_file(photo.file_id)

        # Download to bytes
        import io
        buf = io.BytesIO()
        await message.bot.download_file(file.file_path, buf)
        image_bytes = buf.getvalue()
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")

        # Build prompt
        caption = message.caption or "Что скажешь про это фото?"
        prompt = f"Пользователь прислал фото и пишет: {caption}. Опиши что видишь и прокомментируй в своём стиле — капризно, весело, с оценкой."

        # Ensure user
        await db.get_or_create_user(
            user_id=user_id,
            username=message.from_user.username or "",
            first_name=message.from_user.first_name or "",
        )
        msg_count = await db.increment_messages(user_id)
        await db.add_message(user_id, "user", f"[Фото] {caption}")

        # Get mood
        mood = _user_moods.get(user_id, "капризная")
        if db:
            mood_data = await db.get_random_mood()
            mood = mood_data.get("mood", mood)
            _user_moods[user_id] = mood

        system_prompt = NASTYA_SYSTEM_PROMPT + f"\n\nТВОЁ ТЕКУЩЕЕ НАСТРОЕНИЕ: {mood}. Веди себя соответственно. Пользователь прислал фото — опиши что на нём и прокомментируй в своём стиле."

        history = await db.get_history(user_id, limit=20)

        result = await ai_router.chat_with_image(
            prompt=prompt,
            image_base64=image_b64,
            system_prompt=system_prompt,
            messages=history,
        )

        response_text = result.text or "Ой, что-то Настя разглядела, но слова забыла... Попробуй ещё раз"
        await db.add_message(user_id, "assistant", response_text)

        # Maybe ask for stars
        tracker = _stars_tracker.get(user_id, {"count": 0, "last_ask": 0})
        tracker["count"] = msg_count
        if should_ask_stars(msg_count, tracker["last_ask"]):
            response_text += f"\n\n{get_stars_line()}"
            tracker["last_ask"] = __import__("time").time()
        _stars_tracker[user_id] = tracker

        # Send long messages in chunks
        if len(response_text) > 4096:
            for i in range(0, len(response_text), 4096):
                await message.answer(response_text[i:i + 4096])
        else:
            await message.answer(response_text)

    except AllProvidersExhaustedError:
        await message.answer("Блин, у Насти всё сломалось! Попробуй через пару минут")
    except Exception as e:
        logger.error(f"Photo handler error: {e}")
        await message.answer("Ой, Настя не может разглядеть фото... Попробуй ещё раз или напиши текстом!")


# ── Document/file handler ────────────────────────────────────
@router.message(F.document)
async def handle_document(message: Message, db=None, ai_router=None) -> None:
    """Handle documents — acknowledge and respond."""
    if not db or not ai_router:
        await message.answer("Ой, у Насти что-то сломалось...")
        return

    user_id = message.from_user.id
    doc = message.document
    file_name = doc.file_name or "файл"
    mime_type = doc.mime_type or "unknown"

    # If it's an image file
    if mime_type.startswith("image/"):
        await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
        try:
            file = await message.bot.get_file(doc.file_id)
            import io
            buf = io.BytesIO()
            await message.bot.download_file(file.file_path, buf)
            image_bytes = buf.getvalue()
            image_b64 = base64.b64encode(image_bytes).decode("utf-8")

            caption = message.caption or f"Что скажешь про этот файл {file_name}?"
            prompt = f"Пользователь прислал фото (файл {file_name}) и пишет: {caption}. Опиши что видишь и прокомментируй в своём стиле."

            await db.get_or_create_user(user_id=user_id, username=message.from_user.username or "", first_name=message.from_user.first_name or "")
            msg_count = await db.increment_messages(user_id)
            await db.add_message(user_id, "user", f"[Фото-файл: {file_name}] {caption}")

            mood = _user_moods.get(user_id, "капризная")
            if db:
                mood_data = await db.get_random_mood()
                mood = mood_data.get("mood", mood)
                _user_moods[user_id] = mood

            system_prompt = NASTYA_SYSTEM_PROMPT + f"\n\nТВОЁ ТЕКУЩЕЕ НАСТРОЕНИЕ: {mood}. Пользователь прислал фото — опиши и прокомментируй."
            history = await db.get_history(user_id, limit=20)

            result = await ai_router.chat_with_image(prompt=prompt, image_base64=image_b64, system_prompt=system_prompt, messages=history)
            response_text = result.text or "Ой, Настя посмотрела, но зависла..."
            await db.add_message(user_id, "assistant", response_text)

            if len(response_text) > 4096:
                for i in range(0, len(response_text), 4096):
                    await message.answer(response_text[i:i + 4096])
            else:
                await message.answer(response_text)
            return
        except Exception as e:
            logger.error(f"Image document handler error: {e}")

    # Non-image document
    await db.get_or_create_user(user_id=user_id, username=message.from_user.username or "", first_name=message.from_user.first_name or "")
    msg_count = await db.increment_messages(user_id)
    await db.add_message(user_id, "user", f"[Файл: {file_name}] {message.caption or ''}")

    response = f"Ой, файл {file_name}! Настя не умеет читать файлы... Но звучит важно! Расскажи что там?"
    await message.answer(response)
    await db.add_message(user_id, "assistant", response)


# ── Sticker handler ──────────────────────────────────────────
@router.message(F.sticker)
async def handle_sticker(message: Message, db=None, ai_router=None) -> None:
    """Respond to stickers in Nastya style."""
    sticker = message.sticker
    emoji = sticker.emoji or ""

    responses = [
        f"Ой, стикер! {emoji} Настя тоже так может!",
        f"Это что за стикер? Настя не впечатлена... Или впечатлена! {emoji}",
        f"Прикинь, Настя обожает стикеры! Но этот так себе... Шучу, нормальный! {emoji}",
        f"{emoji} А у тебя стикеры получше есть? Настя требовательная!",
    ]
    import random
    await message.answer(random.choice(responses))

    if db:
        await db.get_or_create_user(user_id=message.from_user.id, username=message.from_user.username or "", first_name=message.from_user.first_name or "")
        await db.increment_messages(message.from_user.id)


# ── Text chat handler ────────────────────────────────────────
@router.message(F.text, ~F.text.startswith("/"))
async def handle_chat(message: Message, db=None, ai_router=None) -> None:
    """Main chat handler — Nastya talks with context memory."""
    if not db or not ai_router:
        await message.answer("Ой, у Насти что-то сломалось... Попробуй позже")
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

    # Get mood (changes periodically per user)
    mood = _user_moods.get(user_id, "капризная")
    if db:
        mood_data = await db.get_random_mood()
        mood = mood_data.get("mood", mood)
        _user_moods[user_id] = mood

    # Build system prompt with current mood
    system_prompt = NASTYA_SYSTEM_PROMPT + f"\n\nТВОЁ ТЕКУЩЕЕ НАСТРОЕНИЕ: {mood}. Веди себя соответственно."

    # Get chat history for context
    history = await db.get_history(user_id, limit=30)

    # Save user message
    await db.add_message(user_id, "user", text)

    try:
        result = await ai_router.chat(
            prompt=text,
            system_prompt=system_prompt,
            messages=history,
        )

        response_text = result.text or "Ммм... Настя задумалась. Попробуй ещё раз"

        # Save assistant message
        await db.add_message(user_id, "assistant", response_text)

        # Check if should ask for stars
        tracker = _stars_tracker.get(user_id, {"count": 0, "last_ask": 0})
        tracker["count"] = msg_count

        if should_ask_stars(msg_count, tracker["last_ask"]):
            response_text += f"\n\n{get_stars_line()}"
            tracker["last_ask"] = __import__("time").time()

        _stars_tracker[user_id] = tracker

        # Update proactive tracker
        pro = _proactive_tracker.get(user_id, {"count": 0, "last_proactive": 0, "chat_id": message.chat.id})
        pro["count"] = msg_count
        pro["chat_id"] = message.chat.id
        _proactive_tracker[user_id] = pro

        # Send (split if long)
        if len(response_text) > 4096:
            for i in range(0, len(response_text), 4096):
                await message.answer(response_text[i:i + 4096])
        else:
            await message.answer(response_text)

    except AllProvidersExhaustedError:
        await message.answer(
            "Блин, у Насти всё сломалось! Все AI-провайдеры недоступны. "
            "Попробуй через пару минут, ладно?"
        )
    except Exception as e:
        logger.error(f"Chat error: {e}")
        await message.answer("Ой, что-то пошло не так... Настя запуталась. Попробуй ещё!")


# ── Proactive message scheduler ──────────────────────────────
async def check_and_send_proactive(bot, db, ai_router) -> None:
    """Periodically check if we should send proactive messages to active users."""
    import time
    for user_id, pro in list(_proactive_tracker.items()):
        if should_send_proactive(pro["count"], pro["last_proactive"]):
            try:
                msg = get_proactive_message()
                chat_id = pro.get("chat_id", user_id)
                await bot.send_message(chat_id, msg)
                pro["last_proactive"] = time.time()
                pro["count"] = 0  # Reset after proactive
                logger.info(f"Sent proactive to user {user_id}")
            except Exception as e:
                logger.warning(f"Proactive send failed for {user_id}: {e}")
