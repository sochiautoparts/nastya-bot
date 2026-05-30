"""Nastya Chat Handler — natural conversation, no commands.
Everything happens naturally: mood, wants, stars, topics.
"""
import logging
import random
import base64
import io
import time
from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice
from bot.config import NASTYA_SYSTEM_PROMPT, DONATION_AMOUNTS, DONATION_LABELS, PROACTIVE_COOLDOWN
from ai.router import AllProvidersExhaustedError

logger = logging.getLogger(__name__)
router = Router()

# Per-user state
_stars_tracker: dict = {}      # user_id -> {count, last_ask}
_proactive_tracker: dict = {}  # user_id -> {last_proactive, chat_id}

# ── Stars request with payment buttons ───────────────────────
STARS_REQUESTS = [
    "Слушай, а кинь Насте звёздочек? Мне на маникюр надо",
    "Ну пожалуйста, звёздочку Насте! Я потом добрая буду",
    "Кинь звёзд, а? Настя на шопинг копит",
    "Ты же не жадина? Поддержи Настю звёздочками!",
    "Настя старается для тебя! Ну кинь звёздочку?",
    "Между прочим, Настя тоже хочет кушать! Звёзды приветствуются",
    "Настя сегодня добрая, но звёздочки всё равно принимает",
    "Маленькая звёздочка Насте — огромная радость!",
    "Кинь звёздочку, а? Насте на шоколадку!",
]

# ── Proactive messages ───────────────────────────────────────
PROACTIVE_MESSAGES = [
    "Что сидишь? Пошли гулять!",
    "Эй, ты где? Настя скучает... Не говори никому!",
    "Ау! Настя тут одна сидит, скука!",
    "Короче, я тут подумала... Жизнь — странная штука. Или нет. Ладно, забыла",
    "Настя тут ремонт смотрит... Хочу розовую плитку в ванную!",
    "Прикинь, я хочу шоколадку. И мороженку. Давай все три!",
    "Слушай, Настя тут на йогу сходила... Ну ладно, дошла до двери и вернулась",
    "Настя хочет суши... Это прям СРОЧНО!",
    "Ой, я тут рецепт нашла — паста карбонара! Надо приготовить... Ну или заказать",
    "Настя тут подумала про Бали... Хочу туда! С тобой поедем?",
]

# ── Nastya's wants ────────────────────────────────────────────
NASTYA_WANTS = [
    "Настя хочет шоколадку... Нет, мороженку! Нет, и то и то!",
    "Ой, мне так хочется вафельку... С кремом!",
    "Насте нужен новый айфон. Этот уже второй день как вышел!",
    "Хочу пироженку... Наполеон. Или медовик. Ладно, оба!",
    "Настя хочет мороженку... Фисташковое!",
    "Хочу на Бали... Или на Пхукет... Ладно, куда угодно, лишь бы море!",
    "Хочу щенка... Или котёнка. Нет, обоих!",
    "Настя хочет плед... Мягкий. И халатик. И чтобы розовые!",
    "Насте нужен ремонт... Обои сменить. И плитку. И вообще всё!",
    "Хочу худеть... Нет, хочу пироженку. Ладно, сначала пироженку!",
    "Настя хочет спать... Но сначала сериал. Но сначала шоколадку...",
    "Хочу на тренировку... Ну ладно, хочу хот-дог после тренировки. А тренировку пропущу.",
]

# ── Gender detection helper ──────────────────────────────────
MALE_NAMES_ENDINGS = ("й", "н", "р", "л", "м", "в", "с", "к", "т", "г", "б", "д", "п", "з", "ж")
FEMALE_NAMES_ENDINGS = ("а", "я", "ь")


def _guess_gender_from_name(first_name: str) -> str:
    """Try to guess gender from Russian first name."""
    if not first_name:
        return "unknown"
    name = first_name.strip()
    if name.lower().endswith(FEMALE_NAMES_ENDINGS):
        # Names ending in -а, -я are typically female
        # But some male names end in -я (Илья, Никита)
        male_exceptions = ("илья", "никита", "данила", "добрыня", "кузьма")
        if name.lower() in male_exceptions:
            return "male"
        if name.lower().endswith("а") or name.lower().endswith("я"):
            return "female"
    if name.lower().endswith(MALE_NAMES_ENDINGS):
        return "male"
    return "unknown"


