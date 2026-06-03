"""Nastya Chat Handler v12.0 — MULTI-MODEL + VISION + HUMAN-LIKE + URL + INLINE!

STABILITY RULES:
  - Bot ALWAYS responds, even if ALL AI providers fail (fallback responses)
  - NO error messages ever shown to user
  - Per-operation DB with write lock — safe for concurrent users
  - 30-day context memory + news context injection
  - Short, effective system prompt

INTELLIGENCE FEATURES v12.0 (MULTI-MODEL POLLINATIONS + HUMAN-LIKE + URL):
  - Pollinations.ai MULTI-MODEL — 8 VERIFIED models with load balancing!
  - Automatic failover: if one model fails, next one picks up
  - Qwen3-4B local GGUF as LAST FALLBACK
  - REAL PHOTO UNDERSTANDING — Настя ВИДИТ что на фото!
  - PHOTO SEARCH — определение объектов на фото по запросу
  - URL UNDERSTANDING — Настя читает ссылки и понимает контекст!
  - INLINE MODE — Настя в любом чате через @bot_username
  - Typing delay indicators — Настя "живой" собеседник
  - Web search integration — Nastya can find and verify information!
  - News discussion with emotions — Настя рассказывает подробно!
  - Group chat message length limiting — короче в группах
  - Expanded proactive messaging — Настя активный собеседник
  - Smart message splitting by sentence boundaries
"""
import asyncio
import logging
import random
import re
import time
import datetime
import io
import httpx
from zoneinfo import ZoneInfo
from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    LabeledPrice, PollAnswer, InlineQuery, InlineQueryResultArticle,
    InputTextMessageContent,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import CommandStart, Command
from bot.config import (
    NASTYA_SYSTEM_PROMPT, DONATION_AMOUNTS, DONATION_LABELS,
    PROACTIVE_COOLDOWN, BOT_USERNAME, CHANNEL_ID, CHANNEL_USERNAME,
    KNOWLEDGE_TOPICS, NASTYA_VOCABULARY, MODEL_HISTORY_LIMIT,
    GROUP_MAX_MESSAGE_LENGTH, GROUP_RESPONSE_CHANCE,
    TYPING_DELAY_THRESHOLD, TYPING_DELAY_CHANCE,
    POLLINATIONS_MAX_TOKENS,
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

# v44: URL detection regex
_URL_PATTERN = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+')

# v44: Photo search keywords — определение объектов на фото
_PHOTO_SEARCH_KEYWORDS = ["что это", "найди", "поиск", "что за", "определи", "узнай что это",
                          "что на фото", "что изображено", "распознай", "опознай"]

# v42: Per-user message dedup — track ACTIVE AI tasks per user
_user_processing: dict = {}  # user_id -> asyncio.Task (active AI task) or None


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


# ════════════════════════════════════════════════════════════
#  URL UNDERSTANDING — Настя читает ссылки!
# ════════════════════════════════════════════════════════════

async def _fetch_url_content(url: str) -> str:
    """Fetch and extract text content from a URL.

    Uses httpx (no extra dependencies) with a short timeout.
    Strips HTML tags to get plain text content.
    """
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0),
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"},
        ) as client:
            response = await client.get(url)
            if response.status_code == 200:
                text = response.text
                # Remove script and style tags with content
                text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
                text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
                # Remove all HTML tags
                text = re.sub(r'<[^>]+>', ' ', text)
                # Normalize whitespace
                text = re.sub(r'\s+', ' ', text).strip()
                return text[:2000]
    except Exception:
        pass
    return ""


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

# ════════════════════════════════════════════════════════════
#  TYPING DELAY INDICATORS — Настя "живой" собеседник!
# ════════════════════════════════════════════════════════════

TYPING_DELAY_PHRASES = [
    "Секунду, Настя думает... 🤔",
    "Блин, Настя задумалась... 💭",
    "Ой, голова разболелась... Щас отвечу! 😫",
    "Отошла на минутку! Сейчас вернусь! 🏃‍♀️",
    "Настя вспоминает... Подожди! 💅",
    "Ммм... Настя формулирует мысль! 🤔",
    "Котятки, Настя не бот — нужно время подумать! 😤",
    "Щас-щас, Настя набирает! ⌨️💅",
    "Ой, Настя отвлеклась на котика... Сейчас отвечу! 🐱",
    "Настя наливает кофе... Минутку! ☕",
]

