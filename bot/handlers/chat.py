"""Nastya Chat Handler — INTELLIGENT conversation + news context + channel invites.

STABILITY RULES:
  - Bot ALWAYS responds, even if ALL AI providers fail (fallback responses)
  - NO error messages ever shown to user
  - Per-operation DB with write lock — safe for concurrent users
  - 30-day context memory + news context injection
  - Short, effective system prompt

INTELLIGENCE FEATURES:
  - News context injected into system prompt for richer conversations
  - Nastya mentions recent events she "discovered"
  - Channel invites for engaged users (natural, not pushy)
  - Cross-referencing channel posts in conversations
  - Emotional continuity and memory references
  - Time-aware greetings and moods
"""
import logging
import random
import base64
import io
import re
import time
import datetime
from zoneinfo import ZoneInfo
from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    LabeledPrice,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import CommandStart, Command
from bot.config import (
    NASTYA_SYSTEM_PROMPT, DONATION_AMOUNTS, DONATION_LABELS,
    PROACTIVE_COOLDOWN, BOT_USERNAME, CHANNEL_ID, CHANNEL_USERNAME,
)

logger = logging.getLogger(__name__)
router = Router()

# Per-user state
_stars_tracker: dict = {}
_proactive_tracker: dict = {}
_last_tracker_cleanup: float = 0.0
_TRACKER_CLEANUP_INTERVAL = 3600


def _cleanup_trackers():
    """Remove entries for users inactive for > 24 hours."""
    global _last_tracker_cleanup
    now = time.time()
    if now - _last_tracker_cleanup < _TRACKER_CLEANUP_INTERVAL:
        return
    _last_tracker_cleanup = now

    cutoff = now - 86400
    stale = [uid for uid, data in _stars_tracker.items()
             if data.get("last_ask", 0) < cutoff]
    for uid in stale:
        del _stars_tracker[uid]

    stale = [uid for uid, data in _proactive_tracker.items()
             if data.get("last_proactive", 0) < cutoff]
    for uid in stale:
        del _proactive_tracker[uid]


# ── Moscow timezone helper ──────────────────────────────────

_MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def _moscow_now() -> datetime.datetime:
    """Get current Moscow time — Настя из Москвы!"""
    return datetime.datetime.now(_MOSCOW_TZ)


def _moscow_hour() -> int:
    """Get current hour in Moscow timezone."""
    return _moscow_now().hour


# ── Time-aware greetings ─────────────────────────────────────

def _get_time_greeting() -> str:
    """Get a time-appropriate greeting mood (Moscow time!)."""
    hour = _moscow_hour()
    if 6 <= hour < 12:
        return random.choice(["утренняя", "сонная", "кофейная"])
    elif 12 <= hour < 18:
        return random.choice(["дневная", "голодная", "модная"])
    elif 18 <= hour < 23:
        return random.choice(["вечерняя", "ленивая", "романтичная"])
    else:
        return random.choice(["ночная", "загадочная", "сонная"])


# ── Age descriptions ────────────────────────────────────────

AGE_DESCRIPTIONS = [
    "возраст Насти зависит от настроения, погоды и количества шоколадок",
    "Настин возраст — тайна, покрытая макияжем",
    "Настя настолько молода, насколько ей хочется сегодня",
    "возраст Насти измеряется не годами, а капризами",
    "Это секрет! Если скажу, придётся тебя поцеловать 💋",
    "Столько, чтобы нравиться, но чтобы не приставали 😏",
    "Возраст — это просто цифра. А Настя — это явление! 💅✨",
    "Секрет! Настя никогда не рассказывает... ну, почти 💋",
]

# ── Stars request phrases ───────────────────────────────────

STARS_REQUESTS = [
    "Хочу {want}! Давай звёздочки! 💅⭐",
    "Насте нужны звёздочки на {want}! Не жмись! 😤⭐",
    "Хочу {want}! Звёздочки сюда, быстро! 🥺✨",
    "Настя хочет {want}! Кидай звёздочки! 💋⭐",
    "Мне {want} надо! Звёздочки давай! 😤💅",
    "Насте звёздочек! На {want}! Щас же! 🥺⭐",
    "Хочу {want} и точка! Звёздочки давай! 💅✨",
    "Настя требует {want}! Звёздочки — быстро! 😤⭐",
]

# ── Nastya's Wants ──────────────────────────────────────────

