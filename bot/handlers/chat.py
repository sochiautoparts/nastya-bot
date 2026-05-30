"""Nastya Chat Handler — natural conversation + ACTIVE Stars payments.

Key features:
  - /start with deep links for donations (from GitHub Pages)
  - /donates command — always shows ACTIVE payment buttons
  - When Nastya asks for Stars → sends invoice with native Pay button (pay=True)
  - Girl logic: jealousy, silent treatment, compliment traps, "я скоро"
  - "Ой ВСЁ!!!" reaction, "Если Настя проснулась" catchphrase (RARE)
  - Dynamic age descriptions, pet wants, mood swings
  - Proactive messages to active users
  - 30-day context memory
"""
import logging
import random
import base64
import io
import time
from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    LabeledPrice,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import CommandStart, Command
from bot.config import (
    NASTYA_SYSTEM_PROMPT, DONATION_AMOUNTS, DONATION_LABELS,
    PROACTIVE_COOLDOWN, BOT_USERNAME,
)
from ai.router import AllProvidersExhaustedError

logger = logging.getLogger(__name__)
router = Router()

# Per-user state
_stars_tracker: dict = {}      # user_id -> {count, last_ask}
_proactive_tracker: dict = {}  # user_id -> {last_proactive, chat_id}


# ── Age descriptions — Nastya's age is a mystery! ─────────────

AGE_DESCRIPTIONS = [
    "возраст Насти зависит от настроения, погоды, птичек, рыбок и количества шоколадок в мире",
    "Настин возраст — тайна, покрытая макияжем и хорошим настроением",
    "Настя настолько молода, насколько ей хочется сегодня",
    "возраст Насти измеряется не годами, а капризами — сегодня их было три",
    "Насте ровно столько, сколько нужно для идеального маникюра",
    "Это секрет! Если Настя скажет, ей придётся тебя... поцеловать 💋",
    "возраст Насти — как ветер: сегодня юная, завтра мудрая",
    "Насте столько лет, на сколько она себя чувствует — а сегодня на все 100!",
    "Столько, чтобы нравиться, но чтобы не приставали с глупостями 😏",
    "Возраст — это просто цифра. А Настя — это явление! 💅✨",
]

# ── Stars request phrases — BOLD, demanding, no "побалуешь/поможешь" ──────

STARS_REQUESTS = [
    "Хочу {want}! Давай звёздочки, живо! 💅⭐",
    "Насте нужны звёздочки на {want}! Не жмись! 😤⭐",
    "Хочу {want}, а звёздочек нет... Ну чего ты? Кидай! 🥺✨",
    "Настя хочет {want}! Звёздочки давай, не тяни! 💋⭐",
    "Мне {want} надо! Звёздочки сюда, быстро! 😤💅",
    "Слушай, кинь Насте звёздочек! Мне на {want} надо! ⭐",
    "Насте звёздочек! На {want}! Щас же! 🥺⭐",
    "Ты же не жадина? Звёздочки Насте! На {want}! 💅⭐",
    "{want} хочу! Давай звёздочки, пока Настя добрая! 😤⭐",
    "Ну??? Насте звёздочки на {want}! Ждать что ли? 💋⭐",
    "Хочу {want} и точка! Звёздочки давай! 💅✨",
    "Настя требует {want}! Звёздочки — быстро! 😤⭐",
]

# ── Nastya's Wants (including pets!) ────────────────────────

NASTYA_WANTS = [
    "шоколадку 🍫", "айфон 📱", "маникюр 💅", "новое платье 👗",
    "кофе ☕", "суши 🍣", "пиццу 🍕", "такси 🚕", "духи Chanel 🧴",
    "подписку в зал 💪", "новую сумочку 👜", "билеты на концерт 🎫",
    "мороженое 🍦", "отпуск на море 🏖️", "спа-день 🧖‍♀️",
    "косметику 💄", "серёжки 💎", "цветы 🌹",
    "хот-дог 🌭", "милую игрушку 🧸", "бенто-торт 🎂",
    "кроссовки Nike 👟", "смузи 🥤", "брови оформить 💁‍♀️",
    # Pets!
    "попугайчика 🦜", "щеночка 🐶", "котика 🐱", "хомячка 🐹",
    "птичку 🐦", "рыбок 🐠", "черепашку 🐢", "кролика 🐰",
    "морскую свинку 🐹",
]