def _get_gender_pronoun(gender: str) -> str:
    """Get appropriate pronoun for addressing."""
    if gender == "male":
        return "он"
    elif gender == "female":
        return "она"
    return "ты"


# ── Start handler (only one that looks like a command) ────────
@router.message(F.text == "/start")
async def cmd_start(message: Message, db=None, ai_router=None) -> None:
    """Welcome from Nastya — natural, not a menu."""
    user = message.from_user
    name = user.first_name or "незнакомец"

    if db:
        await db.get_or_create_user(
            user_id=user.id, username=user.username or "", first_name=name,
        )
        # Try to detect gender from name
        gender = _guess_gender_from_name(name)
        if gender != "unknown":
            await db.set_gender(user.id, gender)

    # Natural greeting, not a menu
    greetings = [
        f"О, привет, {name}! Я Настя. Будем болтать или как?",
        f"Привет! Я Настя. Ты мне сразу нравишься. Ну или нет, посмотрим!",
        f"Ой, {name}! Привет! Настя тут. Будем знакомы!",
        f"Ну привет, {name}. Я Настя. Не путай меня с кем-то, я одна такая!",
    ]
    await message.answer(random.choice(greetings))


# ── Clear history ────────────────────────────────────────────
@router.message(F.text == "/clear")
async def cmd_clear(message: Message, db=None, ai_router=None) -> None:
    if db:
        await db.clear_history(message.from_user.id)
    await message.answer("Что? Ничего не помню! Начнём сначала!")


# ── Donate command (minimal, shows options) ──────────────────
@router.message(F.text == "/donate")
async def cmd_donate(message: Message, db=None, ai_router=None) -> None:
    """Show donation options with inline buttons."""
    buttons = []
    for amount in DONATION_AMOUNTS:
        label = DONATION_LABELS.get(amount, f"Поддержать {amount}")
        buttons.append([InlineKeyboardButton(
            text=f"{label} — {amount} ⭐",
            callback_data=f"donate_{amount}",
        )])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    donated_text = ""
    if db:
        total = await db.get_total_donated(message.from_user.id)
        if total > 0:
            donated_text = f"\n\nТы уже подарил Насте {total} ⭐! Настя благодарна!"

    text = f"Поддержи Настю звёздочками!{donated_text}"
    await message.answer(text, reply_markup=kb)


# ── Voice message handler ────────────────────────────────────
@router.message(F.voice)
async def handle_voice(message: Message, db=None, ai_router=None) -> None:
    if not db or not ai_router:
        await message.answer("Ой, Настя не может сейчас разговаривать... Попробуй позже")
        return

    user_id = message.from_user.id
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    try:
        voice = message.voice
        file = await message.bot.get_file(voice.file_id)
        buf = io.BytesIO()
        await message.bot.download_file(file.file_path, buf)
        ogg_bytes = buf.getvalue()

        transcript = await ai_router.transcribe_voice(ogg_bytes)

        if not transcript:
            await message.answer("Ой, Настя не расслышала... Говори чётче или напиши текстом!")
            return

        # Process as text
        await _process_text_message(message, transcript, db, ai_router, is_voice=True)

    except Exception as e:
        logger.error(f"Voice handler error: {e}")
        await message.answer("Ой, Настя не смогла послушать... Напиши текстом!")


