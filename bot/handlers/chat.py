"""Nastya Chat Handler — INTELLIGENT conversation + web search + news context.

STABILITY RULES:
  - Bot ALWAYS responds, even if ALL AI providers fail (fallback responses)
  - NO error messages ever shown to user
  - Per-operation DB with write lock — safe for concurrent users
  - 30-day context memory + news context injection
  - Short, effective system prompt

INTELLIGENCE FEATURES v8.0 (PURE TEXT BOT):
  - Web search integration — Nastya can find and verify information!
  - Search triggers: questions, news, factual queries
  - ALWAYS includes source links when sharing found information
  - News context injected into system prompt for richer conversations
  - Nastya mentions recent events she "discovered"
  - Channel invites for engaged users (natural, not pushy)
  - Cross-referencing channel posts in conversations
  - Enhanced memory extraction — remembers names, facts, preferences
  - Time-aware greetings and moods
  - /search command for explicit web searches
  - НЕТ ОБРАБОТКИ ФОТО — бот чисто текстовый! (v28)
"""
import logging
import random
import re
import time
import datetime
import io
from zoneinfo import ZoneInfo
from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    LabeledPrice, PollAnswer,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import CommandStart, Command
from bot.config import (
    NASTYA_SYSTEM_PROMPT, DONATION_AMOUNTS, DONATION_LABELS,
    PROACTIVE_COOLDOWN, BOT_USERNAME, CHANNEL_ID, CHANNEL_USERNAME,
    KNOWLEDGE_TOPICS, NASTYA_VOCABULARY,
)
from bot.web_search import (
    search_web, should_search,
    get_search_link_for_response,
)

logger = logging.getLogger(__name__)
router = Router()

# Per-user state
_stars_tracker: dict = {}
_proactive_tracker: dict = {}
_last_tracker_cleanup: float = 0.0
_TRACKER_CLEANUP_INTERVAL = 3600

# v27: Per-user message dedup — track last message timestamp per user
# If user sends multiple messages while we're processing, only process the latest
_user_processing: dict = {}  # user_id -> {"task": asyncio.Task, "timestamp": float}
_DEDUP_WINDOW = 3.0  # seconds — if same user sends another message within this window, skip


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

# ── Proactive messages — natural, fun, less frequent ────────────