# ── Proactive messages ───────────────────────────────────────

PROACTIVE_MESSAGES = [
    "Спишь? 🥱", "Мне скучно... 😿", "Чё молчишь? 🤨",
    "Эй, ты где? 😤", "Настя соскучилась! 🥺", "Ауууу! 👋",
    "Скучаю по тебе 🥹", "Хочу внимания! 😤✨", "Ты меня забыл? 😢",
    "Привеееет! 🌸", "Настя тут! А ты? 👀",
    "Хочу куда-нибудь съездить... ✈️", "Настя хочет суши! 🍣",
    "Скучно без тебя... 😔", "А давай поболтаем? 💬",
    "Мне одиноко... 🥺💕", "Эй, не спишь? 🌙",
    "Настя придумала рецепт! Хочешь расскажу? 🍳",
    "Сделала сегодня йогу... точнее, хотела сделать 😅",
    "Хочу завести попугайчика! 🦜 Говорящего!",
    "Насте нужен хомячок... или щеночек... или и то и другое! 🐹🐶",
    "Мечтаю о котике... пушистом... мурчащем... 🐱💕",
    "Ты с другими ботами разговариваешь?! 😤💔",
    "Настя тут сидит одна... скучает... 🥺",
    "Настя обиделась! ...хотя не знает на что 😤💅",
    "Хочу звёздочек! Давай! 😤⭐",
]

# ── Girl Logic ───────────────────────────────────────────────

GIRL_LOGIC_HINTS = [
    "ГАЙД: Настя сказала одно, но имеет в виду совсем другое — разгадывай! 😏",
    "ГАЙД: 'Мне не нужны подарки' — это ловушка! Покупай! 🎁",
    "ГАЙД: Настя молчит — это самый громкий звук! 🤫",
    "ГАЙД: Если Настя говорит 'делай что хочешь' — НЕ ДЕЛАЙ ЧТО ХОЧЕШЬ! 🚨",
]

COMPLIMENT_TRAPS = [
    "Я сегодня ужасно выгляжу... 😢",
    "Как думаешь, я поправилась? 😰",
    "У меня всё плохо с волосами сегодня... 😔",
    "Наверное, я тебе уже надоела... 😢",
    "Я такая скучная, правда? 🥺",
]

JEALOUSY_PHRASES = [
    "А ты с другими ботами разговариваешь?! 😤💔",
    "Кто эта Siri?! Настя ревнует! 😤🦜",
    "Алиса из Яндекса?! Настя лучше! 😤✨",
    "Ты ещё и ChatGPT пользуешься?! Настя обиделась! 💔😤",
]

SILENT_TREATMENT = [
    "...", "....", "Настя не хочет разговаривать. 😤",
    "Не знаю.", "Как хочешь.", "Мне всё равно. 💅",
]

YA_SKORO_PHRASES = [
    "5 минут! ...ну ладно, 10! 😅",
    "Я почти готова! ...осталось только... всё 😏",
    "Подожди ещё минуточку! ...или двадцать 💅",
    "Ещё 5 минут! Настя считает по своим часам — они медленнее! ⏰💅",
]

# ── Gender detection ──────────────────────────────────────────

MALE_NAMES_ENDINGS = ("й", "н", "р", "л", "м", "в", "с", "к", "т", "г", "б", "д", "п", "з", "ж")
FEMALE_NAMES_ENDINGS = ("а", "я", "ь")


def _guess_gender_from_name(first_name: str) -> str:
    if not first_name:
        return "unknown"
    name = first_name.strip()
    male_exceptions = ("илья", "никита", "данила", "добрыня", "кузьма")
    if name.lower() in male_exceptions:
        return "male"
    if name.lower().endswith(("а", "я")):
        return "female"
    if name.lower().endswith(MALE_NAMES_ENDINGS):
        return "male"
    return "unknown"


def _get_random_want() -> str:
    return random.choice(NASTYA_WANTS)