# ── Photo message handler ────────────────────────────────────
@router.message(F.photo)
async def handle_photo(message: Message, db=None, ai_router=None) -> None:
    if not db or not ai_router:
        await message.answer("Ой, Настя не может сейчас смотреть... Попробуй позже")
        return

    user_id = message.from_user.id
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    try:
        photo = message.photo[-1]
        file = await message.bot.get_file(photo.file_id)
        buf = io.BytesIO()
        await message.bot.download_file(file.file_path, buf)
        image_bytes = buf.getvalue()
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")

        caption = message.caption or "Что скажешь про это фото?"
        prompt = f"Пользователь прислал фото и пишет: {caption}. Посмотри на фото и ответь в своём стиле — как Настя."

        await db.get_or_create_user(
            user_id=user_id, username=message.from_user.username or "",
            first_name=message.from_user.first_name or "",
        )
        msg_count = await db.increment_messages(user_id)
        await db.add_message(user_id, "user", f"[Фото] {caption}")

        mood = await db.get_user_mood(user_id)
        gender = await db.get_gender(user_id)
        gender_ctx = f"Собеседник — {'мужчина' if gender == 'male' else 'женщина' if gender == 'female' else 'пол неизвестен'}. Обращайся соответственно." if gender != "unknown" else ""

        system_prompt = NASTYA_SYSTEM_PROMPT + f"\n\nТВОЁ ТЕКУЩЕЕ НАСТРОЕНИЕ: {mood}. Веди себя соответственно. {gender_ctx} Пользователь прислал фото — посмотри и прокомментируй в своём стиле."

        history = await db.get_history(user_id, limit=30)

        result = await ai_router.chat_with_image(
            prompt=prompt, image_base64=image_b64,
            system_prompt=system_prompt, messages=history,
        )

        response_text = result.text or "Ой, Настя посмотрела, но зависла..."
        await db.add_message(user_id, "assistant", response_text)

        # Maybe add stars request with button
        response_text = await _maybe_add_stars(response_text, user_id, msg_count, db, message)

        if len(response_text) > 4096:
            for i in range(0, len(response_text), 4096):
                await message.answer(response_text[i:i + 4096])
        else:
            await message.answer(response_text)

    except AllProvidersExhaustedError:
        await message.answer("Ой, Настя зависла... Попробуй ещё разочек!")
    except Exception as e:
        logger.error(f"Photo handler error: {e}")
        await message.answer("Настя не может разглядеть фото... Попробуй ещё раз!")


# ── Document handler (image docs go to VLM) ──────────────────
@router.message(F.document)
async def handle_document(message: Message, db=None, ai_router=None) -> None:
    if not db or not ai_router:
        return

    doc = message.document
    mime_type = doc.mime_type or "unknown"
    file_name = doc.file_name or "файл"

    # If it's an image document
    if mime_type.startswith("image/"):
        await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
        try:
            file = await message.bot.get_file(doc.file_id)
            buf = io.BytesIO()
            await message.bot.download_file(file.file_path, buf)
            image_bytes = buf.getvalue()
            image_b64 = base64.b64encode(image_bytes).decode("utf-8")

            caption = message.caption or f"Что скажешь про это фото?"
            prompt = f"Пользователь прислал фото (файл {file_name}) и пишет: {caption}. Посмотри и прокомментируй как Настя."

            user_id = message.from_user.id
            await db.get_or_create_user(user_id=user_id, username=message.from_user.username or "", first_name=message.from_user.first_name or "")
            msg_count = await db.increment_messages(user_id)
            await db.add_message(user_id, "user", f"[Фото: {file_name}] {caption}")

            mood = await db.get_user_mood(user_id)
            system_prompt = NASTYA_SYSTEM_PROMPT + f"\n\nТВОЁ ТЕКУЩЕЕ НАСТРОЕНИЕ: {mood}. Пользователь прислал фото — посмотри и прокомментируй."
            history = await db.get_history(user_id, limit=30)

            result = await ai_router.chat_with_image(prompt=prompt, image_base64=image_b64, system_prompt=system_prompt, messages=history)
            response_text = result.text or "Ой, Настя посмотрела, но зависла..."
            await db.add_message(user_id, "assistant", response_text)

            response_text = await _maybe_add_stars(response_text, user_id, msg_count, db, message)

            if len(response_text) > 4096:
                for i in range(0, len(response_text), 4096):
                    await message.answer(response_text[i:i + 4096])
            else:
                await message.answer(response_text)
            return
        except Exception as e:
            logger.error(f"Image doc error: {e}")

    # Non-image document
    user_id = message.from_user.id
    await db.get_or_create_user(user_id=user_id, username=message.from_user.username or "", first_name=message.from_user.first_name or "")
    await db.increment_messages(user_id)
    await db.add_message(user_id, "user", f"[Файл: {file_name}] {message.caption or ''}")
    await message.answer(f"Ой, файл {file_name}! Настя не умеет читать файлы... Но звучит важно! Расскажи что там?")