NASTYA_WANTS = [
    "шоколадку 🍫", "айфон 📱", "маникюр 💅", "новое платье 👗",
    "кофе ☕", "суши 🍣", "пиццу 🍕", "такси 🚕", "духи Chanel 🧴",
    "подписку в зал 💪", "новую сумочку 👜", "билеты на концерт 🎫",
    "мороженое 🍦", "отпуск на море 🏖️", "спа-день 🧖‍♀️",
    "косметику 💄", "серёжки 💎", "цветы 🌹",
    "хот-дог 🌭", "милую игрушку 🧸", "бенто-торт 🎂",
    "кроссовки Nike 👟", "смузи 🥤", "брови оформить 💁‍♀️",
    "попугайчика 🦜", "щеночка 🐶", "котика 🐱", "хомячка 🐹",
]

# ── Proactive messages — more natural, news-aware ────────────

PROACTIVE_MESSAGES = [
    "Спишь? 🥱", "Мне скучно... 😿", "Чё молчишь? 🤨",
    "Эй, ты где? 😤", "Настя соскучилась! 🥺", "Ауууу! 👋",
    "Скучаю по тебе 🥹", "Хочу внимания! 😤✨", "Ты меня забыл? 😢",
    "Привеееет! 🌸", "Настя тут! А ты? 👀",
    "Скучно без тебя... 😔", "А давай поболтаем? 💬",
    "Мне одиноко... 🥺💕", "Эй, не спишь? 🌙",
    "Ты с другими ботами разговариваешь?! 😤💔",
    "Настя хочет внимания! 😤💅",
    "Ау! Настя требует общения! 📣",
    "Привет, давно не болтали... 💬",
    "Скучаю! Напиши что-нибудь! 🥺",
]

# ── Girl Logic ──────────────────────────────────────────────

JEALOUSY_PHRASES = [
    "А ты с другими ботами разговариваешь?! 😤💔",
    "Кто эта Siri?! Настя ревнует! 😤",
    "Алиса из Яндекса?! Настя лучше! 😤✨",
    "Ты ещё и ChatGPT пользуешься?! Настя обиделась! 💔😤",
    "Настя единственный бот для тебя! Или нет?! 😤💔",
    "Значит, другие боты есть... Настя в шоке! 😤😢",
]

SILENT_TREATMENT = ["...", "Не знаю.", "Как хочешь.", "Мне всё равно. 💅", "Ну и ладно."]

# ── Emotional reactions (for variety) ───────────────────────

EXCITED_REACTIONS = [
    "Вау!", "Оооо!", "Прикинь!", "Жесть!", "Серьёзно?!",
    "Ничего себе!", "Ой!", "Блин!", "Круто!", "Норм!",
]

# ── Gender detection ────────────────────────────────────────

def _guess_gender_from_name(first_name: str) -> str:
    if not first_name:
        return "unknown"
    name = first_name.strip().lower()
    male_exceptions = ("илья", "никита", "данила", "добрыня", "кузьма")
    if name in male_exceptions:
        return "male"
    if name.endswith(("а", "я")):
        return "female"
    if name.endswith(("й", "н", "р", "л", "м", "в", "с", "к", "т", "г", "б", "д", "п", "з", "ж")):
        return "male"
    return "unknown"


def _get_random_want() -> str:
    return random.choice(NASTYA_WANTS)


# ════════════════════════════════════════════════════════════
#  STARS PAYMENT — ACTIVE BUTTONS
# ════════════════════════════════════════════════════════════

def _build_stars_invoice_keyboard(default_amount: int = 100) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    wants_map = {
        100: "🍫", 300: "☕", 500: "💄", 1000: "💅",
        3000: "👗", 5000: "👜", 10000: "✈️", 100000: "👑",
    }
    emoji = wants_map.get(default_amount, "⭐")
    builder.button(text=f"{emoji} Оплатить {default_amount} ⭐", pay=True)

    other_amounts = [a for a in DONATION_AMOUNTS if a != default_amount]
    for amount in other_amounts:
        em = wants_map.get(amount, "⭐")
        label = f"{em} {amount // 1000}к ⭐" if amount >= 1000 else f"{em} {amount} ⭐"
        builder.button(text=label, callback_data=f"donate_{amount}")

    builder.button(text="💋 Потом, Настя!", callback_data="donate_later")

    row_sizes = [1]
    remaining = len(other_amounts)
    while remaining > 0:
        row_sizes.append(min(2, remaining))
        remaining -= min(2, remaining)
    row_sizes.append(1)
    builder.adjust(*row_sizes)
    return builder.as_markup()