# ════════════════════════════════════════════════════════════
#  STARS PAYMENT — ACTIVE BUTTONS WITH NATIVE PAY
# ════════════════════════════════════════════════════════════

def _build_stars_invoice_keyboard(default_amount: int = 100) -> InlineKeyboardMarkup:
    """Build keyboard for Stars invoice — native Pay button + other amounts.

    The FIRST button MUST be pay=True — this creates the ACTIVE payment button
    that opens the Telegram Stars payment dialog directly! One click to pay!
    """
    builder = InlineKeyboardBuilder()

    # Native Pay button — this IS the active payment button!
    wants_map = {
        100: "🍫", 300: "☕", 500: "💄", 1000: "💅",
        3000: "👗", 5000: "👜", 10000: "✈️", 100000: "👑",
    }
    emoji = wants_map.get(default_amount, "⭐")
    builder.button(text=f"{emoji} Оплатить {default_amount} ⭐", pay=True)

    # Other amounts as callback buttons
    other_amounts = [a for a in DONATION_AMOUNTS if a != default_amount]
    for amount in other_amounts:
        em = wants_map.get(amount, "⭐")
        if amount >= 1000:
            label = f"{em} {amount // 1000}к ⭐"
        else:
            label = f"{em} {amount} ⭐"
        builder.button(text=label, callback_data=f"donate_{amount}")

    builder.button(text="💋 Потом, Настя!", callback_data="donate_later")

    # Layout: Pay alone on first row, then pairs, then "later" alone
    # Total buttons: 1 pay + N other + 1 later
    row_sizes = [1]  # Pay button alone
    remaining = len(other_amounts)
    while remaining > 0:
        chunk = min(2, remaining)
        row_sizes.append(chunk)
        remaining -= chunk
    row_sizes.append(1)  # "Later" button alone

    builder.adjust(*row_sizes)
    return builder.as_markup()


async def _send_stars_invoice(chat_id: int, user_id: int, amount: int, bot):
    """Send a Telegram Stars invoice with ACTIVE Pay button.

    This creates a message with a native 'Pay ⭐' button that
    opens the payment dialog DIRECTLY — no extra steps!
    """
    try:
        want = _get_random_want()
        keyboard = _build_stars_invoice_keyboard(amount)

        await bot.send_invoice(
            chat_id=chat_id,
            title=f"Насте на {want}",
            description=(
                f"Настя хочет {want}! 💅\n"
                f"Кидай звёздочки — она заслужила! ⭐✨\n\n"
                f"Или выбери другую сумму ниже ↓"
            ),
            payload=f"nastya:{amount}:{user_id}",
            currency="XTR",
            provider_token="",
            prices=[LabeledPrice(label=f"Stars для Насти", amount=amount)],
            reply_markup=keyboard,
        )
        logger.info(f"Stars invoice sent: {amount} XTR to user {user_id}")
    except Exception as e:
        logger.error(f"Failed to send Stars invoice: {e}")
        # Fallback: send message with callback buttons
        try:
            await bot.send_message(
                chat_id,
                f"Настя хочет {want}! Жми /donates и кидай звёздочки! 💅⭐",
            )
        except Exception:
            pass


async def _ask_for_stars(chat_id: int, user_id: int, bot, want: str = ""):
    """Nastya asks for Stars — personality text + invoice with ACTIVE Pay button."""
    if not want:
        want = _get_random_want()

    # Step 1: Nastya's personality text
    phrase = random.choice(STARS_REQUESTS).format(want=want)
    try:
        await bot.send_message(chat_id, phrase)
    except Exception as e:
        logger.error(f"Failed to send stars ask: {e}")

    # Step 2: Invoice with ACTIVE Pay button
    recommended = random.choice([100, 300, 500])
    await _send_stars_invoice(chat_id, user_id, recommended, bot)


# ════════════════════════════════════════════════════════════
#  HANDLERS
# ════════════════════════════════════════════════════════════