# ── Sticker handler ──────────────────────────────────────────
@router.message(F.sticker)
async def handle_sticker(message: Message, db=None, ai_router=None) -> None:
    responses = [
        "Ой, стикер! Настя тоже так может!",
        "Это что за стикер? Настя не впечатлена... Или впечатлена!",
        "Настя обожает стикеры! Но этот так себе... Шучу, нормальный!",
        "А у тебя стикеры получше есть? Настя требовательная!",
    ]
    await message.answer(random.choice(responses))
    if db:
        await db.get_or_create_user(user_id=message.from_user.id, username=message.from_user.username or "", first_name=message.from_user.first_name or "")
        await db.increment_messages(message.from_user.id)


# ── Main text chat handler ──────────────────────────────────
@router.message(F.text, ~F.text.startswith("/"))
async def handle_chat(message: Message, db=None, ai_router=None) -> None:
    """Natural conversation — no commands, just chat."""
    if not db or not ai_router:
        await message.answer("Ой, Настя не может сейчас говорить... Попробуй позже")
        return

    text = message.text
    await _process_text_message(message, text, db, ai_router)


async def _process_text_message(message: Message, text: str, db, ai_router,
                                 is_voice: bool = False) -> None:
    """Process text message (from text, voice transcription, etc)."""
    user_id = message.from_user.id

    # Ensure user
    user = await db.get_or_create_user(
        user_id=user_id, username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
    )

    # Detect gender from name if unknown
    if user.get("gender", "unknown") == "unknown":
        name = message.from_user.first_name or ""
        gender = _guess_gender_from_name(name)
        if gender != "unknown":
            await db.set_gender(user_id, gender)

    msg_count = await db.increment_messages(user_id)

    # Typing indicator
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    # Get mood and gender for context
    mood = await db.get_user_mood(user_id)
    gender = await db.get_gender(user_id)

    # Build context
    gender_ctx = ""
    if gender == "male":
        gender_ctx = "Собеседник — мужчина. Обращайся соответственно (не 'девочка', 'подруга')."
    elif gender == "female":
        gender_ctx = "Собеседник — женщина. Можешь называть 'подруга', 'девочка' и т.д."

    voice_ctx = " Пользователь прислал голосовое сообщение, ты его 'услышала'." if is_voice else ""

    system_prompt = (
        NASTYA_SYSTEM_PROMPT +
        f"\n\nТВОЁ ТЕКУЩЕЕ НАСТРОЕНИЕ: {mood}. Веди себя соответственно." +
        f"\n{gender_ctx}" +
        voice_ctx
    )

    # Save user message
    prefix = "[Голосовое] " if is_voice else ""
    await db.add_message(user_id, "user", f"{prefix}{text}")

    # Get history
    history = await db.get_history(user_id, limit=30)

    try:
        result = await ai_router.chat(
            prompt=text, system_prompt=system_prompt, messages=history,
        )

        response_text = result.text or "Ммм... Настя задумалась. Повтори?"

        # Save assistant message
        await db.add_message(user_id, "assistant", response_text)

        # Maybe add stars request with payment button
        response_text = await _maybe_add_stars(response_text, user_id, msg_count, db, message)

        # Maybe add Nastya want
        if _should_add_want(msg_count):
            want = random.choice(NASTYA_WANTS)
            response_text += f"\n\n{want}"

        # Update proactive tracker
        _proactive_tracker[user_id] = {
            "last_proactive": _proactive_tracker.get(user_id, {}).get("last_proactive", 0),
            "chat_id": message.chat.id,
        }

        # Send response
        if len(response_text) > 4096:
            for i in range(0, len(response_text), 4096):
                await message.answer(response_text[i:i + 4096])
        else:
            await message.answer(response_text)

    except AllProvidersExhaustedError:
        logger.error(f"All providers exhausted for user={user_id}")
        await message.answer("Ой, Настя зависла... Попробуй ещё разочек!")
    except Exception as e:
        logger.error(f"Chat error for user={user_id}: {e}")
        await message.answer("Ой, что-то пошло не так... Настя запуталась. Попробуй ещё!")