async def _send_typing_delay(message: Message, delay_seconds: float = 0) -> None:
    """Send a typing indicator phrase while AI is processing.

    Makes Настя feel more human — she's 'thinking' or 'distracted'
    rather than being a silent loading bot.
    Only sends if AI is expected to take >3 seconds.
    """
    if delay_seconds > 3.0:
        try:
            phrase = random.choice(TYPING_DELAY_PHRASES)
            await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
            # Send the delay phrase after a brief pause
            await asyncio.sleep(min(delay_seconds * 0.4, 2.0))
            await message.answer(phrase)
        except Exception:
            pass


# ── Proactive messages — EXPANDED for human-like behavior ────────────

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
    # v42: NEW — More human-like, news-aware, emotional
    "Слушай, я тут статью прочитала — прикинь что узнала! Спрашивай! 📰✨",
    "Блин, не могу молчать! Только что новость увидела — шок! 😱🔥",
    "Настя тут подумала о жизни... А ты о чём думаешь? 💭🌙",
    "Ой, я рецепт нашла — классный! Хочешь? 🍳💅",
    "Слушай, а ты знаешь что... Ладно, сама расскажу если спросишь! 🤭",
    "Насте скучно... Расскажи что-нибудь интересное! 🥺💬",
    "Котятки, я тут кино смотрела — эмоции через край! 🎬😭",
    "Привет! Как день прошёл? Настя хочет знать! 💅✨",
    "О, только что с подружкой болтала — есть тема! Спроси! 💬👀",
    "Настя не может уснуть... Поболтаем? 🌙😴",
    "Блин, я сегодня ленивая... Кто со мной? 😴💅",
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

# ── News discussion phrases — Настя рассказывает подробно! ──

