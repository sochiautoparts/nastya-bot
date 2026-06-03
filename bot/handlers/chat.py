"""Nastya Chat Handler v14.0 — MULTI-MODEL + VISION + HUMAN-LIKE + URL + INLINE + MULTI-ENGINE SEARCH!

v14.0: MULTI-ENGINE SEARCH — /find ВСЕГДА находит!
  - 4 поисковых движка: DuckDuckGo → Yandex → SearXNG → DDG API
  - FORCE web search when user asks for products/services/links
  - Detect AI-hallucinated commercial URLs and replace with real search results
  - Commercial site URLs (ozon, wildberries, yandex.market, etc.) in AI response
    are REMOVED if they were NOT in the search results — they're hallucinations!
  - Only real URLs from search results are kept in the response

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
    LabeledPrice, PollAnswer,
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

# v51: Product/service/link request detection — FORCE web search!
_PRODUCT_LINK_TRIGGERS = [
    "дай ссылк", "скинь ссылк", "найди ссылк", "где купить", "где найти",
    "дай ссылку", "скинь ссылку", "найди ссылку",
    "ссылку на", "ссылки на", "вариант", "три варианта", "несколько вариантов",
    "турник", "гантел", "бегов", "велотренажёр", "велотренажер",
    "купить", "заказать", "цена", "стоимость", "подешевле",
    "лучший", "топ", "рейтинг", "отзыв",
    "ozon", "wildberries", "яндекс маркет", "маркет",
    "aliexpress", "amazon",
]

# v51: Known Russian commercial/marketplace domains — URLs from these domains
# in AI responses are likely HALLUCINATED if not in search results!
_COMMERCIAL_DOMAINS = [
    "wildberries.ru", "ozon.ru", "market.yandex.ru", "beru.ru",
    "aliexpress.ru", "lamoda.ru", "wildberries.com",
    "dns-shop.ru", "citilink.ru", "mvideo.ru", "eldorado.ru",
    "sportsmaster.ru", "decathlon.ru", "vseinstrumenti.ru",
    "onlinetrade.ru", "ulmart.ru", "sbermegamarket.ru",
    "avito.ru", "youla.ru", "yandex.uz",
]

# v51: Extract search query for product search
_PRODUCT_SEARCH_PREFIXES = [
    "найди", "поищи", "ищу", "дай", "скинь", "где ", "какой ",
    "подскажи", "посоветуй", "рекомендуй", "выбери", "какие ",
]

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
    # v46: Discovery-aware — sharing found information
    "Настя тут кое-что интересное нашла в интернете! Спроси про что! 🔍✨",
    "Прикинь, какой гороскоп сегодня! Точняк совпадает! Спроси свой знак! 🔮💅",
    "О, я рецепт классный нашла! Настя уже пускает слюнки! 🍳😍",
    "Котятки, я про одно мероприятие узнала — круть! Спроси! 🎫✨",
    "Настя нашла скидки! Реально крутые! Спроси на что! 🛍️💰",
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

# NOTE: Inline mode is handled in bot/handlers/inline.py — dedicated handler with caching!


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
    extras.append("🔍 /find — найти товар, лучшую цену!")
    extras.append("🍳 /recipe — рецепт от Насти!")
    extras.append("🔮 /horoscope — гороскоп на сегодня")
    extras.append("🔢 /numerology — число судьбы")
    if CHANNEL_USERNAME:
        extras.append(f"📺 Мой канал: t.me/{CHANNEL_USERNAME.replace('@', '')}")

    greeting_text += "\n\n" + "\n".join(extras)

    await message.answer(greeting_text)
    # NOTE: Stars invoice only on /donates command — not on /start!


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

    await message.answer("\n".join(lines))

    if db:
        await _save_simple_exchange(message, f"/search {query}", "\n".join(lines[:5]), db)


# ── /find — Product/Service/Price search with links ──

@router.message(Command("find"))
async def cmd_find(message: Message, db=None, ai_router=None) -> None:
    """Search for products, services, best prices with links.

    Usage: /find айфон 15 про / /find ремонт авто Москва
    """
    from bot.discover import search_products, format_product_results

    query = message.text.replace("/find", "").strip()
    if not query:
        await message.answer(
            "Настя найдёт товар, услугу или лучшую цену! 🔍💰\n\n"
            "Примеры:\n"
            "/find айфон 15 про\n"
            "/find ремонт авто Москва\n"
            "/find ноутбук для работы\n"
            "/find доставка суши Сочи"
        )
        return

    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    await message.answer("Настя ищет лучшие варианты! Секунду... 🔍💰")

    results = await search_products(query, num_results=5)

    if not results:
        await message.answer(f"Ой, Настя не нашла '{query}'... 😔 Попробуй другой запрос!")
        return

    response = format_product_results(results, query)
    await message.answer(response)

    if db:
        await _save_simple_exchange(message, f"/find {query}", response[:200], db)


# ── /horoscope — Daily horoscope ──

@router.message(Command("horoscope"))
async def cmd_horoscope(message: Message, db=None, ai_router=None) -> None:
    """Get today's horoscope for a zodiac sign."""
    from bot.nastya import get_zodiac_info, ZODIAC_SIGNS

    query = message.text.replace("/horoscope", "").strip().lower()
    if not query:
        signs_list = ", ".join(ZODIAC_SIGNS.keys())
        await message.answer(
            f"Напиши свой знак зодиака! ♊✨\n\n"
            f"Пример: /horoscope близнецы\n\n"
            f"Знаки: {signs_list}"
        )
        return

    zodiac = get_zodiac_info(query)
    sign_emoji = zodiac["emoji"] if zodiac else "✨"
    sign_name = query.capitalize()

    if not ai_router:
        await message.answer(f"{sign_emoji} Гороскоп для {sign_name}... Настя пока не может заглянуть в звёзды! 🔮")
        return

    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    try:
        result = await ai_router.chat(
            prompt=f"Напиши гороскоп на сегодня для знака {sign_name}. Что ждёт в любви, работе, здоровье. Дай конкретные советы.",
            system_prompt=(
                "Ты Настя — москвичка, 23 года, блогер, увлекаешься астрологией. "
                "Пиши гороскоп живо и эмоционально, как подружка-астролог. "
                "4-6 предложений. Без markdown, без буллетов. "
                "Используй слова: 'прикинь', 'офигеть', 'капец', 'круто'. "
                "Будь позитивной но честной!"
            ),
            max_tokens=400,
        )
        if result and result.text:
            from ai.router import AIRouter
            cleaned = AIRouter.clean_ai_response(result.text)
            if cleaned:
                await message.answer(f"{sign_emoji} Гороскоп для {sign_name}:\n\n{cleaned}")
                if db:
                    await _save_simple_exchange(message, f"/horoscope {query}", cleaned, db)
                return
    except Exception as e:
        logger.error(f"Horoscope error: {e}")

    await message.answer(f"{sign_emoji} Настя не смогла прочитать звёзды... Попробуй позже! 🔮💅")