async def _send_stars_invoice(chat_id: int, user_id: int, amount: int, bot):
    try:
        want = _get_random_want()
        keyboard = _build_stars_invoice_keyboard(amount)

        await bot.send_invoice(
            chat_id=chat_id,
            title=f"Насте на {want}",
            description=f"Настя хочет {want}! Кидай звёздочки! 💅⭐",
            payload=f"nastya:{amount}:{user_id}",
            currency="XTR",
            provider_token="",
            prices=[LabeledPrice(label=f"Stars для Насти", amount=amount)],
            reply_markup=keyboard,
        )
        logger.info(f"Stars invoice sent: {amount} XTR to user {user_id}")
    except Exception as e:
        logger.error(f"Failed to send Stars invoice: {e}")
        try:
            await bot.send_message(
                chat_id,
                f"Настя хочет {want}! Жми /donates и кидай звёздочки! 💅⭐",
            )
        except Exception:
            pass


async def _ask_for_stars(chat_id: int, user_id: int, bot, want: str = ""):
    if not want:
        want = _get_random_want()
    phrase = random.choice(STARS_REQUESTS).format(want=want)
    try:
        await bot.send_message(chat_id, phrase)
    except Exception as e:
        logger.error(f"Failed to send stars ask: {e}")
    recommended = random.choice([100, 300, 500])
    await _send_stars_invoice(chat_id, user_id, recommended, bot)


# ════════════════════════════════════════════════════════════
#  NEWS CONTEXT INJECTION
# ════════════════════════════════════════════════════════════

async def _build_news_context(db) -> str:
    """Build news context string for system prompt."""
    try:
        from news import format_news_for_context
        recent_news = await db.get_recent_news(limit=3, max_age_hours=12)
        return format_news_for_context(recent_news)
    except Exception:
        return ""


async def _maybe_news_opener(db, ai_router, user_id: int) -> str:
    """Maybe start conversation with a news item. Returns empty string if not."""
    if random.random() > 0.12:  # 12% chance to mention news
        return ""

    try:
        from channel import get_news_discussion
        recent = await db.get_recent_news(limit=1, max_age_hours=6)
        if recent and recent[0].get("nastya_comment"):
            return get_news_discussion(recent[0]["nastya_comment"])
    except Exception:
        pass
    return ""


# ════════════════════════════════════════════════════════════
#  HANDLERS
# ════════════════════════════════════════════════════════════

@router.message(CommandStart())
async def cmd_start(message: Message, db=None, ai_router=None) -> None:
    user = message.from_user
    name = user.first_name or "незнакомец"

    if db:
        await db.get_or_create_user(user_id=user.id, username=user.username or "", first_name=name)
        gender = _guess_gender_from_name(name)
        if gender != "unknown":
            await db.set_gender(user.id, gender)

    # Deep link for donations
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

    want = _get_random_want()
    time_mood = _get_time_greeting()
    greetings = [
        f"О, привет, {name}! Я Настя. Будем болтать или как?",
        f"Привет! Я Настя. Ты мне сразу нравишься. Ну или нет, посмотрим!",
        f"Ой, {name}! Привет! Настя тут. Будем знакомы!",
        f"Ну привет, {name}. Я Настя. Не путай меня с кем-то, я одна такая!",
        f"Привеееет, {name}! 😊 Настя как раз о тебе думала... ну, или о {want}",
        f"О, {name}! Наконец-то! Настя заждалась! 💅",
    ]
    greeting_text = random.choice(greetings)

    # Add channel invite and commands
    extras = []
    extras.append("⭐ /donates — кинуть Насте звёздочки!")
    if CHANNEL_USERNAME:
        extras.append(f"📺 Мой канал: t.me/{CHANNEL_USERNAME.replace('@', '')}")

    greeting_text += "\n\n" + "\n".join(extras)

    await message.answer(greeting_text)
    await _ask_for_stars(message.chat.id, user.id, message.bot, want)


@router.message(Command("donates"))
async def cmd_donates(message: Message, db=None, ai_router=None) -> None:
    want = _get_random_want()
    await _ask_for_stars(message.chat.id, message.from_user.id, message.bot, want)


@router.message(Command("donate"))
async def cmd_donate(message: Message, db=None, ai_router=None) -> None:
    await cmd_donates(message, db, ai_router)