NEWS_DISCUSSION_PHRASES = [
    "Прикинь, я тут прочитала про {topic}! {emotion} {detail}",
    "Офигеть, ты знаешь про {topic}? {emotion} Настя прямо в шоке!",
    "Слушай, новость про {topic}! {emotion} {detail}",
    "Блин, я не могу молчать про {topic}! {emotion} {detail}",
    "Котятки, тут такое про {topic}! {emotion} {detail}",
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
        try:
            from news import format_news_for_context
            recent_news = await db.get_recent_news(limit=3, max_age_hours=12)
            return format_news_for_context(recent_news)
        except Exception:
            return ""


async def _maybe_news_opener(db, ai_router, user_id: int) -> str:
    """Maybe start conversation with a news item. Returns empty string if not."""
    if random.random() > 0.12:
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

# IMPORTANT: Inline mode must be enabled via BotFather for this to work!
# Send /setinline to @BotFather and enable inline mode for @asnastya_bot

@router.inline_query()
async def inline_query_handler(inline_query: InlineQuery, db=None, ai_router=None) -> None:
    """Inline mode — Настя отвечает в любом чате через @asnastya_bot!
    
    Users can type @asnastya_bot <question> in any chat to get a response.
    """
    query = inline_query.query.strip()
    
    if not query:
        # Show hint when no query
        results = [
            InlineQueryResultArticle(
                id="hint",
                title="Напиши вопрос для Насти!",
                description="Например: @asnastya_bot Привет! Как дела?",
                input_message_content=InputTextMessageContent(
                    message_text="Привет! Напиши мне вопрос через @asnastya_bot 😊💅"
                ),
            )
        ]
        await inline_query.answer(results, cache_time=5)
        return
    
    # Generate response via AI
    response_text = ""
    if ai_router and ai_router._pollinations and ai_router._pollinations.is_available():
        try:
            from bot.config import NASTYA_SYSTEM_PROMPT
            result = await ai_router.chat(
                prompt=query,
                system_prompt=NASTYA_SYSTEM_PROMPT + "\n\nОТВЕЧАЙ ОЧЕНЬ КОРОТКО — максимум 2-3 предложения для inline режима!",
                max_tokens=200,
            )
            if result and result.text:
                response_text = result.text[:300]  # Inline results should be short
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Inline query AI error: {e}")
    
    if not response_text:
        # Fallback responses
        import random
        response_text = random.choice([
            "Ой, Настя задумалась... Попробуй ещё раз! 💅",
            "Блин, не успела! Ещё раз? 💭",
            "Настя пока не может ответить... ⏳",
        ])
    
    # Clean response
    import re
    response_text = re.sub(r'<[^>]+>', '', response_text).strip()
    response_text = response_text[:300]
    
    results = [
        InlineQueryResultArticle(
            id="nastya_response",
            title=f"Настя: {response_text[:50]}...",
            description=response_text[:100],
            input_message_content=InputTextMessageContent(
                message_text=response_text
            ),
        )
    ]
    
    await inline_query.answer(results, cache_time=10)


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
        
        if param.startswith("discuss_"):
            post_id_str = param.replace("discuss_", "")
            try:
                post_id = int(post_id_str)
            except ValueError:
                post_id = 0
            
            post_content = ""
            if db and post_id > 0:
                try:
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
            
            if not post_content and db:
                try:
                    channel_post = await db.get_channel_post_by_news_id(post_id)
                    if channel_post:
                        post_content = channel_post.get("post_text", "")
                except Exception:
                    pass
            
            if post_content:
                greeting = random.choice([
                    f"О, {name}! Ты с канала пришёл! Давай обсудим! 💅✨",
                    f"Привет, {name}! Видишь, я про это написала! Обсудим? 💅",
                    f"Ой, {name}! Пришёл обсудить? Кайф! Давай! ✨",
                ])
                await message.answer(greeting)
                
                if db:
                    try:
                        await db.add_message(user.id, "assistant", f"[Пост из канала] {post_content[:500]}")
                    except Exception:
                        pass
                
                discuss_prompt = (
                    f"Пришёл из канала обсудить пост:\n{post_content[:500]}\n\n"
                    f"Поделись мнением, спроси что думает, дай ссылку если есть. "
                    f"4-6 предложений, живо и с интересом!"
                )
                task = asyncio.create_task(
                    _process_text_message(
                        message, 
                        f"Давай обсудим этот пост: {post_content[:300]}", 
                        db, ai_router,
                        extra_suffix=discuss_prompt
                    )
                )
                _user_processing[message.from_user.id] = task
                return
            else:
                greeting = random.choice([
                    f"О, {name}! Ты с канала! Привет! 💅✨",
                    f"Привет, {name}! Рада что ты здесь! О чём хочешь поболтать? 💋",
                ])
                await message.answer(greeting)
                return
        
        if param == "chat":
            greeting = random.choice([
                f"О, {name}! Привет! О чём хочешь поболтать? 💅✨",
                f"Привет, {name}! Настя тут! Давай общаться! 💋",
            ])
            await message.answer(greeting)
            return
        
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

        task = asyncio.create_task(
            _process_text_message(message, transcript, db, ai_router, is_voice=True)
        )
        _user_processing[message.from_user.id] = task
    except Exception as e:
        logger.error(f"Voice handler error: {e}")
        await message.answer("Ой, у Насти ушки заболели... Напиши текстом! 👂😅")


# ── Photo handler — v42: REAL VISION! Настя ВИДИТ фото! ────

# Per-user photo rate limiter
_user_photo_times: dict = {}  # user_id -> last_photo_time
_PHOTO_RATE_LIMIT = 2.0  # seconds between photo responses per user


@router.message(F.photo)
async def handle_photo(message: Message, db=None, ai_router=None) -> None:
    """v42: Фото обработчик — Настя ВИДИТ что на фото!

    - REAL VISION: Download photo → base64 → Pollinations vision API
    - Если есть подпись (caption) — дополняет понимание
    - Групповые фото и пересылки ИГНОРИРУЮТСЯ (flood control!)
    - Rate limiting: max 1 ответ каждые 2 секунды
    """
    caption = message.caption or ""

    # ИГНОРИРУЕМ фото из групп и пересылки — flood control!
    chat_type = message.chat.type if message.chat else "private"
    is_forward = message.forward_date is not None

    if chat_type != "private" or is_forward:
        return

    # Rate limit per user
    user_id = message.from_user.id
    now = time.time()
    last_photo_time = _user_photo_times.get(user_id, 0)
    if now - last_photo_time < _PHOTO_RATE_LIMIT:
        return
    _user_photo_times[user_id] = now

    if db:
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

    # ── VISION: Download photo and analyze with Pollinations! ──
    if ai_router and ai_router._pollinations and ai_router._pollinations.is_available():
        try:
            # Send typing indicator
            await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

            # Send a quick human-like delay phrase
            delay_phrase = random.choice([
                "Ой, фотка! Настя смотрит... 👀",
                "Фото! Щас Настя разглядит... 📸💅",
                "О, картинка! Настя изучает... 🔍✨",
            ])
            await message.answer(delay_phrase)

            # Download the photo (largest size)
            photo = message.photo[-1]  # Largest resolution
            file = await message.bot.get_file(photo.file_id)
            buf = io.BytesIO()
            await message.bot.download_file(file.file_path, buf)
            image_bytes = buf.getvalue()

            # Build vision prompt
            caption_lower = (caption or "").lower()
            is_search_photo = any(kw in caption_lower for kw in _PHOTO_SEARCH_KEYWORDS)

            if is_search_photo:
                # PHOTO SEARCH MODE — определение и поиск по фото
                vision_prompt = (
                    "Определи что изображено на этом фото подробно. "
                    "Если это товар — назови бренд и модель. "
                    "Если место — назови его. "
                    "Если растение/животное — определи вид. "
                    "Дай максимум полезной информации."
                )
                if caption:
                    vision_prompt += f" Контекст: {caption}."

                vision_system = (
                    "Ты Настя — девушка из Москвы, 23 года. Ты не бот, ты живой человек. "
                    "Ты видишь фото и определяешь что на нём изображено. "
                    "Отвечай подробно но живо, 3-5 предложений. "
                    "Используй слова: 'прикинь', 'офигеть', 'капец', 'круто'. "
                    "Без markdown, без буллетов."
                )
            else:
                vision_prompt = "Опиши что ты видишь на этом фото. Отреагируй живо и эмоционально, как девушка из Москвы (Настя, 23 года)."
                if caption:
                    vision_prompt += f" Подпись к фото: {caption}. Отреагируй и на подпись тоже."

                # System prompt for vision
                vision_system = (
                    "Ты Настя — девушка из Москвы, 23 года. Ты не бот, ты живой человек. "
                    "Ты видишь фото и реагируешь на него живо и эмоционально. "
                    "2-4 предложения, разговорный стиль. "
                    "Используй слова: 'прикинь', 'офигеть', 'капец', 'круто'. "
                    "Без markdown, без буллетов."
                )

            # Call vision API
            result = await ai_router.vision(
                prompt=vision_prompt,
                image_data=image_bytes,
                image_format="jpeg",
                system_prompt=vision_system,
            )

            response_text = result.text
            if response_text:
                cleaned = _clean_response(response_text)
                if cleaned:
                    # Save to DB
                    try:
                        await db.add_message(user_id, "assistant", cleaned)
                    except Exception:
                        pass
                    await message.answer(cleaned)
                    return

        except Exception as e:
            logger.error(f"Vision handler error: {e}")
            # Fall through to caption-only handling

    # ── FALLBACK: Caption-only processing (if vision failed or no Pollinations) ──
    if caption and ai_router and db:
        task = asyncio.create_task(
            _process_text_message(
                message, f"Скинула фотку! {caption}", db, ai_router,
                extra_context="Пользователь прислал фото с подписью. Отреагируй на подпись живо и эмоционально, как будто видишь фото. Спроси что на фото если интересно."
            )
        )
        _user_processing[user_id] = task
        return

    # No caption, no vision — ask what's in the photo
    responses = [
        "Ой, фотка! А что на ней? Расскажи! 📸💅",
        "О, фото! Настя не видит, но если расскажешь — обсудим! 💅",
        "Фотка! Опиши что там — Насте интересно! 👀✨",
        "О, картинка! Что на ней? Настя хочет знать! 📱💅",
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
#  MAIN TEXT CHAT HANDLER — INTELLIGENT + NEWS AWARE + GROUP LIMITS
# ════════════════════════════════════════════════════════════

@router.message(F.text, ~F.text.startswith("/"))
async def handle_chat(message: Message, db=None, ai_router=None) -> None:
    if not db or not ai_router:
        await message.answer("Настя пока не готова... Подожди минуточку! 💅")
        return

    text = message.text
    text_lower = text.lower()

    user_id = message.from_user.id
    active_task = _user_processing.get(user_id)
    if active_task is not None and not active_task.done():
        logger.info(f"Dedup: skipping message from user {user_id} (AI still processing)")
        try:
            await db.add_message(user_id, "user", text)
        except Exception:
            pass
        return

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

    # Zodiac/horoscope — MUST go to AI with context
    if any(t in text_lower for t in ["гороскоп", "зодиак", "знак зодиака", "предсказание", "астролог"]):
        pass  # Fall through to normal AI chat

    # Channel question
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

    # News question — Настя рассказывает ПОДРОБНО с ЭМОЦИЯМИ!
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
            if CHANNEL_USERNAME:
                answer = f"Мой канал @chasnastya! Там всё самое интересное! 💅✨\n👉 https://t.me/{CHANNEL_USERNAME.replace('@', '')}"
            else:
                answer = "Настя пока не нашла ссылку... Спроси по-другому! 🔍"
            await message.answer(answer)
            await _save_simple_exchange(message, text, answer, db)
        return

    # ── v44: URL UNDERSTANDING — Настя читает ссылки! ──
    url_context = ""
    urls = _URL_PATTERN.findall(text)
    if urls:
        await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
        for url in urls[:2]:  # Max 2 URLs per message
            content = await _fetch_url_content(url)
            if content:
                url_context += f"Настя прочитала эту ссылку: {url}. Содержание: {content[:1000]} "
            else:
                url_context += f"Пользователь скинул ссылку: {url}. Отреагируй и спроси что там интересного. "

    # ── Normal AI chat — MOST conversations go here ──
    # Detect if we're in a group chat (limit message length)
    chat_type = message.chat.type if message.chat else "private"
    is_group = chat_type in ("group", "supergroup")

    task = asyncio.create_task(
        _process_text_message(message, text, db, ai_router, is_group=is_group, url_context=url_context)
    )
    _user_processing[user_id] = task


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
                                 is_voice: bool = False, extra_suffix: str = "",
                                 extra_context: str = "", is_group: bool = False,
                                 url_context: str = "") -> None:
    """Process text with AI. ALWAYS responds — even if all providers fail.

    Enhanced with:
    - Pollinations PRIMARY + local model FALLBACK
    - Photo caption awareness via extra_context
    - News context injection into system prompt
    - Typing delay indicators for human-like feel
    - Group chat message length limiting
    - Channel invite for engaged users
    - News discussion with emotions
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

    _ai_start_time = time.time()

    # Get mood
    mood = "капризная"
    try:
        mood = await db.get_user_mood(user_id)
    except Exception:
        pass

    time_mood = _get_time_greeting()

    # Build user context
    user_context = f"Собеседник: {user_name}"
    if gender == "male":
        user_context += " (парень — флирти, называй по имени, шути, интересуйся им)."
    elif gender == "female":
        user_context += " (девушка — как подруга, делись новостями, обсуждай)."
    else:
        user_context += "."
    if msg_count > 20:
        user_context += " Старый знакомый — можно откровеннее!"
    elif msg_count > 5:
        user_context += " Уже общались — помни что говорили раньше."
    else:
        user_context += " Новый собеседник — познакомься поближе."

    # POLITICS FILTER
    political_keywords = ["путин", "зеленск", "байден", "трамп", "навальн", "войн",
                         "санкци", "нато", "политик", "депутат", "президент", "министр",
                         "религи", "конфликт", "террор", "бомб", "фашизм", "нацизм"]
    if any(kw in text.lower() for kw in political_keywords):
        user_context += " Вопрос про политику — переведи тему!"

    # Build system prompt
    system_prompt = NASTYA_SYSTEM_PROMPT + f" Настроение: {mood}. Время: {time_mood}."
    system_prompt += f" {user_context}"
    if extra_context:
        system_prompt += f" {extra_context}"
    # v44: URL context
    if url_context:
        system_prompt += f" {url_context}"

    # Group chat: shorter responses
    if is_group:
        system_prompt += " Мы в групповом чате — отвечай КОРОЧЕ, 1-2 предложения, без лишних деталей. Не пиши длинные сообщения."

    # News context
    _current_news_items = []
    try:
        recent_news = await db.get_recent_news_with_links(limit=3, max_age_hours=12)
        if recent_news:
            news_parts = []
            for item in recent_news[:3]:
                title = item.get("title", "")
                link = item.get("link", "")
                if title:
                    entry = title
                    if link:
                        entry += f" ({link})"
                    news_parts.append(entry)
            if news_parts:
                system_prompt += f" Свежие новости: {'; '.join(news_parts)}. Если спрашиваешь про событие — давай ссылку!"
        _current_news_items = recent_news
    except Exception:
        pass

    # History
    history = []
    try:
        history = await db.get_history(user_id, limit=MODEL_HISTORY_LIMIT)
    except Exception:
        pass

    # Web search
    search_query = should_search(text)
    search_results = []
    if search_query:
        try:
            search_results = await search_web(search_query, num_results=3)
            if search_results:
                search_parts = []
                for r in search_results[:2]:
                    title = r.get('title', '')
                    url = r.get('url', '')
                    snippet = r.get('snippet', '')[:100]
                    if title:
                        entry = f"{title}"
                        if snippet:
                            entry += f": {snippet}"
                        if url:
                            entry += f" [{url}]"
                        search_parts.append(entry)
                if search_parts:
                    system_prompt += f" Нашла в интернете: {'; '.join(search_parts)}. Обязательно добавь ссылку в ответ!"
                logger.info(f"Web search for user {user_id}: '{search_query}' → {len(search_results)} results")
        except Exception as e:
            logger.warning(f"Web search error: {e}")

    # Save user message
    prefix = "[Голосовое] " if is_voice else ""
    try:
        await db.add_message(user_id, "user", f"{prefix}{text}")
    except Exception:
        pass

    history_with_current = history + [{"role": "user", "content": f"{prefix}{text}"}]

    # ── TYPING DELAY INDICATOR ──
    # Send a human-like delay phrase in a background task
    delay_task = asyncio.create_task(
        _send_typing_delay(message, delay_seconds=5.0)
    )

    # Call AI
    try:
        result = await ai_router.chat(
            prompt=text, system_prompt=system_prompt, messages=history_with_current,
        )
        response_text = _clean_response(result.text)

    except Exception as e:
        logger.error(f"Chat error for user={user_id}: {e}")
        response_text = ai_router.get_fallback_response()
    finally:
        # Cancel delay task if still running
        delay_task.cancel()

    # ── POST-PROCESS: Filter political content ──
    political_filter_words = ["путин", "зеленск", "байден", "трамп", "навальн", "войн",
                              "спецопер", "санкци", "нато", "бомб", "обстрел", "террор",
                              "фашизм", "нацизм", "депутат", "госдум", "едро"]
    if any(kw in response_text.lower() for kw in political_filter_words):
        response_text = random.choice([
            f"Ой, Настя не про политику! Давай лучше про кино? 🎬💅",
            f"Ой, не хочу про это! Давай лучше про шопинг? 🛍️✨",
            f"Настя аполитична! Давай про что-нибудь весёлое? 💅💕",
            f"Это не ко мне! Давай лучше про технологии? 💻💅",
            f"Ой, давай не про политику! Какой сериал ты смотришь? 📺✨",
        ])
        logger.info(f"Filtered political content in response for user {user_id}")

    # ── POST-PROCESS: Channel awareness ──
    channel_keywords_in_user = ["канал", "подписк", "ссылк", "насти", "твой"]
    if any(k in text.lower() for k in channel_keywords_in_user):
        if not any(k in response_text.lower() for k in ["chasnastya", "t.me/chasnastya"]):
            response_text += f"\n\nКстати, мой канал: @chasnastya 👉 https://t.me/chasnastya 💅"
    if "не могу поделиться" in response_text.lower() or "не могу дать" in response_text.lower() or "у меня нет канала" in response_text.lower():
        response_text = f"Конечно! Мой канал @chasnastya 💅✨\n👉 https://t.me/chasnastya"

    # ── POST-PROCESS: News links ──
    response_text = _enforce_news_links(response_text, _current_news_items)

    # ── POST-PROCESS: Web search links ──
    if search_results and not re.search(r'https?://\S+', response_text):
        search_link = get_search_link_for_response(search_results)
        if search_link:
            response_text += f"\n\n🔗 {search_link}"

    # ── GROUP CHAT: Limit response length ──
    if is_group and len(response_text) > 300:
        # Cut at sentence boundary for group chats
        for sep in ['. ', '! ', '? ', '\n']:
            idx = response_text[:300].rfind(sep)
            if idx > 100:
                response_text = response_text[:idx + len(sep)].strip()
                break
        else:
            response_text = response_text[:300]

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

    # Maybe ask for stars
    should_stars = False
    stars_want = ""
    elapsed = time.time() - _ai_start_time
    if elapsed < 10:
        should_stars, stars_want = await _maybe_ask_stars_check(user_id, msg_count, db, message)

    # Update proactive tracker
    _proactive_tracker[user_id] = {
        "last_proactive": _proactive_tracker.get(user_id, {}).get("last_proactive", 0),
        "chat_id": message.chat.id,
    }

    # Send response — SMART SPLITTING
    try:
        full_response = response_text + channel_invite
        parts = _smart_split_message(full_response, max_len=4096)
        for i, part in enumerate(parts):
            if i > 0:
                await asyncio.sleep(0.1)
            await message.answer(part)
    except Exception as e:
        logger.error(f"Failed to send response: {e}")

    # Send Stars ask
    if should_stars and stars_want:
        try:
            await _ask_for_stars(message.chat.id, user_id, message.bot, stars_want)
        except Exception:
            pass

    # Clear processing task
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
    """Post-process AI response to add news links — ONLY when specifically relevant."""
    if not news_items or not response_text:
        return response_text

    response_lower = response_text.lower()

    if re.search(r'https?://\S+', response_text) or 't.me/' in response_lower:
        return response_text

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
            if match_count >= min(3, len(title_words)) and match_count > best_match_count:
                matched_news = item
                best_match_count = match_count

    if matched_news and matched_news.get("link"):
        if matched_news["link"] not in response_text:
            response_text += f"\n\n🔗 {matched_news['link']}"

    return response_text


def _smart_split_message(text: str, max_len: int = 4096) -> list:
    """Умное разбиение длинного текста на части для Telegram."""
    if not text:
        return []
    if len(text) <= max_len:
        return [text]
    
    parts = []
    remaining = text
    
    while remaining:
        if len(remaining) <= max_len:
            parts.append(remaining)
            break
        
        chunk = remaining[:max_len]
        split_pos = -1
        
        pos = chunk.rfind('\n\n')
        if pos > max_len * 0.3:
            split_pos = pos + 2
        else:
            pos = chunk.rfind('\n')
            if pos > max_len * 0.3:
                split_pos = pos + 1
            else:
                for sep in ['. ', '! ', '? ', '... ']:
                    pos = chunk.rfind(sep)
                    if pos > max_len * 0.3:
                        split_pos = pos + len(sep)
                        break
                else:
                    pos = chunk.rfind(' ')
                    if pos > max_len * 0.3:
                        split_pos = pos + 1
                    else:
                        split_pos = max_len
        
        part = remaining[:split_pos].strip()
        if part:
            parts.append(part)
        remaining = remaining[split_pos:].strip()
    
    return parts


def _clean_response(text: str) -> str:
    if not text:
        return "Ммм... Настя задумалась... 🤔"

    from ai.router import AIRouter
    text = AIRouter.clean_ai_response(text)

    if not text:
        return "Ммм... Настя задумалась... 🤔"

    # Forbidden words
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

    # Strip AI disclaimers
    text = re.sub(r'(?:как AI|как искусственный интеллект|я AI|я бот|я программа|я нейросеть)[^.]*\.', '', text, flags=re.IGNORECASE)

    # Strip generic AI intros
    ai_intros = [
        r'давай уточни[^.]*\.',
        r'раз уж ты[^.]*\.',
        r'я могу помочь[^.]*\.',
    ]
    for pattern in ai_intros:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)

    # Truncate extremely long responses
    if len(text) > 2500:
        for sep in ['. ', '! ', '? ', '\n']:
            idx = text[:2500].rfind(sep)
            if idx > 300:
                text = text[:idx + len(sep)].strip()
                break
        else:
            text = text[:2500]

    # FAKE LINK FILTER
    _real_channel_url = f"t.me/{CHANNEL_USERNAME.replace('@', '')}" if CHANNEL_USERNAME else "t.me/chasnastya"
    _real_patterns = [
        r'https?://t\.me/' + re.escape(CHANNEL_USERNAME.replace('@', '')) if CHANNEL_USERNAME else r'',
        r'https?://sochiautoparts\.ru',
        r'https?://rbc\.ru', r'https?://ria\.ru', r'https?://interfax\.ru',
        r'https?://habr\.com', r'https?://bbc\.com', r'https?://dw\.com',
        r'https?://meduza\.io', r'https?://ixbt\.com', r'https?://3dnews\.ru',
        r'https?://dtf\.ru', r'https?://pikabu\.ru', r'https?://nplus1\.ru',
    ]
    url_pattern = r'https?://[^\s<>\)\]"\']+|t\.me/[^\s<>\)\]"\']+'
    found_urls = re.findall(url_pattern, text)
    for url in found_urls:
        is_real = False
        for real_pattern in _real_patterns:
            if real_pattern and re.search(real_pattern, url, re.IGNORECASE):
                is_real = True
                break
        if not is_real:
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
                if any(d in parsed_netloc for d in known_domains):
                    if 't.me/' in url:
                        if _real_channel_url in url:
                            is_real = True
                    else:
                        is_real = True
            except Exception:
                pass
        if not is_real:
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
#  PROACTIVE MESSAGES — NEWS AWARE + TIME AWARE + EMOTIONAL
# ════════════════════════════════════════════════════════════

async def check_and_send_proactive(bot, db, ai_router) -> None:
    """Send proactive messages to users who haven't chatted recently.

    Enhanced: More diverse messages, news discussion with emotions,
    time-aware content, more human-like behavior.
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
            # 50% news-based, 50% regular proactive (MORE NEWS!)
            if random.random() < 0.5 and db:
                recent = await db.get_recent_news(limit=1, max_age_hours=6)
                if recent and recent[0].get("nastya_comment"):
                    from channel import get_news_discussion
                    msg = get_news_discussion(recent[0]["nastya_comment"])
                    # Add emotional context for news
                    title = recent[0].get("title", "")
                    if title:
                        emotion = random.choice(NASTYA_VOCABULARY["emotion"])
                        msg = f"{emotion} {title}! {msg}"
                else:
                    msg = random.choice(PROACTIVE_MESSAGES)
            else:
                msg = random.choice(PROACTIVE_MESSAGES)

            chat_id = pro.get("chat_id", user_id)
            proactive_text = msg

            # 30% chance to include channel invite
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
    logger.info(f"Poll vote: user={poll_answer.user.id}, options={poll_answer.option_ids}")