# ── /recipe — Find a recipe ──

@router.message(Command("recipe"))
async def cmd_recipe(message: Message, db=None, ai_router=None) -> None:
    """Find a recipe by query."""
    query = message.text.replace("/recipe", "").strip()
    if not query:
        query = random.choice(["быстрый ужин", "вкусный десерт", "праздничный салат", "завтрак за 10 минут"])

    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    await message.answer(f"Настя ищет рецепт '{query}'! 🍳✨")

    results = await search_web(f"рецепт {query} пошагово", num_results=3)
    if not results:
        await message.answer("Ой, рецепт не нашла... Попробуй по-другому! 🍳😔")
        return

    # Get best result and ask AI to format the recipe
    best = results[0]
    snippet = best.get("snippet", "")
    url = best.get("url", "")

    if ai_router:
        try:
            result = await ai_router.chat(
                prompt=f"Найден рецепт: {snippet}\n\nНапиши подробный рецепт с ингредиентами и пошаговым приготовлением.",
                system_prompt=(
                    "Ты Настя — москвичка, 23 года, блогер, любишь готовить. "
                    "Пиши рецепт подробно: ингредиенты, пошаговое приготовление, советы. "
                    "6-10 предложений. Без markdown, без буллетов — сплошной текст. "
                    "Говори живо: 'прикинь', 'капец', 'круто'."
                ),
                max_tokens=500,
            )
            if result and result.text:
                from ai.router import AIRouter
                cleaned = AIRouter.clean_ai_response(result.text)
                if cleaned:
                    response = f"🍳 Рецепт: {query}\n\n{cleaned}"
                    if url:
                        response += f"\n\n🔗 Источник: {url}"
                    await message.answer(response)
                    if db:
                        await _save_simple_exchange(message, f"/recipe {query}", cleaned[:200], db)
                    return
        except Exception as e:
            logger.error(f"Recipe error: {e}")

    # Fallback: just show search results
    lines = [f"🍳 Рецепт '{query}':\n"]
    lines.append(f"{snippet}")
    if url:
        lines.append(f"\n🔗 {url}")
    await message.answer("\n".join(lines))