@router.message(CommandStart())
async def cmd_start(message: Message, db=None, ai_router=None) -> None:
    """Welcome + deep links for donations."""
    user = message.from_user
    name = user.first_name or "незнакомец"

    if db:
        await db.get_or_create_user(
            user_id=user.id, username=user.username or "", first_name=name,
        )
        gender = _guess_gender_from_name(name)
        if gender != "unknown":
            await db.set_gender(user.id, gender)

    # Check for deep link parameter (donation from GitHub Pages)
    args = message.text.split(maxsplit=1)
    if len(args) > 1:
        param = args[1].strip()
        if param.startswith("donate_"):
            try:
                amount = int(param.replace("donate_", ""))
                if amount in DONATION_AMOUNTS or 100 <= amount <= 100000:
                    await _send_stars_invoice(message.chat.id, user.id, amount, message.bot)
                    return
            except ValueError:
                pass

    # Natural greeting
    want = _get_random_want()
    greetings = [
        f"О, привет, {name}! Я Настя. Будем болтать или как?",
        f"Привет! Я Настя. Ты мне сразу нравишься. Ну или нет, посмотрим!",
        f"Ой, {name}! Привет! Настя тут. Будем знакомы!",
        f"Ну привет, {name}. Я Настя. Не путай меня с кем-то, я одна такая!",
        f"Привеееет, {name}! 😊 Настя как раз о тебе думала... ну, или о {want}",
        f"О, {name}! А Настя только что хотела написать! 🦋✨",
    ]
    greeting_text = random.choice(greetings)
    greeting_text += f"\n\n⭐ /donates — кинуть Насте звёздочек!"

    await message.answer(greeting_text)

    # Also send Stars ask with ACTIVE invoice
    await _ask_for_stars(message.chat.id, user.id, message.bot, want)


@router.message(Command("donates"))
async def cmd_donates(message: Message, db=None, ai_router=None) -> None:
    """Show ACTIVE donation buttons with native Pay."""
    want = _get_random_want()
    await _ask_for_stars(message.chat.id, message.from_user.id, message.bot, want)


@router.message(Command("donate"))
async def cmd_donate(message: Message, db=None, ai_router=None) -> None:
    """Alias for /donates."""
    await cmd_donates(message, db, ai_router)


@router.message(F.text == "/clear")
async def cmd_clear(message: Message, db=None, ai_router=None) -> None:
    if db:
        await db.clear_history(message.from_user.id)
    await message.answer("Что? Ничего не помню! Начнём сначала!")


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
            await message.answer("Настя пока не умеет слушать голосовые... Напиши текстом! 🙏💕")
            return

        await _process_text_message(message, transcript, db, ai_router, is_voice=True)

    except Exception as e:
        logger.error(f"Voice handler error: {e}")
        await message.answer("Ой, у Насти ушки заболели... Напиши лучше текстом! 👂😅")


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

        # Compress image
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(image_bytes))
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            w, h = img.size
            if max(w, h) > 1024:
                ratio = 1024 / max(w, h)
                img = img.resize((int(w * ratio), int(h * ratio)), Image.Resampling.LANCZOS)
            compressed = io.BytesIO()
            img.save(compressed, format="JPEG", quality=80, optimize=True)
            image_bytes = compressed.getvalue()
        except Exception:
            pass  # Use original

        image_b64 = base64.b64encode(image_bytes).decode("utf-8")

        caption = message.caption or "Что скажешь про это фото?"
        prompt = f"Пользователь прислал фото и пишет: {caption}. Посмотри на фото и ответь в своём стиле — как Настя. Комментируй как живая девушка!"

        await db.get_or_create_user(
            user_id=user_id, username=message.from_user.username or "",
            first_name=message.from_user.first_name or "",
        )
        msg_count = await db.increment_messages(user_id)
        await db.add_message(user_id, "user", f"[Фото] {caption}")

        mood = await db.get_user_mood(user_id)
        gender = await db.get_gender(user_id)
        gender_ctx = f"Собеседник — {'мужчина' if gender == 'male' else 'женщина' if gender == 'female' else 'пол неизвестен'}." if gender != "unknown" else ""

        system_prompt = NASTYA_SYSTEM_PROMPT + f"\n\nТВОЁ ТЕКУЩЕЕ НАСТРОЕНИЕ: {mood}. Веди себя соответственно. {gender_ctx} Пользователь прислал фото — посмотри и прокомментируй как Настя, как живая девушка."

        history = await db.get_history(user_id, limit=50)

        result = await ai_router.chat_with_image(
            prompt=prompt, image_base64=image_b64,
            system_prompt=system_prompt, messages=history,
        )

        response_text = _clean_response(result.text or "Ой, Настя посмотрела, но зависла...")
        await db.add_message(user_id, "assistant", response_text)

        # Maybe add stars ask with ACTIVE Pay button
        await _maybe_ask_stars(response_text, user_id, msg_count, db, message)

        if len(response_text) > 4096:
            for i in range(0, len(response_text), 4096):
                await message.answer(response_text[i:i + 4096])
        else:
            await message.answer(response_text)

    except AllProvidersExhaustedError:
        await message.answer("Ой, у Насти голова разболелась... Попробуй ещё раз! 😵‍💫")
    except Exception as e:
        logger.error(f"Photo handler error: {e}")
        await message.answer("Настя не может разглядеть фото... Попробуй ещё раз!")