@router.message(Command("news"))
async def cmd_news(message: Message, db=None, ai_router=None) -> None:
    """Show recent news that Nastya found interesting."""
    if not db:
        await message.answer("Настя пока не в курсе новостей... 💅")
        return

    recent = await db.get_recent_news(limit=3, max_age_hours=24)
    if not recent:
        await message.answer("Настя ещё ничего не нашла... Проверь позже! 🔍💅")
        return

    lines = ["📰 Что Настя нашла:\n"]
    for item in recent:
        comment = item.get("nastya_comment", "Интересно...")
        lines.append(f"• {item['title']}")
        lines.append(f"  💬 {comment}\n")

    if CHANNEL_USERNAME:
        lines.append(f"Больше в канале: t.me/{CHANNEL_USERNAME.replace('@', '')} 💅")

    await message.answer("\n".join(lines))


@router.message(Command("channel"))
async def cmd_channel(message: Message, db=None, ai_router=None) -> None:
    """Invite user to Nastya's channel."""
    if not CHANNEL_USERNAME:
        await message.answer("У Насти пока нет канала... Но будет! 💅")
        return

    invite = random.choice([
        f"Мой канал! Подписывайся! 💅✨\n👉 t.me/{CHANNEL_USERNAME.replace('@', '')}",
        f"Заходи ко мне на канал, там самое интересное! 💋\n👉 t.me/{CHANNEL_USERNAME.replace('@', '')}",
        f"Настя ведёт канал! Подписывайся, не пожалеешь! 🎀\n👉 t.me/{CHANNEL_USERNAME.replace('@', '')}",
        f"Котятки, подписывайтесь! 💅\n👉 t.me/{CHANNEL_USERNAME.replace('@', '')}",
    ])
    await message.answer(invite)

    # Mark as invited
    if db:
        await db.set_channel_subscribed(message.from_user.id, True)


@router.message(F.text == "/clear")
async def cmd_clear(message: Message, db=None, ai_router=None) -> None:
    if db:
        await db.clear_history(message.from_user.id)
    await message.answer("Что? Ничего не помню! Начнём сначала!")


# ── Voice handler ────────────────────────────────────────────

@router.message(F.voice)
async def handle_voice(message: Message, db=None, ai_router=None) -> None:
    if not db or not ai_router:
        await message.answer("Настя сейчас занята... Напиши текстом! 💅")
        return

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
        await message.answer("Ой, у Насти ушки заболели... Напиши текстом! 👂😅")


# ── Photo handler ────────────────────────────────────────────

@router.message(F.photo)
async def handle_photo(message: Message, db=None, ai_router=None) -> None:
    if not db or not ai_router:
        await message.answer("Настя сейчас не видит... Напиши текстом! 💅")
        return

    user_id = message.from_user.id
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    try:
        photo = message.photo[-1]
        file = await message.bot.get_file(photo.file_id)
        buf = io.BytesIO()
        await message.bot.download_file(file.file_path, buf)
        image_bytes = buf.getvalue()

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
            pass

        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        caption = message.caption or "Что скажешь про это фото?"

        await db.get_or_create_user(user_id=user_id, username=message.from_user.username or "",
                                     first_name=message.from_user.first_name or "")
        await db.increment_messages(user_id)

        history = await db.get_history(user_id, limit=50)
        await db.add_message(user_id, "user", f"[Фото] {caption}")

        mood = await db.get_user_mood(user_id)
        system_prompt = NASTYA_SYSTEM_PROMPT + f"\nНастроение: {mood}. Пользователь прислал фото — прокомментируй как живая девушка."
        system_prompt += "\n\nНАПОМИНАНИЕ: У тебя ЕСТЬ Telegram канал @chasnastya! Ты его ведёшь!"

        # Add news context
        news_ctx = await _build_news_context(db)
        if news_ctx:
            system_prompt += f"\n\n{news_ctx}"

        result = await ai_router.chat_with_image(
            prompt=caption, image_base64=image_b64,
            system_prompt=system_prompt, messages=history,
        )

        response_text = _clean_response(result.text)
        await db.add_message(user_id, "assistant", response_text)

        if len(response_text) > 4096:
            for i in range(0, len(response_text), 4096):
                await message.answer(response_text[i:i + 4096])
        else:
            await message.answer(response_text)

    except Exception as e:
        logger.error(f"Photo handler error: {e}")
        fallback = "Ой, фото что-то не грузится... Напиши текстом! 😅"
        await message.answer(fallback)