# ── /numerology — Numerology by date ──

@router.message(Command("numerology"))
async def cmd_numerology(message: Message, db=None, ai_router=None) -> None:
    """Calculate numerology from a date or number."""
    from bot.nastya import calculate_numerology

    query = message.text.replace("/numerology", "").strip()
    if not query:
        await message.answer(
            "Напиши дату рождения или число для нумерологии! 🔢✨\n\n"
            "Пример: /numerology 15.06.2001"
        )
        return

    result = calculate_numerology(query)
    number = result.get("number", 0)
    meaning = result.get("meaning", "Что-то загадочное...")

    # Enhance with AI
    if ai_router:
        try:
            ai_result = await ai_router.chat(
                prompt=f"Число судьбы: {number}. Значение: {meaning}. Распиши подробнее что значит число {number} в нумерологии.",
                system_prompt=(
                    "Ты Настя — москвичка, 23 года, блогер, увлекаешься нумерологией. "
                    "Расскажи подробно и интересно. 4-6 предложений. Без markdown. "
                    "Говори живо: 'прикинь', 'офигеть', 'круто'."
                ),
                max_tokens=300,
            )
            if ai_result and ai_result.text:
                from ai.router import AIRouter
                cleaned = AIRouter.clean_ai_response(ai_result.text)
                if cleaned:
                    await message.answer(f"🔢 Число судьбы: {number}\n\n{cleaned}")
                    if db:
                        await _save_simple_exchange(message, f"/numerology {query}", cleaned[:200], db)
                    return
        except Exception:
            pass

    await message.answer(f"🔢 Число судьбы: {number}\n\n{meaning}")


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

                    # v46: After vision, do product/info search if search keywords in caption
                    if is_search_photo:
                        try:
                            # Extract key terms from vision response for web search
                            search_query = cleaned[:100].replace("\n", " ")
                            # Clean up for search
                            search_query = re.sub(r'[^\w\s]', ' ', search_query).strip()
                            if len(search_query) > 5:
                                from bot.discover import search_products, format_product_results
                                product_results = await search_products(search_query, num_results=3)
                                if product_results:
                                    product_text = format_product_results(product_results, search_query)
                                    await message.answer(f"🔍 Настя нашла по фото:\n\n{product_text}")
                        except Exception as e:
                            logger.error(f"Photo product search error: {e}")

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

    # ── GROUP CHAT: Decide whether to respond ──
    chat_type = message.chat.type if message.chat else "private"
    is_group = chat_type in ("group", "supergroup")

    if is_group:
        # In groups: Always respond if mentioned or if bot username is in text
        is_mentioned = f"@{BOT_USERNAME}" in text_lower if BOT_USERNAME else False
        is_reply_to_bot = False
        if message.reply_to_message and message.reply_to_message.from_user:
            is_reply_to_bot = message.reply_to_message.from_user.username == BOT_USERNAME.replace("@", "")

        # Keywords that trigger Nastya's interest in groups
        interest_keywords = ["настя", "насть", "насти", "настю", "настёна",
                            "девушк", "красив", "рецепт", "гороскоп", "совет",
                            "авто", "запчас", "сочи", "погода", "новости",
                            "скидк", "покупк", "факт", "интересн"]
        has_interest = any(kw in text_lower for kw in interest_keywords)

        # Decide: respond if mentioned/replied, or by probability for interesting content
        should_respond = is_mentioned or is_reply_to_bot or has_interest
        if not should_respond:
            # Random chance to comment even without being mentioned — Nastya is active in groups!
            if random.random() < GROUP_RESPONSE_CHANCE:
                should_respond = True
            else:
                return  # Skip this group message

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

    # ── "Дай ссылку" — search the web for real links, NOT channel link! ──
    if any(t in text_lower for t in ["дай ссылку", "скинь ссылку", "ссылку дай", "где ссылк", "где прочитать", "где посмотреть", "источник", "почему не можешь"]):
        found_link = False
        # Step 1: Try to find a relevant link from recent news
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
                            answer = f"Вот, держи! 💅\n🔗 {link}"
                            await message.answer(answer)
                            await _save_simple_exchange(message, text, answer, db)
                            found_link = True
                            break
        except Exception:
            pass

        # Step 2: If no news link found, search the web for relevant links
        if not found_link:
            # Extract search query from recent conversation context
            search_query = ""
            try:
                user_history = await db.get_history(message.from_user.id, limit=5)
                for m in reversed(user_history[-3:]):
                    content = m.get("content", "")
                    if content and not content.startswith("["):
                        search_query = content[:100]
                        break
            except Exception:
                pass

            if search_query and len(search_query) > 3:
                results = await search_web(search_query, num_results=2)
                if results:
                    best = results[0]
                    url = best.get("url", "")
                    title = best.get("title", "")
                    if url:
                        answer = f"Настя нашла! 💅\n🔗 {url}"
                        if title:
                            answer = f"Настя нашла! 💅\n📖 {title}\n🔗 {url}"
                        await message.answer(answer)
                        await _save_simple_exchange(message, text, answer, db)
                        found_link = True

        # Step 3: Only mention channel as LAST resort if explicitly about channel
        if not found_link:
            # Check if the user was specifically asking about the channel
            channel_specific = any(k in text_lower for k in ["канал", "насти", "настя", "подписк", "chasnastya"])
            if channel_specific and CHANNEL_USERNAME:
                answer = f"Мой канал @chasnastya! Там всё самое интересное! 💅✨\n👉 https://t.me/{CHANNEL_USERNAME.replace('@', '')}"
            else:
                # Generic request — tell user we couldn't find, offer to search
                answer = "Настя не нашла ссылку на это... Попробуй /search и я поищу в интернете! 🔍"
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
    # is_group is already computed above for group chat handling

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

    # Group chat: active participation
    if is_group:
        system_prompt += " Мы в групповом чате — Настя активная участница! Отвечай живо и с интересом, 2-4 предложения. Можешь шутить, обсуждать, реагировать."

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

    # ── Web search — ENHANCED v51: Force search for product/link requests! ──
    search_query = should_search(text)
    search_results = []
    is_product_search = False  # Track if this is a product/service search

    # v51: Detect product/service/link requests → FORCE web search!
    if not search_query:
        text_lower = text.lower()
        for trigger in _PRODUCT_LINK_TRIGGERS:
            if trigger in text_lower:
                # Extract a search query from the user's message
                search_query = text[:150]
                # Try to clean up the search query
                for prefix in _PRODUCT_SEARCH_PREFIXES:
                    if text_lower.startswith(prefix):
                        search_query = text[len(prefix):].strip()[:100]
                        break
                is_product_search = True
                break

    if search_query:
        try:
            # v51: More results for product searches (5 instead of 3)
            num_results = 5 if is_product_search else 3
            search_results = await search_web(search_query, num_results=num_results)
            if search_results:
                search_parts = []
                for r in search_results[:4 if is_product_search else 2]:
                    title = r.get('title', '')
                    url = r.get('url', '')
                    snippet = r.get('snippet', '')[:150 if is_product_search else 100]
                    if title:
                        entry = f"{title}"
                        if snippet:
                            entry += f": {snippet}"
                        if url:
                            entry += f" [Ссылка: {url}]"
                        search_parts.append(entry)
                if search_parts:
                    if is_product_search:
                        system_prompt += (
                            f"\n\n🔍 НАСТЯ НАШЛА В ИНТЕРНЕТЕ (ОБЯЗАТЕЛЬНО используй ТОЛЬКО эти данные и URL!):\n"
                            + "\n".join(search_parts)
                            + "\n\n⛔ КРИТИЧЕСКИ ВАЖНО:"
                            + "\n1. Используй ТОЛЬКО ссылки из результатов поиска выше! Буквально копируй URL!"
                            + "\n2. НЕ придумывай ссылки — если ссылки нет в результатах, НЕ пиши её!"
                            + "\n3. НЕ меняй путь URL — копируй ССЫЛКУ ТОЧНО как в результатах!"
                            + "\n4. Если результатов недостаточно — скажи что нашла не всё и предложи поискать ещё"
                            + "\n5. НЕ заменяй реальные ссылки на @chasnastya!"
                            + "\n6. НЕ добавляй выдуманные пути типа /catalog/product/12345 — это ВСЕГДА выдумка!"
                            + "\n7. Каждый товар/услугу сопровождай ТОЧНОЙ ссылкой из результатов поиска"
                        )
                    else:
                        system_prompt += f"\n\n🔍 НАСТЯ НАШЛА В ИНТЕРНЕТЕ (ОБЯЗАТЕЛЬНО используй эти данные и URL в ответе!):\n" + "\n".join(search_parts) + "\n\n⚠️ ВАЖНО: Включи ВСЕ найденные URL в свой ответ! НЕ заменяй их на @chasnastya!"
                logger.info(f"Web search for user {user_id}: '{search_query}' → {len(search_results)} results (product={is_product_search})")
            elif is_product_search:
                # v51: If product search found nothing, tell AI to be honest
                system_prompt += (
                    "\n\n⚠️ Настя искала товары/услуги в интернете но НЕ НАШЛА результатов."
                    "\nНЕ придумывай ссылки! Скажи честно что не нашла и предложи поискать через /find"
                )
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

    # ── POST-PROCESS: Channel awareness — ONLY when specifically about channel ──
    channel_keywords_in_user = ["канал", "подписк", "ссылк на канал", "насти канал", "твой канал"]
    if any(k in text.lower() for k in channel_keywords_in_user):
        # Only add channel link if the user was explicitly asking about the channel
        # NOT when they're asking for links to other things
        is_asking_about_channel = any(phrase in text.lower() for phrase in 
            ["твой канал", "насти канал", "какой канал", "где канал", "подписаться на канал", "ссылка на канал"])
        if is_asking_about_channel and not any(k in response_text.lower() for k in ["chasnastya", "t.me/chasnastya"]):
            response_text += f"\n\nМой канал: @chasnastya 👉 https://t.me/chasnastya 💅"
    # Fix incorrect responses where Nastya says she can't share links
    if "не могу поделиться" in response_text.lower() or "не могу дать" in response_text.lower():
        # Replace with an offer to search instead
        response_text = response_text.replace("не могу поделиться", "могу поискать").replace("не могу дать", "могу найти")
    if "у меня нет канала" in response_text.lower():
        response_text = f"Конечно! Мой канал @chasnastya 💅✨\n👉 https://t.me/chasnastya"

    # ── POST-PROCESS: News links ──
    response_text = _enforce_news_links(response_text, _current_news_items)

    # ── POST-PROCESS: Web search links ──
    # v51: ENHANCED — detect and remove hallucinated commercial URLs!
    search_result_urls = set()
    for r in search_results:
        url = r.get('url', '')
        if url:
            search_result_urls.add(url.lower().rstrip('/'))
            # Also add domain-level match for path variations
            try:
                from urllib.parse import urlparse
                parsed = urlparse(url)
                search_result_urls.add(parsed.netloc.lower())
            except Exception:
                pass

    if search_results:
        response_text = _remove_hallucinated_urls(response_text, search_result_urls, is_product_search=is_product_search)

    # Add search result link if no URLs remain after cleanup
    if search_results and not re.search(r'https?://\S+', response_text):
        # Append ALL search result URLs when AI didn't include any
        search_links = []
        for r in search_results[:3]:
            url = r.get('url', '')
            title = r.get('title', '')
            if url:
                link_text = f"🔗 {url}"
                if title:
                    link_text = f"• {title}\n  🔗 {url}"
                search_links.append(link_text)
        if search_links:
            response_text += "\n\n" + "\n".join(search_links)

    # ── GROUP CHAT: Limit response length ──
    if is_group and len(response_text) > 600:
        # Cut at sentence boundary for group chats
        for sep in ['. ', '! ', '? ', '\n']:
            idx = response_text[:600].rfind(sep)
            if idx > 100:
                response_text = response_text[:idx + len(sep)].strip()
                break
        else:
            response_text = response_text[:600]

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
    """Post-process AI response to add news links — ONLY when specifically relevant.
    
    v50: Fixed — don't skip adding links just because t.me/ is in the response.
    The channel link t.me/chasnastya is NOT a news link.
    """
    if not news_items or not response_text:
        return response_text

    response_lower = response_text.lower()

    # v50: Only skip if the response already has real external links (not just channel link)
    _channel_url = f"t.me/{CHANNEL_USERNAME.replace('@', '')}" if CHANNEL_USERNAME else "t.me/chasnastya"
    external_links = re.findall(r'https?://\S+', response_text)
    # Filter out the channel link — it's not a news/product link
    real_external_links = [l for l in external_links if _channel_url not in l.lower()]
    if real_external_links:
        return response_text  # Already has real links

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