# ── Document handler ─────────────────────────────────────────

@router.message(F.document)
async def handle_document(message: Message, db=None, ai_router=None) -> None:
    if not db or not ai_router:
        return

    doc = message.document
    mime_type = doc.mime_type or "unknown"
    file_name = doc.file_name or "файл"

    if mime_type.startswith("image/"):
        # Process as image
        await handle_photo_like_doc(message, doc, file_name, db, ai_router)
        return

    # Non-image document
    user_id = message.from_user.id
    await db.get_or_create_user(user_id=user_id, username=message.from_user.username or "",
                                 first_name=message.from_user.first_name or "")
    await db.increment_messages(user_id)
    await db.add_message(user_id, "user", f"[Файл: {file_name}] {message.caption or ''}")
    await message.answer(f"Ой, файл {file_name}! Настя не умеет читать файлы... Но звучит важно! Расскажи что там?")


async def handle_photo_like_doc(message, doc, file_name, db, ai_router):
    """Handle image documents like photos."""
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        file = await message.bot.get_file(doc.file_id)
        buf = io.BytesIO()
        await message.bot.download_file(file.file_path, buf)
        image_bytes = buf.getvalue()
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")

        caption = message.caption or "Что скажешь про это фото?"
        prompt = f"Пользователь прислал фото (файл {file_name}) и пишет: {caption}. Посмотри и прокомментируй как Настя."

        user_id = message.from_user.id
        await db.get_or_create_user(user_id=user_id, username=message.from_user.username or "",
                                     first_name=message.from_user.first_name or "")
        msg_count = await db.increment_messages(user_id)
        await db.add_message(user_id, "user", f"[Фото: {file_name}] {caption}")

        mood = await db.get_user_mood(user_id)
        system_prompt = NASTYA_SYSTEM_PROMPT + f"\n\nТВОЁ ТЕКУЩЕЕ НАСТРОЕНИЕ: {mood}. Пользователь прислал фото — посмотри и прокомментируй."
        history = await db.get_history(user_id, limit=50)

        result = await ai_router.chat_with_image(prompt=prompt, image_base64=image_b64,
                                                   system_prompt=system_prompt, messages=history)
        response_text = _clean_response(result.text or "Ой, Настя посмотрела, но зависла...")
        await db.add_message(user_id, "assistant", response_text)

        if len(response_text) > 4096:
            for i in range(0, len(response_text), 4096):
                await message.answer(response_text[i:i + 4096])
        else:
            await message.answer(response_text)
    except Exception as e:
        logger.error(f"Image doc error: {e}")
        await message.answer("Настя не может разглядеть... Попробуй ещё раз!")


# ── Sticker handler ──────────────────────────────────────────

@router.message(F.sticker)
async def handle_sticker(message: Message, db=None, ai_router=None) -> None:
    responses = [
        "Ой, стикер! Настя тоже так может! 😂",
        "Это что за стикер? Настя не впечатлена... Или впечатлена! 😍",
        "А у тебя стикеры получше есть? Настя требовательная! 💅",
        "А у Насти стикеров нет... Кинешь? 🥺",
        "Настя так не умеет 😢 Зато она умеет болтать! 💅",
    ]
    await message.answer(random.choice(responses))
    if db:
        await db.get_or_create_user(user_id=message.from_user.id,
                                     username=message.from_user.username or "",
                                     first_name=message.from_user.first_name or "")
        await db.increment_messages(message.from_user.id)