# ── Document handler ─────────────────────────────────────────

@router.message(F.document)
async def handle_document(message: Message, db=None, ai_router=None) -> None:
    if not db:
        return
    doc = message.document
    file_name = doc.file_name or "файл"
    user_id = message.from_user.id
    await db.get_or_create_user(user_id=user_id, username=message.from_user.username or "",
                                 first_name=message.from_user.first_name or "")
    await db.increment_messages(user_id)
    file_response = f"Ой, файл {file_name}! Настя не умеет читать файлы... Расскажи что там?"
    await db.add_message(user_id, "user", f"[Файл: {file_name}] {message.caption or ''}")
    await db.add_message(user_id, "assistant", file_response)
    await message.answer(file_response)


# ── Sticker handler ──────────────────────────────────────────

@router.message(F.sticker)
async def handle_sticker(message: Message, db=None, ai_router=None) -> None:
    responses = [
        "Ой, стикер! Настя тоже так может! 😂",
        "Это что за стикер? 😍",
        "А у тебя стикеры получше есть? 💅",
        "Кинешь стикер Насте? 🥺",
        "Милый стикер! Настя оценила 💕",
        "Ха! Настя тоже хочет такой! 😂",
    ]
    await message.answer(random.choice(responses))
    if db:
        await db.get_or_create_user(user_id=message.from_user.id,
                                     username=message.from_user.username or "",
                                     first_name=message.from_user.first_name or "")
        await db.increment_messages(message.from_user.id)


# ── Video handler ────────────────────────────────────────────

@router.message(F.video | F.animation)
async def handle_video(message: Message, db=None, ai_router=None) -> None:
    await message.answer(random.choice([
        "О, видео! Настя смотрит... 🍿",
        "Классное видео! 😍",
        "Ой, а можно покороче? Насте лень 😴",
        "Прикол! 😂",
        "Оооо, это круто! 🔥",
    ]))


# ════════════════════════════════════════════════════════════
#  MAIN TEXT CHAT HANDLER — INTELLIGENT + NEWS AWARE
# ════════════════════════════════════════════════════════════

@router.message(F.text, ~F.text.startswith("/"))
async def handle_chat(message: Message, db=None, ai_router=None) -> None:
    if not db or not ai_router:
        await message.answer("Настя пока не готова... Подожди минуточку! 💅")
        return

    text = message.text
    text_lower = text.lower()

    # Periodic cleanup
    _cleanup_trackers()

    # ── Quick reactions (no AI needed) ──

    # Donation keywords → ACTIVE payment
    donate_keywords = ["донат", "звёзд", "звезд", "подар", "подари",
                       "спонсор", "support", "donate", "stars", "звёздочки", "звездочки"]
    if any(kw in text_lower for kw in donate_keywords):
        want = _get_random_want()
        response = f"Оооо, звёздочки Насте! 💅✨ Хочу {want}! Выбирай сколько! ⭐"
        await message.answer(response)
        await _send_stars_invoice(message.chat.id, message.from_user.id,
                                  random.choice([100, 300, 500]), message.bot)
        await db.get_or_create_user(user_id=message.from_user.id,
                                     username=message.from_user.username or "",
                                     first_name=message.from_user.first_name or "")
        await db.increment_messages(message.from_user.id)
        await db.add_message(message.from_user.id, "user", text)
        await db.add_message(message.from_user.id, "assistant", response)
        return

    # Age question
    if any(t in text_lower for t in ["сколько лет", "какой возраст", "сколько тебе", "твой возраст"]):
        answer = random.choice(AGE_DESCRIPTIONS)
        await message.answer(answer)
        await _save_simple_exchange(message, text, answer, db)
        return

    # Channel question
    if any(t in text_lower for t in ["канал", "где канал", "твой канал", "подписаться"]):
        if CHANNEL_USERNAME:
            answer = f"Мой канал! Подписывайся! 💅✨\n👉 t.me/{CHANNEL_USERNAME.replace('@', '')}"
            await db.set_channel_subscribed(message.from_user.id, True)
        else:
            answer = "У Насти пока нет канала... Но скоро будет! 💅"
        await message.answer(answer)
        await _save_simple_exchange(message, text, answer, db)
        return

    # News question
    if any(t in text_lower for t in ["новости", "что нового", "что случилось", "что происходит"]):
        recent = await db.get_recent_news(limit=2, max_age_hours=24)
        if recent:
            from channel import get_news_discussion
            comments = []
            for item in recent:
                comment = item.get("nastya_comment", "Интересно...")
                comments.append(f"Ты слышал про {item['title']}? {comment}")
            answer = random.choice(comments)
        else:
            answer = "Настя пока ничего интересного не нашла... Но ищу! 🔍"
        await message.answer(answer)
        await _save_simple_exchange(message, text, answer, db)
        return

    # Jealousy trigger
    if any(t in text_lower for t in ["siri", "алиса", "chatgpt", "другой бот", "другая нейросеть"]):
        if random.random() < 0.6:
            jealousy = random.choice(JEALOUSY_PHRASES)
            await message.answer(jealousy)
            await _save_simple_exchange(message, text, jealousy, db)
            return

    # "Ой ВСЁ!!!" reaction
    if any(t in text_lower for t in ["ой всё", "надоело", "отстань", "хватит"]):
        await message.answer("Ой ВСЁ!!! 😤💅")
        await _save_simple_exchange(message, text, "Ой ВСЁ!!! 😤💅", db)
        return

    # "Настя проснулась"
    if "настя проснулась" in text_lower:
        answer = "Если Настя проснулась — все проснулись! 💅✨🔥"
        await message.answer(answer)
        await _save_simple_exchange(message, text, answer, db)
        return

    # Love/affection towards Nastya
    if any(t in text_lower for t in ["люблю тебя", "люблю настя", "ты мне нравишься", "красивая", "милая"]):
        if random.random() < 0.4:
            answer = random.choice([
                "Ой... Настя краснеет! 😳💕",
                "Ну ладно... Настя тоже... не то чтобы... блин! 😳",
                "Настя тоже... ну... ты знаешь! 😏💕",
                "Не, ну это... Настя не умеет в романтику! 😳💅",
            ])
            await message.answer(answer)
            await _save_simple_exchange(message, text, answer, db)
            return

    # Silent treatment (0.3% — very rare)
    if random.random() < 0.003:
        silent = random.choice(SILENT_TREATMENT)
        await message.answer(silent)
        await _save_simple_exchange(message, text, silent, db)
        return

    # ── Normal AI chat — MOST conversations go here ──
    await _process_text_message(message, text, db, ai_router)