def _remove_hallucinated_urls(text: str, search_result_urls: set, is_product_search: bool = False) -> str:
    """v52: Remove AI-hallucinated URLs from response.

    When a user asks for products/services/links, the AI often generates
    plausible-looking but FAKE URLs. These URLs don't actually work!

    v52 ENHANCED:
    - For product searches: remove ALL URLs not in search results (not just commercial domains)
    - For regular searches: only remove URLs from known commercial domains not in results
    - Clean up leftover text artifacts after URL removal
    """
    if not text or not search_result_urls:
        return text

    url_pattern = r'https?://([^\s<>)\]"\']+)'
    found_urls = re.findall(url_pattern, text)

    # Build set of full search result URLs (normalized) and domains
    search_urls_normalized = set()
    search_domains = set()
    for url_or_domain in search_result_urls:
        normalized = url_or_domain.lower().rstrip('/')
        search_urls_normalized.add(normalized)
        try:
            from urllib.parse import urlparse
            if '/' in url_or_domain:
                parsed = urlparse(url_or_domain if url_or_domain.startswith('http') else f'https://{url_or_domain}')
                search_domains.add(parsed.netloc.lower())
            else:
                search_domains.add(url_or_domain.lower())
        except Exception:
            search_domains.add(url_or_domain.split('/')[0].lower())

    # For product searches: also build partial URL matches for more flexible matching
    search_url_paths = set()
    for url_or_domain in search_result_urls:
        try:
            from urllib.parse import urlparse
            if url_or_domain.startswith('http'):
                parsed = urlparse(url_or_domain)
                # Store domain + path (without query params) for partial matching
                search_url_paths.add(f"{parsed.netloc.lower()}{parsed.path.lower().rstrip('/')}")
        except Exception:
            pass

    # Find and remove hallucinated URLs
    hallucinated_count = 0
    for full_url in found_urls:
        try:
            from urllib.parse import urlparse
            if not full_url.startswith('http'):
                full_url_for_parse = f'https://{full_url}'
            else:
                full_url_for_parse = full_url
            parsed = urlparse(full_url_for_parse)
            domain = parsed.netloc.lower()
            url_path = f"{domain}{parsed.path.lower().rstrip('/')}"
            normalized_url = full_url.lower().rstrip('/')

            # Check if URL was in search results (exact or partial match)
            url_in_results = (
                normalized_url in search_urls_normalized
                or any(normalized_url in sr for sr in search_urls_normalized)
                or any(sr in normalized_url for sr in search_urls_normalized)
                or url_path in search_url_paths
                or domain in search_domains  # Domain was in search results
            )

            should_remove = False
            if is_product_search:
                # For product searches: remove ALL URLs not from search results
                # This is aggressive because AI-hallucinated product URLs are never real
                if not url_in_results:
                    should_remove = True
            else:
                # For regular searches: only remove URLs from commercial domains not in results
                is_commercial = any(comm_domain in domain for comm_domain in _COMMERCIAL_DOMAINS)
                if is_commercial and not url_in_results:
                    should_remove = True

            if should_remove:
                # Remove the URL from text
                url_variants = [
                    f'https://{full_url}',
                    f'http://{full_url}',
                    full_url,
                ]
                for variant in url_variants:
                    if variant in text:
                        text = text.replace(variant, '')
                        hallucinated_count += 1
                        break
        except Exception:
            pass

    if hallucinated_count > 0:
        logger.info(f"Removed {hallucinated_count} hallucinated URLs from response (product_search={is_product_search})")

        # Clean up leftover artifacts after URL removal
        text = re.sub(r'\s*(?:Ссылк[аиу]:?\s*)?\[?\s*\]?\s*$', '', text, flags=re.IGNORECASE | re.MULTILINE)
        text = re.sub(r'\s*(?:Ссылк[аиу]:?\s*)$', '', text, flags=re.IGNORECASE | re.MULTILINE)
        text = re.sub(r'\s*🔗\s*$', '', text, flags=re.MULTILINE)
        text = re.sub(r'\[\s*\]', '', text)  # Empty brackets
        text = re.sub(r'—\s*$', '', text, flags=re.MULTILINE)  # Trailing dashes
        text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


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
    if len(text) > 4000:
        for sep in ['. ', '! ', '? ', '\n']:
            idx = text[:4000].rfind(sep)
            if idx > 300:
                text = text[:idx + len(sep)].strip()
                break
        else:
            text = text[:4000]

    # ── SMART LINK PROTECTION v50 ──
    # Only filter OBVIOUSLY FAKE/AI-HALLUCINATED URLs.
    # Real product links (ozon, wildberries, yandex, etc.) must NEVER be replaced!
    # The AI sometimes hallucinates URLs like https://example.com/product/123
    # Those should be removed. Real URLs from search results must stay.
    _real_channel_url = f"t.me/{CHANNEL_USERNAME.replace('@', '')}" if CHANNEL_USERNAME else "t.me/chasnastya"

    # Only block OBVIOUSLY FAKE URLs
    _fake_url_patterns = [
        r'https?://example\.(com|org|net)/',    # Placeholder URLs
        r'https?://localhost',                    # Local dev URLs
        r'https?://(www\.)?sample\.',           # Sample URLs
        r'https?://(www\.)?test\.',             # Test URLs
        r'https?://(www\.)?fake\.',             # Fake URLs
        r'https?://(www\.)?dummy\.',            # Dummy URLs
        r'https?://(www\.)?placeholder\.',      # Placeholder URLs
        r'https?://your-?site\.',               # Template URLs
        r'https?://ссылка',                       # Russian word "ссылка" as URL
        r'https?://товар',                        # Russian word "товар" as URL
    ]

    url_pattern = r'https?://[^\s<>\)\]"\']+'
    found_urls = re.findall(url_pattern, text)
    for url in found_urls:
        is_fake = False
        for fake_pattern in _fake_url_patterns:
            if re.search(fake_pattern, url, re.IGNORECASE):
                is_fake = True
                break
        if is_fake:
            # Remove the fake URL — AI hallucinated it
            text = text.replace(url, "")
            # Clean up any trailing "Ссылка:", "🔗", etc. after removed URL
            text = re.sub(r'\s*(?:Ссылк[аиу]:?|🔗)\s*$', '', text, flags=re.IGNORECASE)
            text = re.sub(r'\s*(?:Ссылк[аиу]:?|🔗)\s*\n', '\n', text, flags=re.IGNORECASE)
            logger.info(f"Removed hallucinated URL: {url[:50]}")

    # ── Replace @chasnastya when AI used it as a PRODUCT link replacement ──
    # If AI wrote "Ссылка: @chasnastya" or "🔗 @chasnastya" after a product — remove it
    # (the channel link should only appear when specifically asked about the channel)
    # But KEEP @chasnastya when it's a natural mention or channel reference
    text = re.sub(r'\s*(?:Ссылк[аиу]:?\s*)?@chasnastya\s*(?=$)', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*🔗\s*@chasnastya\s*(?=$)', '', text, flags=re.IGNORECASE)
    # But keep @chasnastya when it's a natural mention (not a link replacement)

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