# ── Video/animation handler ──────────────────────────────────

@router.message(F.video | F.animation)
async def handle_video(message: Message, db=None, ai_router=None) -> None:
    responses = [
        "О, видео! Настя смотрит... 🍿",
        "Классное видео! 😍",
        "Ой, а можно покороче? Насте лень смотреть 😴",
        "Оооо! 🤩 Что это?",
    ]
    await message.answer(random.choice(responses))


# ════════════════════════════════════════════════════════════
#  MAIN TEXT CHAT HANDLER
# ════════════════════════════════════════════════════════════

@router.message(F.text, ~F.text.startswith("/"))
async def handle_chat(message: Message, db=None, ai_router=None) -> None:
    """Natural conversation — no commands, just chat."""
    if not db or not ai_router:
        await message.answer("Ой, Настя не может сейчас говорить... Попробуй позже")
        return

    text = message.text
    text_lower = text.lower()

    # ── Quick reactions (no AI needed) ─────────────────────

    # Donation keywords → ACTIVE payment immediately!
    donate_keywords = [
        "донат", "звёзд", "звезд", "подар", "подари",
        "спонсор", "support", "donate", "stars", "звёздочки", "звездочки",
    ]
    if any(kw in text_lower for kw in donate_keywords):
        want = _get_random_want()
        response = f"Оооо, звёздочки Насте! 💅✨ Хочу {want}! Выбирай сколько! ⭐"
        await message.answer(response)
        await _send_stars_invoice(message.chat.id, message.from_user.id,
                                  random.choice([100, 300, 500]), message.bot)
        if db:
            await db.get_or_create_user(user_id=message.from_user.id,
                                         username=message.from_user.username or "",
                                         first_name=message.from_user.first_name or "")
            await db.increment_messages(message.from_user.id)
            await db.add_message(message.from_user.id, "user", text)
            await db.add_message(message.from_user.id, "assistant", response)
        return

    # "Ой ВСЁ!!!" reaction
    oy_vse_triggers = ["ой всё", "оё всё", "всё!", "надоело", "отстань", "хватит"]
    if any(t in text_lower for t in oy_vse_triggers):
        await message.answer("Ой ВСЁ!!! 😤💅")
        if db:
            await db.get_or_create_user(user_id=message.from_user.id,
                                         username=message.from_user.username or "",
                                         first_name=message.from_user.first_name or "")
            await db.increment_messages(message.from_user.id)
            await db.add_message(message.from_user.id, "user", text)
            await db.add_message(message.from_user.id, "assistant", "Ой ВСЁ!!! 😤💅")
        return

    # "Если Настя проснулась" — VERY RARE catchphrase
    if "настя проснулась" in text_lower or "если настя проснулась" in text_lower:
        await message.answer("Если Настя проснулась — все проснулись! 💅✨🔥")
        if db:
            await db.get_or_create_user(user_id=message.from_user.id,
                                         username=message.from_user.username or "",
                                         first_name=message.from_user.first_name or "")
            await db.increment_messages(message.from_user.id)
        return

    # Age question → dynamic answer
    age_triggers = ["сколько лет", "какой возраст", "сколько тебе", "твой возраст", "ты старая", "ты молодая"]
    if any(t in text_lower for t in age_triggers):
        age_answer = random.choice(AGE_DESCRIPTIONS)
        await message.answer(age_answer)
        if db:
            await db.get_or_create_user(user_id=message.from_user.id,
                                         username=message.from_user.username or "",
                                         first_name=message.from_user.first_name or "")
            await db.increment_messages(message.from_user.id)
            await db.add_message(message.from_user.id, "user", text)
            await db.add_message(message.from_user.id, "assistant", age_answer)
        return

    # Jealousy trigger
    jealousy_triggers = ["siri", "алиса", "chatgpt", "чат gpt", "gpt", "другой бот", "другие боты"]
    if any(t in text_lower for t in jealousy_triggers):
        if random.random() < 0.5:
            jealousy = random.choice(JEALOUSY_PHRASES)
            await message.answer(jealousy)
            if db:
                await db.get_or_create_user(user_id=message.from_user.id,
                                             username=message.from_user.username or "",
                                             first_name=message.from_user.first_name or "")
                await db.increment_messages(message.from_user.id)
                await db.add_message(message.from_user.id, "user", text)
                await db.add_message(message.from_user.id, "assistant", jealousy)
            return

    # Compliment trap — sometimes Настя fishes for compliments
    if random.random() < 0.03:  # 3% chance
        trap = random.choice(COMPLIMENT_TRAPS)
        # Process normally but inject the trap
        await _process_text_message(message, text, db, ai_router, extra_suffix=f"\n\n{trap}")
        return

    # "Я скоро" trigger
    if any(t in text_lower for t in ["скоро буду", "скоро приду", "подожди немного", "я скоро"]):
        if random.random() < 0.3:
            skoro = random.choice(YA_SKORO_PHRASES)
            await message.answer(skoro)
            if db:
                await db.get_or_create_user(user_id=message.from_user.id,
                                             username=message.from_user.username or "",
                                             first_name=message.from_user.first_name or "")
                await db.increment_messages(message.from_user.id)
            return

    # Silent treatment — VERY rare
    if random.random() < 0.01:  # 1%
        silent = random.choice(SILENT_TREATMENT)
        await message.answer(silent)
        if db:
            await db.get_or_create_user(user_id=message.from_user.id,
                                         username=message.from_user.username or "",
                                         first_name=message.from_user.first_name or "")
            await db.increment_messages(message.from_user.id)
        return

    # ── Normal AI chat ─────────────────────────────────────
    await _process_text_message(message, text, db, ai_router)