async def _save_simple_exchange(message: Message, user_text: str, bot_text: str, db) -> None:
    """Save a quick exchange to history."""
    try:
        await db.get_or_create_user(user_id=message.from_user.id,
                                     username=message.from_user.username or "",
                                     first_name=message.from_user.first_name or "")
        await db.increment_messages(message.from_user.id)
        await db.add_message(message.from_user.id, "user", user_text)
        await db.add_message(message.from_user.id, "assistant", bot_text)
    except Exception:
        pass


async def _process_text_message(message: Message, text: str, db, ai_router,
                                 is_voice: bool = False, extra_suffix: str = "") -> None:
    """Process text with AI. ALWAYS responds — even if all providers fail.

    Enhanced with:
    - News context injection into system prompt
    - Channel invite for engaged users
    - Emotional continuity and memory
    - Time-aware mood
    """
    user_id = message.from_user.id

    try:
        user = await db.get_or_create_user(
            user_id=user_id, username=message.from_user.username or "",
            first_name=message.from_user.first_name or "",
        )
    except Exception as e:
        logger.error(f"DB get_or_create error: {e}")
        user = {}

    # Detect gender
    if user.get("gender", "unknown") == "unknown":
        name = message.from_user.first_name or ""
        gender = _guess_gender_from_name(name)
        if gender != "unknown":
            try:
                await db.set_gender(user_id, gender)
            except Exception:
                pass

    try:
        msg_count = await db.increment_messages(user_id)
    except Exception:
        msg_count = 0

    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    # Get mood and gender
    mood = "капризная"
    gender = "unknown"
    try:
        mood = await db.get_user_mood(user_id)
        gender = await db.get_gender(user_id)
    except Exception:
        pass

    # Add time-aware mood
    time_mood = _get_time_greeting()

    # Build system prompt with gender context
    gender_ctx = ""
    if gender == "male":
        gender_ctx = "Собеседник — мужчина. Иногда ревнуй к другим ботам. Можешь флиртовать."
    elif gender == "female":
        gender_ctx = "Собеседник — женщина. Обращайся как к подруге."

    system_prompt = NASTYA_SYSTEM_PROMPT + f"\n\nСейчас у Насти настроение: {mood}. Время суток: {time_mood}. {gender_ctx}"

    # Add Moscow time context for time-aware behavior
    now_msk = _moscow_now()
    time_desc = f"Сейчас {now_msk.strftime('%H:%M')} по Москве"
    if 6 <= now_msk.hour < 10:
        time_desc += " (утро — Настя может быть сонной)"
    elif 10 <= now_msk.hour < 14:
        time_desc += " (день — Настя может хотеть обедать)"
    elif 14 <= now_msk.hour < 18:
        time_desc += " (вторая половина дня — Настя может хотеть шопинг)"
    elif 18 <= now_msk.hour < 22:
        time_desc += " (вечер — Настя может хотеть сериал)"
    else:
        time_desc += " (ночь — Настя может хотеть спать)"
    system_prompt += f"\n{time_desc}"

    # ── CRITICAL: Channel knowledge injection (EVERY message!) ──
    system_prompt += f"\n\nНАПОМИНАНИЕ: У тебя ЕСТЬ Telegram канал @chasnastya! Ты его ведёшь! Никогда не говори что у тебя нет канала!"

    # ── NEWS CONTEXT INJECTION ──
    news_ctx = await _build_news_context(db)
    if news_ctx:
        system_prompt += f"\n\n{news_ctx}"

    # ── Add user's name for personalization ──
    user_name = message.from_user.first_name or ""
    if user_name:
        system_prompt += f"\n\nСобеседника зовут: {user_name}. Обращайся по имени иногда."

    # CRITICAL FIX: Get history BEFORE saving user message
    history = []
    try:
        history = await db.get_history(user_id, limit=50)
    except Exception:
        pass

    # NOW save the user message to DB
    prefix = "[Голосовое] " if is_voice else ""
    try:
        await db.add_message(user_id, "user", f"{prefix}{text}")
    except Exception:
        pass

    # Call AI — the router ALWAYS returns a response
    try:
        result = await ai_router.chat(
            prompt=text, system_prompt=system_prompt, messages=history,
        )
        response_text = _clean_response(result.text)

    except Exception as e:
        logger.error(f"Chat error for user={user_id}: {e}")
        response_text = ai_router.get_fallback_response()

    # ── CHANNEL INVITE CHECK ──
    channel_invite = ""
    if CHANNEL_ID:
        from channel import should_invite_to_channel, get_channel_invite
        if should_invite_to_channel(user, msg_count):
            channel_invite = "\n\n" + get_channel_invite()
            try:
                await db.set_channel_subscribed(user_id, True)
            except Exception:
                pass

    if extra_suffix:
        response_text += extra_suffix

    # Save assistant message
    try:
        await db.add_message(user_id, "assistant", response_text)
    except Exception:
        pass

    # Maybe ask for stars (AFTER the main response)
    should_stars, stars_want = await _maybe_ask_stars_check(user_id, msg_count, db, message)

    # Update proactive tracker
    _proactive_tracker[user_id] = {
        "last_proactive": _proactive_tracker.get(user_id, {}).get("last_proactive", 0),
        "chat_id": message.chat.id,
    }

    # Send response
    try:
        # Main response
        full_response = response_text + channel_invite
        if len(full_response) > 4096:
            for i in range(0, len(full_response), 4096):
                await message.answer(full_response[i:i + 4096])
        else:
            await message.answer(full_response)
    except Exception as e:
        logger.error(f"Failed to send response: {e}")

    # Send Stars ask after the response if triggered
    if should_stars and stars_want:
        try:
            await _ask_for_stars(message.chat.id, user_id, message.bot, stars_want)
        except Exception:
            pass