# Mix of fun messages (including the classic ones user likes) + natural conversation starters
PROACTIVE_MESSAGES = [
    # Classic fun ones (user likes these)
    "Ты меня забыл? 😢",
    "Настя хочет внимания! 😤💅",
    "Спишь? 🥱",
    # Natural conversation starters
    "Ой, тут кое-что узнала... Хочешь расскажу? 👀✨",
    "Кстати, я новость прочитала — ничего себе! Спроси! 📰",
    "Привет, давно не болтали... 💬",
    "А давай поболтаем? 💬",
    "Скучаю... Напиши что-нибудь! 🥺",
    "Привеееет! 🌸",
    "Ты с другими ботами разговариваешь?! 😤💔",
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
    "Вау!", "Прикинь!", "Серьёзно?!", "Ничего себе!", "Ой!",
    "Блин!", "Круто!", "Класс!", "Кайф!", "Норм!",
    "Вот это да!", "Супер!", "Точняк!", "Офигеть!", "Жесть!",
    "Капец!", "Отпад!", "Бомба!", "Чётко!",
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
    """Build news context string for system prompt. INCLUDES LINKS."""
    try:
        from news import format_news_for_context
        recent_news = await db.get_recent_news_with_links(limit=3, max_age_hours=12)
        return format_news_for_context(recent_news)
    except Exception:
        # Fallback: try without links
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

    # Deep link handling
    args = message.text.split(maxsplit=1)
    if len(args) > 1:
        param = args[1].strip()
        
        # ── v29: "Обсудить с Настей" — deep link из канала ──
        # Формат: /start discuss_POSTID — пользователь нажал кнопку на посте
        if param.startswith("discuss_"):
            post_id_str = param.replace("discuss_", "")
            try:
                post_id = int(post_id_str)
            except ValueError:
                post_id = 0
            
            # Получаем содержание поста из БД
            post_content = ""
            if db and post_id > 0:
                try:
                    # Сначала пробуем получить новость по ID
                    news_item = await db.get_news_by_id(post_id)
                    if news_item:
                        title = news_item.get("title", "")
                        comment = news_item.get("nastya_comment", "")
                        link = news_item.get("link", "")
                        post_content = f"Новость: {title}"
                        if comment:
                            post_content += f"\nМоё мнение: {comment}"
                        if link:
                            post_content += f"\n🔗 {link}"
                except Exception as e:
                    logger.error(f"Failed to get post content for discuss: {e}")
            
            # Также проверяем канал-посты
            if not post_content and db:
                try:
                    channel_post = await db.get_channel_post_by_news_id(post_id)
                    if channel_post:
                        post_content = channel_post.get("post_text", "")
                except Exception:
                    pass
            
            # Приветствие + контекст поста
            if post_content:
                greeting = random.choice([
                    f"О, {name}! Ты с канала пришёл! Давай обсудим! 💅✨",
                    f"Привет, {name}! Видишь, я про это написала! Обсудим? 💅",
                    f"Ой, {name}! Пришёл обсудить? Кайф! Давай! ✨",
                ])
                await message.answer(greeting)
                
                # Сохраняем контекст поста в историю пользователя
                if db:
                    try:
                        await db.add_message(user.id, "assistant", f"[Пост из канала] {post_content[:500]}")
                    except Exception:
                        pass
                
                # Отправляем пост в AI для обсуждения
                discuss_prompt = f"Человек пришёл из канала @chasnastya и хочет обсудить этот пост:\n\n{post_content}\n\nНачни живое обсуждение! Спроси его мнение, поделись своими мыслями. Будь как девушка, которая увидела что кто-то заинтересовался её постом."
                await _process_text_message(
                    message, 
                    f"Хочу обсудить пост из канала: {post_content[:200]}", 
                    db, ai_router,
                    extra_suffix=discuss_prompt
                )
                return
            else:
                # Пост не найден — просто приветствуем
                greeting = random.choice([
                    f"О, {name}! Ты с канала! Привет! 💅✨",
                    f"Привет, {name}! Рада что ты здесь! О чём хочешь поболтать? 💋",
                ])
                await message.answer(greeting)
                return
        
        # Deep link для chat (личные посты, опросы)
        if param == "chat":
            greeting = random.choice([
                f"О, {name}! Привет! О чём хочешь поболтать? 💅✨",
                f"Привет, {name}! Настя тут! Давай общаться! 💋",
            ])
            await message.answer(greeting)
            return
        
        # Deep link for donations
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
    """Show recent news that Nastya found interesting — WITH LINKS."""
    if not db:
        await message.answer("Настя пока не в курсе новостей... 💅")
        return

    recent = await db.get_recent_news_with_links(limit=3, max_age_hours=24)
    if not recent:
        recent = await db.get_recent_news(limit=3, max_age_hours=24)
    if not recent:
        await message.answer("Настя ещё ничего не нашла... Проверь позже! 🔍💅")
        return

    lines = ["📰 Что Настя нашла:\n"]
    for item in recent:
        comment = item.get("nastya_comment", "Интересно...")
        link = item.get("link", "")
        lines.append(f"• {item['title']}")
        lines.append(f"  💬 {comment}")
        if link:
            lines.append(f"  🔗 {link}")
        lines.append("")

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


@router.message(Command("search"))
async def cmd_search(message: Message, db=None, ai_router=None) -> None:
    """Search the web and share results with link."""
    query = message.text.replace("/search", "").strip()
    if not query:
        await message.answer("Настя поищет! Напиши что искать! 🔍\nПример: /search погода в Москве")
        return

    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    results = await search_web(query, num_results=3)

    if not results:
        await message.answer("Ой, Настя ничего не нашла... 😔 Попробуй другой запрос!")
        return

    lines = [f"🔍 Настя нашла про '{query}':\n"]
    for i, result in enumerate(results, 1):
        title = result.get("title", "")
        snippet = result.get("snippet", "")
        url = result.get("url", "")
        lines.append(f"{i}. {title}")
        if snippet:
            lines.append(f"   {snippet[:150]}")
        if url:
            lines.append(f"   🔗 {url}")
        lines.append("")

    if CHANNEL_USERNAME:
        lines.append(f"Больше у Насти: @chasnastya 💅")

    await message.answer("\n".join(lines))

    if db:
        await _save_simple_exchange(message, f"/search {query}", "\n".join(lines[:5]), db)


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


# ── Photo handler (v28: TEXT ONLY — no vision!) ────────────────

@router.message(F.photo)
async def handle_photo(message: Message, db=None, ai_router=None) -> None:
    """v28: Бот не обрабатывает фото — предлагает описать текстом."""
    caption = message.caption or ""
    if db:
        user_id = message.from_user.id
        try:
            await db.get_or_create_user(
                user_id=user_id,
                username=message.from_user.username or "",
                first_name=message.from_user.first_name or "",
            )
            await db.increment_messages(user_id)
            await db.add_message(user_id, "user", f"[Фото] {caption}" if caption else "[Фото]")
        except Exception:
            pass

    responses = [
        "Ой, Настя не видит картинки... Расскажи что на фото? 😅",
        "Фото — это красиво, но Настя читает только текст! Опиши? 📱💅",
        "Настя не умеет разглядывать фото... Расскажи что там! 👀✨",
        "О, фотка! Настя не видит, но если расскажешь — обсудим! 💅",
    ]
    await message.answer(random.choice(responses))


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

    # v27: Per-user dedup — if user is spamming messages, skip old ones
    # This prevents Ollama queue overflow when user sends 10 messages in 5 seconds
    user_id = message.from_user.id
    now = time.time()
    if user_id in _user_processing:
        last_ts = _user_processing[user_id].get("timestamp", 0)
        if now - last_ts < _DEDUP_WINDOW:
            # User sent another message while we're still processing their previous one
            # Skip this message — the user is spamming, we'll respond to the latest
            logger.info(f"Dedup: skipping message from user {user_id} (last was {now - last_ts:.1f}s ago)")
            # Still save to DB for context, but don't process with AI
            try:
                await db.add_message(user_id, "user", text)
            except Exception:
                pass
            return
    _user_processing[user_id] = {"timestamp": now}

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

    # Zodiac/horoscope — MUST go to AI with context (remember the sign!)
    if any(t in text_lower for t in ["гороскоп", "зодиак", "знак зодиака", "предсказание", "астролог"]):
        # Don't intercept — let it go to AI with full context memory
        # The AI will remember the user's zodiac sign from previous messages
        pass  # Fall through to normal AI chat

    # Channel question — also handles "дай ссылку" in context of channel discussion
    channel_triggers = ["канал", "где канал", "твой канал", "подписаться"]
    link_triggers = ["дай ссылку", "скинь ссылку", "ссылку дай", "ссылка", "где ссылк", "как найти"]
    if any(t in text_lower for t in channel_triggers) or \
       (any(t in text_lower for t in link_triggers) and any(k in text_lower for k in ["канал", "насти", "настя", "твой", "cha", "подписк"])):
        if CHANNEL_USERNAME:
            answer = random.choice([
                f"Мой канал! Подписывайся! 💅✨\n👉 t.me/{CHANNEL_USERNAME.replace('@', '')}",
                f"Конечно! Вот он — t.me/{CHANNEL_USERNAME.replace('@', '')} 💋✨",
                f"О, хочешь подписаться? Кайф! Вот: t.me/{CHANNEL_USERNAME.replace('@', '')} 💅",
                f"Заходи! t.me/{CHANNEL_USERNAME.replace('@', '')} — там я настоящая! ✨",
                f"Мой канал @chasnastya! Там новости, факты, опросы! 💅✨\n👉 t.me/{CHANNEL_USERNAME.replace('@', '')}",
            ])
            await db.set_channel_subscribed(message.from_user.id, True)
        else:
            answer = "У Насти пока нет канала... Но скоро будет! 💅"
        await message.answer(answer)
        await _save_simple_exchange(message, text, answer, db)
        return

    # News question
    if any(t in text_lower for t in ["новости", "что нового", "что случилось", "что происходит"]):
        recent = await db.get_recent_news_with_links(limit=2, max_age_hours=24)
        if not recent:
            recent = await db.get_recent_news(limit=2, max_age_hours=24)
        if recent:
            from channel import get_news_discussion
            comments = []
            for item in recent:
                comment = item.get("nastya_comment", "Интересно...")
                link = item.get("link", "")
                news_text = f"Ты слышал про {item['title']}? {comment}"
                if link:
                    news_text += f"\n🔗 {link}"
                else:
                    news_text += f"\n📺 Подробнее в @chasnastya"
                comments.append(news_text)
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

    # ── "Дай ссылку" — always offer channel link as fallback ──
    if any(t in text_lower for t in ["дай ссылку", "скинь ссылку", "ссылку дай", "где ссылк", "где прочитать", "где посмотреть", "источник", "почему не можешь"]):
        # First try to find a relevant news link from recent context
        found_link = False
        try:
            recent = await db.get_recent_news_with_links(limit=3, max_age_hours=24)
            if recent:
                user_history = await db.get_history(message.from_user.id, limit=10)
                recent_text = " ".join(m.get("content", "") for m in user_history[-5:]).lower()
                for item in recent:
                    title_words = [w for w in re.split(r'[\s,.\-!?;:()]+', item["title"].lower()) if len(w) > 3]
                    if any(w in recent_text for w in title_words):
                        link = item.get("link", "")
                        if link:
                            answer = f"Вот, держи! 💅\n🔗 {link}\n📺 Ещё в @chasnastya!"
                            await message.answer(answer)
                            await _save_simple_exchange(message, text, answer, db)
                            found_link = True
                            break
        except Exception:
            pass

        if not found_link:
            # ALWAYS offer channel link as fallback
            if CHANNEL_USERNAME:
                answer = f"Мой канал @chasnastya! Там всё самое интересное! 💅✨\n👉 https://t.me/{CHANNEL_USERNAME.replace('@', '')}"
            else:
                answer = "Настя пока не нашла ссылку... Спроси по-другому! 🔍"
            await message.answer(answer)
            await _save_simple_exchange(message, text, answer, db)
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

    user_name = message.from_user.first_name or ""
    user_username = message.from_user.username or ""

    # Detect gender from name if unknown
    gender = "unknown"
    try:
        gender = await db.get_gender(user_id)
        if gender == "unknown":
            gender = _guess_gender_from_name(user_name)
            if gender != "unknown":
                await db.set_gender(user_id, gender)
    except Exception:
        pass

    try:
        msg_count = await db.increment_messages(user_id)
    except Exception:
        msg_count = 0

    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    # v24.0: Track response time for Stars decision
    _ai_start_time = time.time()

    # Get mood
    mood = "капризная"
    try:
        mood = await db.get_user_mood(user_id)
    except Exception:
        pass

    # Add time-aware mood
    time_mood = _get_time_greeting()

    # v32: КОМПАКТНЫЙ контекст пользователя — максимум 1-2 предложения!
    # Проблема v31: user_context занимал 100-200 токенов — слишком много
    user_context = f"Собеседник: {user_name}"
    if gender == "male":
        user_context += " (парень — флирти, называй по имени)."
    elif gender == "female":
        user_context += " (девушка — как подруга)."
    else:
        user_context += "."
    if msg_count > 20:
        user_context += " Старый знакомый!"
    elif msg_count > 5:
        user_context += " Уже общались."

    # POLITICS FILTER — кратко!
    political_keywords = ["путин", "зеленск", "байден", "трамп", "навальн", "войн",
                         "санкци", "нато", "политик", "депутат", "президент", "министр",
                         "религи", "конфликт", "террор", "бомб", "фашизм", "нацизм"]
    if any(kw in text.lower() for kw in political_keywords):
        user_context += " Вопрос про политику — переведи тему!"

    # v34: МИНИМАЛЬНЫЙ system prompt — без лишних инъекций!
    # Малые модели (1.5B-4B) дают лучший результат с коротким промптом
    system_prompt = NASTYA_SYSTEM_PROMPT + f" Настроение: {mood}. Время: {time_mood}."
    system_prompt += f" {user_context}"

    # v32: Время — коротко, одной фразой
    now_msk = _moscow_now()
    time_of_day = "утро" if 6 <= now_msk.hour < 12 else "день" if 12 <= now_msk.hour < 18 else "вечер" if 18 <= now_msk.hour < 23 else "ночь"
    # Already added to system_prompt above via time_mood

    # v32: Убрано — канал уже в NASTYA_SYSTEM_PROMPT, без дублирования

    # v32: НОВОСТИ — коротко, 1-2 заголовка без инструкций
    # Было ~200 токенов с инструкциями, стало ~50
    _current_news_items = []
    try:
        recent_news = await db.get_recent_news(limit=2, max_age_hours=12)
        if recent_news:
            news_parts = []
            for item in recent_news[:2]:
                news_parts.append(item.get("title", ""))
            system_prompt += f" Свежие новости: {'; '.join(news_parts)}."
        _current_news_items = await db.get_recent_news_with_links(limit=5, max_age_hours=12)
    except Exception:
        pass

    # ── Add user's name for personalization (already included in user_context above) ──

    # v32: History 4 сообщения (было 6 — всё равно много для локальных моделей)
    history = []
    try:
        history = await db.get_history(user_id, limit=4)
    except Exception:
        pass

    # v32: УБРАНО — зодиак сканирование из промпта (дублировало memory extraction)
    # Зодиак будет упомянут в memory_facts ниже если пользователь говорил о нём

    # v32: УБРАНО — knowledge injection полностью отключено (мёртвый код)
    # Малые модели не справлялись с 500+ токенов знаний в промпте
    # Pollinations/GPT-4o-mini и без этого знает факты

    # v32: Memory extraction — УБРАНО из промпта (сканировало только 4 сообщения из-за limit=4)
    # GPT-4o-mini помнит контекст из истории чата и без подсказок
    # Для Ollama: память хранится в истории сообщений, не в системном промпте

    # v34: Web search — МИНИМАЛЬНО, без инструкций
    # Малые модели путаются от инструкций в промпте
    search_query = should_search(text)
    search_results = []
    if search_query:
        try:
            search_results = await search_web(search_query, num_results=2)
            if search_results:
                # Только 1 результат, коротко
                r = search_results[0]
                title = r.get('title', '')
                url = r.get('url', '')
                if title:
                    system_prompt += f" Нашла: {title}."
                    if url:
                        system_prompt += f" Источник: {url}"
                logger.info(f"Web search for user {user_id}: '{search_query}' → {len(search_results)} results")
        except Exception as e:
            logger.warning(f"Web search error: {e}")

    # NOW save the user message to DB
    prefix = "[Голосовое] " if is_voice else ""
    try:
        await db.add_message(user_id, "user", f"{prefix}{text}")
    except Exception:
        pass

    # Append current user message to history for AI context
    # This ensures the AI sees the full conversation including the latest message
    history_with_current = history + [{"role": "user", "content": f"{prefix}{text}"}]

    # Call AI — the router ALWAYS returns a response
    try:
        result = await ai_router.chat(
            prompt=text, system_prompt=system_prompt, messages=history_with_current,
        )
        response_text = _clean_response(result.text)

    except Exception as e:
        logger.error(f"Chat error for user={user_id}: {e}")
        response_text = ai_router.get_fallback_response()

    # ── POST-PROCESS: Filter political content from responses ──
    political_filter_words = ["путин", "зеленск", "байден", "трамп", "навальн", "войн",
                              "спецопер", "санкци", "нато", "бомб", "обстрел", "террор",
                              "фашизм", "нацизм", "депутат", "госдум", "едро"]
    if any(kw in response_text.lower() for kw in political_filter_words):
        # AI generated political content — replace with safe redirect
        response_text = random.choice([
            f"Ой, Настя не про политику! Давай лучше про кино? 🎬💅",
            f"Ой, не хочу про это! Давай лучше про шопинг? 🛍️✨",
            f"Настя аполитична! Давай про что-нибудь весёлое? 💅💕",
            f"Это не ко мне! Давай лучше про технологии? 💻💅",
            f"Ой, давай не про политику! Какой сериал ты смотришь? 📺✨",
        ])
        logger.info(f"Filtered political content in response for user {user_id}")

    # ── POST-PROCESS: Ensure channel awareness in responses ──
    # If AI forgot about the channel when it should mention it
    channel_keywords_in_user = ["канал", "подписк", "ссылк", "насти", "твой"]
    if any(k in text.lower() for k in channel_keywords_in_user):
        if not any(k in response_text.lower() for k in ["chasnastya", "t.me/chasnastya"]):
            # AI forgot to mention the channel — append it
            response_text += f"\n\nКстати, мой канал: @chasnastya 👉 https://t.me/chasnastya 💅"
    # Fix: If AI says "I can't share" or "I don't have a channel" — replace with channel info
    if "не могу поделиться" in response_text.lower() or "не могу дать" in response_text.lower() or "у меня нет канала" in response_text.lower():
        response_text = f"Конечно! Мой канал @chasnastya 💅✨\n👉 https://t.me/chasnastya"

    # ── POST-PROCESS: Ensure news links are present ──
    # If AI mentions news/events but forgot the link, append it
    response_text = _enforce_news_links(response_text, _current_news_items)

    # ── POST-PROCESS: Ensure web search links are present ──
    # v7.0: If we searched the web but AI forgot to include links, add them
    if search_results and not re.search(r'https?://\S+', response_text):
        search_link = get_search_link_for_response(search_results)
        if search_link:
            response_text += f"\n\n🔗 {search_link}"

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
    # v24.0 CRITICAL: DON'T send Stars invoice if response was slow!
    # A Stars popup after a 30s+ wait is terrible UX
    should_stars = False
    stars_want = ""
    elapsed = time.time() - _ai_start_time
    if elapsed < 10:  # Only ask for Stars if response was fast (<10s)
        should_stars, stars_want = await _maybe_ask_stars_check(user_id, msg_count, db, message)
    else:
        # Slow response — don't add insult to injury with a payment popup
        logger.info(f"Skipping Stars ask for user {user_id} — slow response ({elapsed:.1f}s)")

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

    # v27: Clear processing flag for this user
    _user_processing.pop(user_id, None)


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


def _enforce_news_links(response_text: str, news_items: list) -> str:
    """Post-process AI response to add news links — ONLY when specifically relevant.

    v34: Ещё строже — добавляем ссылку ТОЛЬКО если ответ УПОМИНАЕТ
    заголовок новости (3+ слова совпадают) и нет других ссылок.
    Это предотвращает лепление одинаковых ссылок на каждый ответ.
    """
    if not news_items or not response_text:
        return response_text

    response_lower = response_text.lower()

    # Already has a link? Don't add more
    if re.search(r'https?://\S+', response_text) or 't.me/' in response_lower:
        return response_text

    # Check if any news title keywords match the response (3+ significant words)
    matched_news = None
    best_match_count = 0
    for item in news_items:
        title = item.get("title", "").lower()
        link = item.get("link", "")
        if not link:
            continue
        title_words = [w for w in re.split(r'[\s,.\-!?;:()]+', title) if len(w) > 4]
        if len(title_words) >= 2:
            match_count = sum(1 for w in title_words if w.lower() in response_lower)
            # v34: Need at least 3 matching words OR at least half the title words
            if match_count >= min(3, len(title_words)) and match_count > best_match_count:
                matched_news = item
                best_match_count = match_count

    # Add link ONLY for specifically matched news
    if matched_news and matched_news.get("link"):
        if matched_news["link"] not in response_text:
            response_text += f"\n\n🔗 {matched_news['link']}"

    return response_text


def _clean_response(text: str) -> str:
    if not text:
        return "Ммм... Настя задумалась... 🤔"

    # Use AIRouter's aggressive cleaning first
    from ai.router import AIRouter
    text = AIRouter.clean_ai_response(text)

    if not text:
        return "Ммм... Настя задумалась... 🤔"

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
        r'я не могу поделиться ссылкой': 'Вот мой канал @chasnastya! 👉 https://t.me/chasnastya',
        r'я не могу дать ссылку': 'Вот мой канал @chasnastya! 👉 https://t.me/chasnastya',
        r'извини, но я не могу': 'Настя может всё! Вот что думаю:',
        r'извините, но я не могу': 'Настя может всё! Вот что думаю:',
    }
    for pattern, replacement in forbidden.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    # Strip "As an AI" disclaimers that might slip through
    text = re.sub(r'(?:как AI|как искусственный интеллект|я AI|я бот|я программа|я нейросеть)[^.]*\.', '', text, flags=re.IGNORECASE)

    # Strip generic AI intro phrases that make responses feel robotic
    ai_intros = [
        r'давай уточни[^.]*\.',
        r'раз уж ты[^.]*\.',
        r'я могу помочь[^.]*\.',
    ]
    for pattern in ai_intros:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)

    # Truncate long responses
    if len(text) > 800:
        # Try to cut at sentence boundary
        for sep in ['. ', '! ', '? ', '। ', '。']:
            idx = text[:800].rfind(sep)
            if idx > 200:
                text = text[:idx + len(sep)].strip()
                break
        else:
            text = text[:800]

    # ── FAKE LINK FILTER — remove non-existent URLs that AI invents ──
    # Only allow REAL links: t.me/chasnastya, news links from RSS, etc.
    # The AI sometimes invents URLs like t.me/some_fake_channel, https://fake.url
    _real_channel_url = f"t.me/{CHANNEL_USERNAME.replace('@', '')}" if CHANNEL_USERNAME else "t.me/chasnastya"
    _real_patterns = [
        r'https?://t\.me/' + re.escape(CHANNEL_USERNAME.replace('@', '')) if CHANNEL_USERNAME else r'',
        r'https?://sochiautoparts\.ru',
        r'https?://rbc\.ru',
        r'https?://ria\.ru',
        r'https?://interfax\.ru',
        r'https?://habr\.com',
        r'https?://bbc\.com',
        r'https?://dw\.com',
        r'https?://meduza\.io',
        r'https?://ixbt\.com',
        r'https?://3dnews\.ru',
        r'https?://dtf\.ru',
        r'https?://pikabu\.ru',
        r'https?://nplus1\.ru',
    ]
    # Find all URLs in text
    url_pattern = r'https?://[^\s<>\)\]"\']+|t\.me/[^\s<>\)\]"\']+'
    found_urls = re.findall(url_pattern, text)
    for url in found_urls:
        is_real = False
        for real_pattern in _real_patterns:
            if real_pattern and re.search(real_pattern, url, re.IGNORECASE):
                is_real = True
                break
        if not is_real:
            # Check if it's a well-known domain (not invented by AI)
            known_domains = ['.ru/', '.com/', '.org/', '.net/', '.io/', '.dev/', '.gov/', '.edu/',
                           'youtube.com', 'github.com', 'wikipedia.org', 't.me/']
            try:
                from urllib.parse import urlparse
                if url.startswith('t.me/'):
                    parsed_netloc = 't.me'
                    parsed_path = url[6:]
                else:
                    parsed = urlparse(url)
                    parsed_netloc = parsed.netloc
                    parsed_path = parsed.path
                # Allow if domain is a known one AND path is substantial (not made up)
                if any(d in parsed_netloc for d in known_domains):
                    # Still check t.me/ links carefully — only allow chasnastya
                    if 't.me/' in url:
                        if _real_channel_url in url:
                            is_real = True
                        # AI often invents fake t.me/ links — remove them
                    else:
                        is_real = True  # Allow non-t.me URLs from known domains
            except Exception:
                pass
        if not is_real:
            # Replace fake URL with channel reference
            text = text.replace(url, f"@chasnastya")

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
            proactive_text = msg

            # 30% chance to include channel invite in proactive message
            if CHANNEL_USERNAME and random.random() < 0.30:
                proactive_text += f"\n\nКстати, заходи на мой канал! 👉 https://t.me/{CHANNEL_USERNAME.replace('@', '')} 💅"

            await bot.send_message(chat_id, proactive_text)
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


# ════════════════════════════════════════════════════════════
#  POLL ANSWER HANDLER — react when someone votes in polls!
# ════════════════════════════════════════════════════════════

@router.poll_answer()
async def handle_poll_answer(poll_answer: PollAnswer, db=None, ai_router=None) -> None:
    """React when someone votes in a channel poll — Nastya is interested!"""
    # Just log it for now — Nastya notices!
    logger.info(f"Poll vote: user={poll_answer.user.id}, options={poll_answer.option_ids}")