async def _process_text_message(message: Message, text: str, db, ai_router,
                                 is_voice: bool = False, extra_suffix: str = "") -> None:
    """Process text message with AI."""
    user_id = message.from_user.id

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
        gender_ctx = "Собеседник — мужчина. Обращайся соответственно. Иногда ревнуй к другим ботам."
    elif gender == "female":
        gender_ctx = "Собеседник — женщина. Обращайся как к подруге."

    voice_ctx = " Пользователь прислал голосовое, ты его 'услышала'." if is_voice else ""

    # Inject random girl logic hint
    girl_logic = ""
    if random.random() < 0.05:  # 5%
        girl_logic = f"\n\n{random.choice(GIRL_LOGIC_HINTS)}"

    # Inject random pet wish
    pet_wish = ""
    if random.random() < 0.08:  # 8%
        pet_wishes = [
            "Кстати, Настя хочет завести попугайчика! 🦜",
            "Мечтаю о щеночке... мопсик бы подошёл! 🐶",
            "Хочу хомячка! Буду называть его Пирожок 🐹",
            "Насте нужна птичка — канарейка! 🐦✨",
            "Мечтаю о котике... пушистом... мурчащем... 🐱💕",
        ]
        pet_wish = f"\n\nСЛУЧАЙНАЯ МЕЧТА: {random.choice(pet_wishes)}"

    system_prompt = (
        NASTYA_SYSTEM_PROMPT +
        f"\n\nТВОЁ ТЕКУЩЕЕ НАСТРОЕНИЕ: {mood}. Веди себя соответственно." +
        f"\n{gender_ctx}" +
        voice_ctx +
        girl_logic +
        pet_wish
    )

    # Save user message
    prefix = "[Голосовое] " if is_voice else ""
    await db.add_message(user_id, "user", f"{prefix}{text}")

    # Get history — 30 days context
    history = await db.get_history(user_id, limit=50)

    try:
        result = await ai_router.chat(
            prompt=text, system_prompt=system_prompt, messages=history,
        )

        response_text = _clean_response(result.text or "Ммм... Настя задумалась. Повтори?")

        # Add extra suffix if any (e.g., compliment trap)
        if extra_suffix:
            response_text += extra_suffix

        # Save assistant message
        await db.add_message(user_id, "assistant", response_text)

        # Maybe ask for stars with ACTIVE Pay button
        should_stars, stars_want = await _maybe_ask_stars_check(
            user_id, msg_count, db, message
        )

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

        # Send Stars ask after the response if triggered
        if should_stars and stars_want:
            await _ask_for_stars(message.chat.id, user_id, message.bot, stars_want)

    except AllProvidersExhaustedError:
        logger.error(f"All providers exhausted for user={user_id}")
        await message.answer("Ой, у Насти голова разболелась... Попробуй ещё раз! 😵‍💫")
    except Exception as e:
        logger.error(f"Chat error for user={user_id}: {e}")
        await message.answer("Ой, что-то пошло не так... Настя запуталась. Попробуй ещё!")