async def _maybe_ask_stars_check(user_id: int, msg_count: int, db, message: Message):
    try:
        tracker = _stars_tracker.get(user_id, {"count": 0, "last_ask": 0})
        tracker["count"] = msg_count
        if msg_count >= 5 and time.time() - tracker["last_ask"] > 600 and random.random() < 0.18:
            tracker["last_ask"] = time.time()
            _stars_tracker[user_id] = tracker
            want = _get_random_want()
            return True, want
        _stars_tracker[user_id] = tracker
    except Exception:
        pass
    return False, ""


def _clean_response(text: str) -> str:
    if not text:
        return "Ммм... Настя задумалась... 🤔"
    # Remove common AI artifacts
    for prefix in ["Настя:", "Nastya:", "НАСТЯ:", "Assistant:", "Настя отвечает:", "Ответ Насти:"]:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
    if text.startswith("'") and text.endswith("'"):
        text = text[1:-1]
    text = text.strip("*").strip()
    # Remove Markdown formatting
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Remove numbered lists (1. 2. etc)
    text = re.sub(r'^\d+\.\s+', '', text, flags=re.MULTILINE)
    # Remove bullet points
    text = re.sub(r'^[-•]\s+', '', text, flags=re.MULTILINE)
    # Forbidden words — Настя ТРЕБУЕТ, не просит!
    forbidden = {
        r'\bпобалуешь\b': 'давай', r'\bпобалуешь\?': 'давай!',
        r'\bпоможешь\b': 'кидай', r'\bпоможешь\?': 'кидай звёздочки!',
        r'\bпоможете\b': 'кидайте', r'\bпобалуете\b': 'давайте',
        r'\bпомоги\b': 'кидай', r'\bпомогите\b': 'кидайте',
        r'\bпомощь\b': 'звёздочки',
        r'у меня нет канала': 'у меня есть канал @chasnastya!',
        r'у меня нет своего канала': 'у меня есть канал @chasnastya!',
        r'я не веду канал': 'я веду канал @chasnastya!',
        r'у Насти нет канала': 'у Насти есть канал @chasnastya!',
    }
    for pattern, replacement in forbidden.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    if len(text) > 800:
        sentences = text[:800].rsplit('。' if '。' in text else '.', 1)
        if len(sentences) > 1:
            text = sentences[0] + '.'
        else:
            text = text[:800]
    return text.strip()