async def _maybe_add_stars(response_text: str, user_id: int, msg_count: int,
                            db, message: Message) -> str:
    """Maybe add stars request with inline payment button."""
    tracker = _stars_tracker.get(user_id, {"count": 0, "last_ask": 0})
    tracker["count"] = msg_count

    # Ask every 6-10 messages, at least 3 min apart
    if msg_count >= 5 and time.time() - tracker["last_ask"] > 180 and random.random() < 0.12:
        stars_line = random.choice(STARS_REQUESTS)

        # Add quick donate buttons
        buttons = [
            [
                InlineKeyboardButton(text="☕ 100 ⭐", callback_data="donate_100"),
                InlineKeyboardButton(text="💅 1000 ⭐", callback_data="donate_1000"),
            ],
            [
                InlineKeyboardButton(text="👗 3000 ⭐", callback_data="donate_3000"),
                InlineKeyboardButton(text="✈️ 10000 ⭐", callback_data="donate_10000"),
            ],
            [InlineKeyboardButton(text="💝 Другая сумма", callback_data="donate_more")],
        ]
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)

        # Send stars request as separate message with buttons
        try:
            await message.answer(stars_line, reply_markup=kb)
        except Exception:
            pass  # Don't break the flow

        tracker["last_ask"] = time.time()

    _stars_tracker[user_id] = tracker
    return response_text


def _should_add_want(msg_count: int) -> bool:
    """Very rarely add a Nastya want (not more than ~5% after 8 messages)."""
    return msg_count >= 8 and random.random() < 0.05


# ── Donation callback handlers ───────────────────────────────
@router.callback_query(F.data.startswith("donate_"))
async def callback_donate(callback, db=None, ai_router=None) -> None:
    data = callback.data.replace("donate_", "")

    if data == "more":
        # Show all amounts
        buttons = []
        for amount in DONATION_AMOUNTS:
            label = DONATION_LABELS.get(amount, f"Поддержать {amount}")
            buttons.append([InlineKeyboardButton(
                text=f"{label} — {amount} ⭐",
                callback_data=f"donate_{amount}",
            )])
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        await callback.message.edit_text("Выбери сумму:", reply_markup=kb)
        await callback.answer()
        return

    amount = int(data)
    label = DONATION_LABELS.get(amount, "Поддержка Насти")

    await callback.message.answer_invoice(
        title=label,
        description=f"Поддержка Насти — {amount} Telegram Stars",
        payload=f"donate_{amount}",
        currency="XTR",
        prices=[LabeledPrice(label=label, amount=amount)],
        provider_token="",
    )
    await callback.answer()


# ── Proactive messages ───────────────────────────────────────
async def check_and_send_proactive(bot, db, ai_router) -> None:
    """Send proactive messages to recently active users."""
    now = time.time()
    for user_id, pro in list(_proactive_tracker.items()):
        last = pro.get("last_proactive", 0)
        if now - last < PROACTIVE_COOLDOWN:
            continue
        try:
            msg = random.choice(PROACTIVE_MESSAGES)
            chat_id = pro.get("chat_id", user_id)
            await bot.send_message(chat_id, msg)
            pro["last_proactive"] = now
            logger.info(f"Sent proactive to user {user_id}")
        except Exception as e:
            logger.warning(f"Proactive failed for {user_id}: {e}")