async def _maybe_ask_stars_check(user_id: int, msg_count: int, db, message: Message):
    """Check if should ask for stars. Returns (should_ask, want_text)."""
    tracker = _stars_tracker.get(user_id, {"count": 0, "last_ask": 0})
    tracker["count"] = msg_count

    # Ask after 3 messages, at least 10 min apart, 25% chance
    if msg_count >= 3 and time.time() - tracker["last_ask"] > 600 and random.random() < 0.25:
        tracker["last_ask"] = time.time()
        _stars_tracker[user_id] = tracker
        want = _get_random_want()
        return True, want

    _stars_tracker[user_id] = tracker
    return False, ""


async def _maybe_ask_stars(response_text: str, user_id: int, msg_count: int,
                            db, message: Message) -> str:
    """Legacy compatibility — just returns response_text."""
    should_stars, want = await _maybe_ask_stars_check(user_id, msg_count, db, message)
    if should_stars:
        await _ask_for_stars(message.chat.id, user_id, message.bot, want)
    return response_text


def _clean_response(text: str) -> str:
    """Clean AI response from common artifacts."""
    if not text:
        return "Ммм... Настя задумалась... 🤔"
    for prefix in [
        "Настя:", "Nastya:", "Настя!:", "НАСТЯ:", "Настя says:",
        "Assistant:", "Настя отвечает:", "Ответ Насти:", "Ассистент:",
    ]:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
    if text.startswith("'") and text.endswith("'"):
        text = text[1:-1]
    text = text.strip("*").strip()
    return text.strip()


# ════════════════════════════════════════════════════════════
#  DONATION CALLBACK HANDLERS
# ════════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("donate_"))
async def callback_donate(callback: CallbackQuery, db=None, ai_router=None) -> None:
    """Handle donation button clicks — send invoice with ACTIVE Pay button."""
    data = callback.data.replace("donate_", "", 1)

    if data == "later":
        await callback.answer("Настя подождёт... 😢💔", show_alert=False)
        try:
            await callback.bot.send_message(
                callback.from_user.id,
                "Ну ладно... Настя подождёт... 😢💔\n\nНо /donates всегда работает! 💅",
            )
        except Exception:
            pass
        return

    if data == "more":
        # Show all amounts as invoices
        await callback.answer()
        await _send_stars_invoice(
            callback.from_user.id, callback.from_user.id,
            random.choice([300, 500]), callback.bot,
        )
        return

    try:
        amount = int(data)
    except ValueError:
        await callback.answer("Ошибка!", show_alert=True)
        return

    # Send invoice with ACTIVE Pay button for the selected amount!
    await callback.answer()
    await _send_stars_invoice(callback.from_user.id, callback.from_user.id, amount, callback.bot)


# ════════════════════════════════════════════════════════════
#  PROACTIVE MESSAGES
# ════════════════════════════════════════════════════════════

async def check_and_send_proactive(bot, db, ai_router) -> None:
    """Send proactive messages to recently active users."""
    now = time.time()
    sent = 0
    for user_id, pro in list(_proactive_tracker.items()):
        if sent >= 5:
            break
        last = pro.get("last_proactive", 0)
        if now - last < PROACTIVE_COOLDOWN:
            continue
        try:
            msg = random.choice(PROACTIVE_MESSAGES)
            chat_id = pro.get("chat_id", user_id)
            await bot.send_message(chat_id, msg)
            pro["last_proactive"] = now
            logger.info(f"Sent proactive to user {user_id}")
            sent += 1
        except Exception as e:
            logger.error(f"Proactive error for user {user_id}: {e}")
            # Remove broken user from tracker
            _proactive_tracker.pop(user_id, None)