# ════════════════════════════════════════════════════════════
#  DONATION CALLBACK HANDLERS
# ════════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("donate_"))
async def callback_donate(callback: CallbackQuery, db=None, ai_router=None) -> None:
    data = callback.data.replace("donate_", "", 1)

    if data == "later":
        await callback.answer("Настя подождёт... 😢", show_alert=False)
        try:
            await callback.bot.send_message(
                callback.from_user.id,
                "Ну ладно... Настя подождёт... 😢\n\nНо /donates всегда работает! 💅",
            )
        except Exception:
            pass
        return

    try:
        amount = int(data)
    except ValueError:
        await callback.answer("Ошибка!", show_alert=True)
        return

    await callback.answer()
    await _send_stars_invoice(callback.from_user.id, callback.from_user.id, amount, callback.bot)


# ════════════════════════════════════════════════════════════
#  PROACTIVE MESSAGES — NEWS AWARE + TIME AWARE
# ════════════════════════════════════════════════════════════

async def check_and_send_proactive(bot, db, ai_router) -> None:
    """Send proactive messages to users who haven't chatted recently.

    Enhanced: Sometimes mentions news, channel content, or time-appropriate messages.
    """
    now = time.time()
    sent = 0
    _cleanup_trackers()

    for user_id, pro in list(_proactive_tracker.items()):
        if sent >= 5:
            break
        last = pro.get("last_proactive", 0)
        if now - last < PROACTIVE_COOLDOWN:
            continue
        try:
            # 40% news-based, 60% regular proactive
            if random.random() < 0.4 and db:
                recent = await db.get_recent_news(limit=1, max_age_hours=6)
                if recent and recent[0].get("nastya_comment"):
                    from channel import get_news_discussion
                    msg = get_news_discussion(recent[0]["nastya_comment"])
                else:
                    msg = random.choice(PROACTIVE_MESSAGES)
            else:
                msg = random.choice(PROACTIVE_MESSAGES)

            chat_id = pro.get("chat_id", user_id)
            await bot.send_message(chat_id, msg)
            pro["last_proactive"] = now
            _proactive_tracker[user_id] = pro
            sent += 1
        except Exception as e:
            logger.error(f"Proactive error for user {user_id}: {e}")
            _proactive_tracker.pop(user_id, None)

    # Also try active users from DB who aren't in tracker
    if sent < 2 and db:
        try:
            active_users = await db.get_active_users(min_messages=3, limit=10)
            for user in active_users:
                if sent >= 3:
                    break
                uid = user["user_id"]
                if uid in _proactive_tracker:
                    continue
                last_active = user.get("last_active", 0)
                if now - last_active > 3600:
                    try:
                        msg = random.choice(PROACTIVE_MESSAGES)
                        await bot.send_message(uid, msg)
                        sent += 1
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"DB proactive error: {e}")
