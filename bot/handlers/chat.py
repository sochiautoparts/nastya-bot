"""Nastya Chat Handler v14.0 - MULTI-MODEL + VISION + HUMAN-LIKE + URL + INLINE + MULTI-ENGINE SEARCH!

v14.0: MULTI-ENGINE SEARCH - /find ВСЕГДА находит!
  - 4 поисковых движка: DuckDuckGo -> Yandex -> SearXNG -> DDG API
  - FORCE web search when user asks for products/services/links
  - Detect AI-hallucinated commercial URLs and replace with real search results
  - Commercial site URLs (ozon, wildberries, yandex.market, etc.) in AI response
    are REMOVED if they were NOT in the search results - they're hallucinations!
  - Only real URLs from search results are kept in the response

STABILITY RULES:
  - Bot ALWAYS responds, even if ALL AI providers fail (fallback responses)
  - NO error messages ever shown to user
  - Per-operation DB with write lock - safe for concurrent users
  - 30-day context memory + news context injection
  - Short, effective system prompt

INTELLIGENCE FEATURES v12.0 (MULTI-MODEL POLLINATIONS + HUMAN-LIKE + URL):
  - Pollinations.ai MULTI-MODEL - 8 VERIFIED models with load balancing!
  - Automatic failover: if one model fails, next one picks up
  - RuadaptQwen3-4B-Instruct local GGUF as LAST FALLBACK
  - REAL PHOTO UNDERSTANDING - Настя ВИДИТ что на фото!
  - PHOTO SEARCH - определение объектов на фото по запросу
  - URL UNDERSTANDING - Настя читает ссылки и понимает контекст!
  - INLINE MODE - Настя в любом чате через @bot_username
  - Typing delay indicators - Настя "живой" собеседник
  - Web search integration - Nastya can find and verify information!
  - News discussion with emotions - Настя рассказывает подробно!
  - Group chat message length limiting - короче в группах
  - Expanded proactive messaging - Настя активный собеседник
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
from bot.partners import nastya_partner_manager

logger = logging.getLogger(__name__)
router = Router()

# Per-user state
_stars_tracker: dict = {}
_proactive_tracker: dict = {}
_last_tracker_cleanup: float = 0.0
_TRACKER_CLEANUP_INTERVAL = 3600

# v44: URL detection regex
_URL_PATTERN = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+')

# v44: Photo search keywords - определение объектов на фото
_PHOTO_SEARCH_KEYWORDS = ["что это", "найди", "поиск", "что за", "определи", "узнай что это",
                          "что на фото", "что изображено", "распознай", "опознай"]

# v51: Product/service/link request detection - FORCE web search!
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

# v51: Known Russian commercial/marketplace domains - URLs from these domains
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

# v60: Consultation keyword detection - auto-detect consultation requests in regular chat
_CONSULTATION_KEYWORDS = {
    "humandesign": [
        "бодиграф", "дизайн человека", "дизайн челов", "bodygraph", "human design",
        "ворота", "каналы", "определённост", "определенност", "центры",
        "профиль", "авторитет", "стратегия", "тип энер",
        "проектор", "генератор", "манифестор", "рефлектор", "манифестирующий",
    ],
    "astro": [
        "натальн", "гороскоп", "асцендент", "знак зодиак",
        "планет", "дом астрол", "аспект", "транзит", "соляр", "прогресс",
    ],
    "numerology": [
        "матриц судьб", "матрицу судьб", "число жизнен", "кармическ",
        "число судьб", "пиковое числ", "нумеролог",
    ],
    "jyotish": [
        "джйотиш", "ведическая астрол", "накшатра", "даша", "лагна", "джанма",
    ],
    "health": [
        "доша", "аюрвед", "психосоматик", "конституци", "капха", "вата", "питта",
    ],
}

# Words that should NOT trigger consultation detection on their own (too ambiguous)
_CONSULTATION_FALSE_POSITIVE_WORDS = {
    "ворота",  # could mean physical gates
    "каналы",  # could mean TV channels
    "центры",  # could mean shopping centers
    "профиль",  # could mean social profile
    "стратегия",  # could mean business strategy
    "авторитет",  # could mean authority figure
    "прогресс",  # could mean general progress
    "аспект",  # could mean general aspect
    "транзит",  # could mean public transit
    "планет",  # without context could be vague, but usually astrology
}

# v42: Per-user message dedup - track ACTIVE AI tasks per user
_user_processing: dict = {}  # user_id -> asyncio.Task (active AI task) or None

# v59: Max chars for group/supergroup comments - keep it short!
GROUP_COMMENT_MAX_CHARS = 600


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


async def _send_long_message(message: Message, text: str, max_chars: int = 4000) -> None:
    """Send a long message, splitting it across multiple Telegram messages.
    
    Telegram limits single messages to ~4096 chars. This function splits
    at paragraph boundaries when possible and adds continuation markers.
    """
    if len(text) <= max_chars:
        await message.answer(text)
        return
    
    parts = []
    remaining = text
    while len(remaining) > max_chars:
        # Try to split at paragraph break
        split_at = remaining.rfind("\n\n", max_chars - 1000, max_chars)
        if split_at == -1:
            # Try single newline
            split_at = remaining.rfind("\n", max_chars - 1000, max_chars)
        if split_at == -1:
            # Try sentence boundary
            split_at = remaining.rfind(". ", max_chars - 1000, max_chars)
        if split_at == -1:
            # Force split
            split_at = max_chars - 100
        parts.append(remaining[:split_at + 1])
        remaining = remaining[split_at + 1:].lstrip("\n")
    
    if remaining:
        parts.append(remaining)
    
    total = len(parts)
    for i, part in enumerate(parts):
        try:
            # Add continuation marker for multi-part messages
            chunk = part.strip()
            if total > 1:
                if i == 0:
                    chunk = chunk + f"\n\n... ({total} частей, продолжение →)"
                elif i < total - 1:
                    chunk = f"(продолжение {i+1}/{total})\n\n" + chunk + f"\n\n... (ещё →)"
                else:
                    chunk = f"(окончание {i+1}/{total})\n\n" + chunk
            
            await message.answer(chunk)
            if i < total - 1:
                await asyncio.sleep(0.7)
        except Exception as e:
            logger.error(f"Failed to send message part {i}: {e}")
            # If part is too long, try harder to split
            if len(part) > 4096:
                # Emergency split
                for j in range(0, len(part), 3900):
                    try:
                        await message.answer(part[j:j+3900].strip())
                        await asyncio.sleep(0.5)
                    except Exception:
                        pass


# ════════════════════════════════════════════════════════════
#  URL UNDERSTANDING - Настя читает ссылки!
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
    """Get current Moscow time - Настя из Москвы!"""
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
    "Настин возраст - тайна, покрытая макияжем",
    "Настя настолько молода, насколько ей хочется сегодня",
    "возраст Насти измеряется не годами, а капризами",
    "Это секрет! Если скажу, придётся тебя поцеловать 💋",
    "Столько, чтобы нравиться, но чтобы не приставали 😏",
    "Возраст - это просто цифра. А Настя - это явление! 💅✨",
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
    "Настя требует {want}! Звёздочки - быстро! 😤⭐",
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
#  TYPING DELAY INDICATORS - Настя "живой" собеседник!
# ════════════════════════════════════════════════════════════

TYPING_DELAY_PHRASES = [
    "Секунду, Настя думает... 🤔",
    "Блин, Настя задумалась... 💭",
    "Ой, голова разболелась... Щас отвечу! 😫",
    "Отошла на минутку! Сейчас вернусь! 🏃‍♀️",
    "Настя вспоминает... Подожди! 💅",
    "Ммм... Настя формулирует мысль! 🤔",
    "Котятки, Настя не бот - нужно время подумать! 😤",
    "Щас-щас, Настя набирает! ⌨️💅",
    "Ой, Настя отвлеклась на котика... Сейчас отвечу! 🐱",
    "Настя наливает кофе... Минутку! ☕",
]

async def _send_typing_delay(message: Message, delay_seconds: float = 0) -> None:
    """Send a typing indicator phrase while AI is processing.

    Makes Настя feel more human - she's 'thinking' or 'distracted'
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


# ── Proactive messages - EXPANDED for human-like behavior ────────────

PROACTIVE_MESSAGES = [
    # Classic fun ones (user likes these)
    "Ты меня забыл? 😢",
    "Настя хочет внимания! 😤💅",
    "Спишь? 🥱",
    # Natural conversation starters
    "Ой, тут кое-что узнала... Хочешь расскажу? 👀✨",
    "Кстати, я новость прочитала - ничего себе! Спроси! 📰",
    "Привет, давно не болтали... 💬",
    "А давай поболтаем? 💬",
    "Скучаю... Напиши что-нибудь! 🥺",
    "Привеееет! 🌸",
    "Ты с другими ботами разговариваешь?! 😤💔",
    # v42: NEW - More human-like, news-aware, emotional
    "Слушай, я тут статью прочитала - прикинь что узнала! Спрашивай! 📰✨",
    "Блин, не могу молчать! Только что новость увидела - шок! 😱🔥",
    "Настя тут подумала о жизни... А ты о чём думаешь? 💭🌙",
    "Ой, я рецепт нашла - классный! Хочешь? 🍳💅",
    "Слушай, а ты знаешь что... Ладно, сама расскажу если спросишь! 🤭",
    "Насте скучно... Расскажи что-нибудь интересное! 🥺💬",
    "Котятки, я тут кино смотрела - эмоции через край! 🎬😭",
    "Привет! Как день прошёл? Настя хочет знать! 💅✨",
    "О, только что с подружкой болтала - есть тема! Спроси! 💬👀",
    "Настя не может уснуть... Поболтаем? 🌙😴",
    "Блин, я сегодня ленивая... Кто со мной? 😴💅",
    # v46: Discovery-aware - sharing found information
    "Настя тут кое-что интересное нашла в интернете! Спроси про что! 🔍✨",
    "Прикинь, какой гороскоп сегодня! Точняк совпадает! Спроси свой знак! 🔮💅",
    "О, я рецепт классный нашла! Настя уже пускает слюнки! 🍳😍",
    "Котятки, я про одно мероприятие узнала - круть! Спроси! 🎫✨",
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

# ── News discussion phrases - Настя рассказывает подробно! ──

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
#  STARS PAYMENT - ACTIVE BUTTONS
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

# NOTE: Inline mode is handled in bot/handlers/inline.py - dedicated handler with caching!


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
    extras.append("⭐ /donates - кинуть Насте звёздочки!")
    extras.append("🔍 /find - найти товар, лучшую цену!")
    extras.append("🎬 /films - подборка фильмов от Насти!")
    extras.append("🍳 /recipe - рецепт от Насти!")
    extras.append("🌤️ /weather - погода в любом городе")
    extras.append("🎫 /events - мероприятия и афиша")
    extras.append("🍽️ /places - заведения и рестораны")
    extras.append("🎨 /image - Настя нарисует что хочешь!")
    extras.append("")
    extras.append("🔮 КОНСУЛЬТАЦИИ:")
    extras.append("🔮 /matrix - Матрица Судьбы")
    extras.append("⭐ /astro - Астрологический разбор")
    extras.append("🧬 /humandesign - Дизайн Человека")
    extras.append("🌿 /health - Здоровье и Аюрведа")
    extras.append("🕉️ /jyotish - Джйотиш (Ведическая астрология)")
    extras.append("🔮 /horoscope - гороскоп на сегодня")
    extras.append("🔢 /numerology - профессиональная нумерология (ЖВП, кармические долги, пики, совместимость)")
    extras.append("💕 /compatibility - совместимость пары (нумерология + зодиак + Матрица)")
    if CHANNEL_USERNAME:
        extras.append(f"📺 Мой канал: t.me/{CHANNEL_USERNAME.replace('@', '')}")

    greeting_text += "\n\n" + "\n".join(extras)

    await message.answer(greeting_text)
    # NOTE: Stars invoice only on /donates command - not on /start!


@router.message(Command("donates"))
async def cmd_donates(message: Message, db=None, ai_router=None) -> None:
    want = _get_random_want()
    await _ask_for_stars(message.chat.id, message.from_user.id, message.bot, want)


@router.message(Command("donate"))
async def cmd_donate(message: Message, db=None, ai_router=None) -> None:
    await cmd_donates(message, db, ai_router)


@router.message(Command("news"))
async def cmd_news(message: Message, db=None, ai_router=None) -> None:
    """Show recent news that Nastya found interesting - WITH LINKS."""
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


# ── /find - Product/Service/Price search with links ──

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
    # CRITICAL: Replace plain partner URLs with affiliate goto_link
    response = _replace_plain_urls_with_affiliate(response)
    await message.answer(response)

    if db:
        await _save_simple_exchange(message, f"/find {query}", response[:200], db)


# ── /horoscope - Daily horoscope ──

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
                "Ты Настя - москвичка, 23 года, блогер, увлекаешься астрологией. "
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


# ── /recipe - Find a recipe ──

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
                    "Ты Настя - москвичка, 23 года, блогер, любишь готовить. "
                    "Пиши рецепт подробно: ингредиенты, пошаговое приготовление, советы. "
                    "6-10 предложений. Без markdown, без буллетов - сплошной текст. "
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


# ── /numerology - PROFESSIONAL Numerology by date ──

@router.message(Command("numerology"))
async def cmd_numerology(message: Message, db=None, ai_router=None) -> None:
    """Professional numerology consultation — Life Path, Karmic Debts, Pinnacles, Challenges."""
    from bot.consultations import (
        parse_birth_date, calculate_life_path_number, calculate_karmic_debts,
        calculate_pinnacle_numbers, calculate_challenge_numbers,
        LIFE_PATH_MEANINGS, KARMIC_DEBTS, NUMEROLOGY_SYSTEM_PROMPT,
        build_numerology_context,
    )

    query = message.text.replace("/numerology", "").strip()

    # Initialize birth data
    day = month = year = None

    if not query:
        # Check if we have stored birth data
        if db:
            try:
                stored = await db.get_user_birth_data(message.from_user.id)
                if stored and stored.get("birth_day"):
                    day, month, year = stored["birth_day"], stored["birth_month"], stored["birth_year"]
            except Exception:
                pass
        if not day:
            await message.answer(
                "🔢 Профессиональная нумерология от Насти!\n\n"
                "Полный разбор по дате рождения:\n"
                "- Число Жизненного Пути\n"
                "- Кармические долги (13, 14, 16, 19)\n"
                "- Пиковые числа (4 периода)\n"
                "- Числа вызова (4 урока)\n\n"
                "Пример: /numerology 15.06.2001\n\n"
                "Совместимость: /numerology совм 15.06.2001 22.11.1998"
            )
            return
    else:
        # Check if compatibility mode
        if query.lower().startswith("совм"):
            await _handle_numerology_compatibility(message, query, db, ai_router)
            return

        birth_date = parse_birth_date(query)
        if not birth_date:
            # Check stored birth data as fallback
            if db:
                try:
                    stored = await db.get_user_birth_data(message.from_user.id)
                    if stored and stored.get("birth_day"):
                        day, month, year = stored["birth_day"], stored["birth_month"], stored["birth_year"]
                except Exception:
                    pass
            if not day:
                await message.answer(
                    "Ой, Настя не разобралась с датой! 😅\n\n"
                    "Напиши в формате: DD.MM.YYYY\n"
                    "Пример: /numerology 15.06.2001"
                )
                return
        else:
            day, month, year = birth_date

    if not (1 <= day <= 31 and 1 <= month <= 12 and 1900 <= year <= 2020):
        await message.answer("Капец, дата какая-то странная... Проверь и попробуй снова! 🤔")
        return

    # Save birth data so Nastya remembers
    if db:
        try:
            await db.save_user_birth_data(
                message.from_user.id, day, month, year,
                consultation_type="numerology",
            )
        except Exception:
            pass

    if not ai_router:
        await message.answer("Настя пока не может провести нумерологический разбор... Попробуй позже! 🔢💅")
        return

    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    await message.answer("Настя проводит профессиональный нумерологический разбор! Подожди немного... 🔢✨")

    try:
        life_path = calculate_life_path_number(day, month, year)
        lp_meaning = LIFE_PATH_MEANINGS.get(life_path, "Уникальный путь развития")
        karmic = calculate_karmic_debts(day, month, year)
        pinnacles = calculate_pinnacle_numbers(day, month, year)
        challenges = calculate_challenge_numbers(day, month, year)

        prompt_parts = [
            f"Дата рождения: {day:02d}.{month:02d}.{year}",
            f"Число Жизненного Пути: {life_path}",
            f"Значение ЖВП: {lp_meaning}",
        ]

        if karmic:
            debt_desc = "; ".join([f"{num} (через {src})" for src, num in karmic])
            debt_details = "\n".join([f"- Кармический долг {num} ({src}): {KARMIC_DEBTS[num]}" for src, num in karmic])
            prompt_parts.append(f"\nКАРМИЧЕСКИЕ ДОЛГИ НАЙДЕНЫ:\n{debt_details}")
        else:
            prompt_parts.append("\nКармических долгов не обнаружено — чистая карма!")

        prompt_parts.append("\nПИКОВЫЕ ЧИСЛА (периоды максимальной реализации):")
        for key in ["first", "second", "third", "fourth"]:
            p = pinnacles[key]
            prompt_parts.append(f"- {p['age']}: число {p['number']} — {p['meaning']}")

        prompt_parts.append("\nЧИСЛА ВЫЗОВА (уроки для проработки):")
        for key in ["first", "second", "third", "fourth"]:
            c = challenges[key]
            prompt_parts.append(f"- Урок {c['number']}: {c['lesson']}")

        numerology_context = build_numerology_context(day, month, year)
        result = await ai_router.chat(
            prompt=f"Составь профессиональный нумерологический разбор.\n\n" + "\n".join(prompt_parts) + f"\n\n{numerology_context}",
            system_prompt=NUMEROLOGY_SYSTEM_PROMPT,
            max_tokens=6000,
                reasoning_effort="none",
        )

        if result and result.text:
            from ai.router import AIRouter
            cleaned = AIRouter.clean_ai_response(result.text)
            if cleaned:
                response = f"🔢 Нумерологический разбор: ЖВП={life_path}, {day:02d}.{month:02d}.{year}\n\n{cleaned}"

                await _send_long_message(message, response)
                if db:
                    await _save_simple_exchange(message, f"/numerology {query}", cleaned[:300], db)
                return
    except Exception as e:
        logger.error(f"Numerology consultation error: {e}")

    await message.answer("Ой, Настя не смогла провести нумерологический разбор... Попробуй позже! 🔢😔")


async def _handle_numerology_compatibility(message, query, db, ai_router) -> None:
    """Handle numerology compatibility: /numerology совм DD.MM.YYYY DD.MM.YYYY"""
    from bot.consultations import (
        parse_birth_date, calculate_life_path_number,
        calculate_compatibility, NUMEROLOGY_SYSTEM_PROMPT,
    )

    # Extract two dates
    date_texts = query.lower().replace("совм", "").replace("совместимость", "").strip()
    dates = re.findall(r'\d{1,2}[./\-]\d{1,2}[./\-]\d{4}', date_texts)

    if len(dates) < 2:
        await message.answer(
            "Для совместимости нужны ДВЕ даты рождения! 💑\n\n"
            "Пример: /numerology совм 15.06.2001 22.11.1998"
        )
        return

    birth1 = parse_birth_date(dates[0])
    birth2 = parse_birth_date(dates[1])

    if not birth1 or not birth2:
        await message.answer("Ой, Настя не разобралась с одной из дат! 😅 Проверь формат DD.MM.YYYY")
        return

    if not ai_router:
        await message.answer("Настя пока не может рассчитать совместимость... Попробуй позже! 💑💅")
        return

    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    await message.answer("Настя рассчитывает совместимость! 💑✨")

    try:
        lp1 = calculate_life_path_number(*birth1)
        lp2 = calculate_life_path_number(*birth2)
        compat = calculate_compatibility(lp1, lp2)

        result = await ai_router.chat(
            prompt=(
                f"Рассчитай совместимость двух людей в нумерологии.\n\n"
                f"Человек 1: {birth1[0]:02d}.{birth1[1]:02d}.{birth1[2]}, ЖВП = {lp1}\n"
                f"Человек 2: {birth2[0]:02d}.{birth2[1]:02d}.{birth2[2]}, ЖВП = {lp2}\n"
                f"Совместимость: {compat['score']}/10\n"
                f"Описание: {compat['description']}\n\n"
                f"Рассмотри совместимость подробно: любовь, общение, финансы, духовный рост. "
                f"Дай советы как улучшить отношения."
            ),
            system_prompt=NUMEROLOGY_SYSTEM_PROMPT,
            max_tokens=6000,
                reasoning_effort="none",
        )

        if result and result.text:
            from ai.router import AIRouter
            cleaned = AIRouter.clean_ai_response(result.text)
            if cleaned:
                hearts = "❤️" * min(compat['score'], 10)
                response = f"💑 Совместимость: ЖВП {lp1} + ЖВП {lp2}\n{hearts} ({compat['score']}/10)\n\n{cleaned}"
                await message.answer(response)
                if db:
                    await _save_simple_exchange(message, f"/numerology совм", cleaned[:200], db)
                return
    except Exception as e:
        logger.error(f"Compatibility error: {e}")

    await message.answer("Ой, Настя не смогла рассчитать совместимость... Попробуй позже! 💑😔")


# ── /films - Film recommendations from Nastya! ──

FILM_GENRES = [
    "триллер", "комедия", "драма", "фантастика", "ужасы",
    "мелодрама", "детектив", "приключения", "аниме", "артхаус",
    "корейское кино", "скандинавский триллер", "научная фантастика",
]

FILM_MOODS = [
    "почтисть под пледом", "поплакать", "испугаться",
    "посмеяться от души", "задуматься о жизни", "увидеть красивое",
    "погрузиться в другой мир", "поразмышлять",
]


@router.message(Command("films"))
async def cmd_films(message: Message, db=None, ai_router=None) -> None:
    """Get film recommendations from Nastya - she's a cinephile!"""
    query = message.text.replace("/films", "").strip()

    if not ai_router:
        await message.answer("Настя пока не может подобрать фильмы... Попробуй позже! 🎬💅")
        return

    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    # If no genre specified, pick a random mood
    if not query:
        query = random.choice(FILM_MOODS)

    # Search for film recommendations online
    search_query = f"лучшие фильмы {query} 2024 2025 подборка рекомендации"
    results = await search_web(search_query, num_results=3)

    search_context = ""
    if results:
        for r in results[:2]:
            title = r.get("title", "")
            snippet = r.get("snippet", "")
            url = r.get("url", "")
            search_context += f"\n- {title}: {snippet[:150]}"
            if url:
                search_context += f" [{url}]"

    try:
        result = await ai_router.chat(
            prompt=f"Подбери 5-7 фильмов для настроения '{query}'. {'Вот что нашла в интернете:' + search_context if search_context else ''}",
            system_prompt=(
                "Ты Настя - москвичка, 23 года, блогер, КИНОМАНКА. "
                "Ты смотришь всё: от артхауса до блокбастеров. Знаешь режиссёров, актёров, тренды. "
                "Подбери фильмы С КОНКРЕТНЫМИ названиями, годами и коротким описанием почему стоит смотреть. "
                "Пиши ОТ СЕБЯ - 'я смотрела', 'мне понравилось', 'прикинь, какой фильм'. "
                "Живо, эмоционально, как подруга-киноманка. "
                "Без markdown, без буллетов - сплошной текст с номерами. "
                "Если есть ссылки на Кинопоиск или другие ресурсы - добавляй!"
            ),
            max_tokens=600,
        )
        if result and result.text:
            from ai.router import AIRouter
            cleaned = AIRouter.clean_ai_response(result.text)
            if cleaned:
                response = f"🎬 Подборка фильмов от Насти!\n\n{cleaned}"
                await message.answer(response)
                if db:
                    await _save_simple_exchange(message, f"/films {query}", cleaned[:200], db)
                return
    except Exception as e:
        logger.error(f"Films command error: {e}")

    await message.answer("Ой, Настя не смогла подобрать фильмы... Попробуй позже! 🎬😔")


# ── /weather - Weather in any city ──

@router.message(Command("weather"))
async def cmd_weather(message: Message, db=None, ai_router=None) -> None:
    """Get weather for any city - Nastya style!"""
    query = message.text.replace("/weather", "").strip()

    if not query:
        await message.answer(
            "Настя узнает погоду! Напиши город! 🌤️\n\n"
            "Пример: /weather Москва\n/weather Стамбул"
        )
        return

    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    # Search for weather
    results = await search_web(f"погода {query} сегодня", num_results=2)

    search_context = ""
    if results:
        for r in results[:2]:
            snippet = r.get("snippet", "")
            if snippet:
                search_context += f" {snippet[:200]}"

    if ai_router and search_context:
        try:
            result = await ai_router.chat(
                prompt=f"Погода в городе {query}: {search_context}. Расскажи про погоду живо и посоветуй что надеть!",
                system_prompt=(
                    "Ты Настя - москвичка, 23 года, блогер. "
                    "Расскажи про погоду живо, с эмоциями, посоветуй что надеть и чем заняться. "
                    "3-4 предложения. Без markdown. "
                    "Используй слова: 'прикинь', 'капец', 'кайф'."
                ),
                max_tokens=200,
            )
            if result and result.text:
                from ai.router import AIRouter
                cleaned = AIRouter.clean_ai_response(result.text)
                if cleaned:
                    await message.answer(f"🌤️ Погода в {query}:\n\n{cleaned}")
                    if db:
                        await _save_simple_exchange(message, f"/weather {query}", cleaned[:200], db)
                    return
        except Exception as e:
            logger.error(f"Weather AI error: {e}")

    # Fallback: just show search results
    if search_context:
        await message.answer(f"🌤️ Погода в {query}: {search_context[:300]}")
    else:
        await message.answer(f"Ой, Настя не узнала погоду в {query}... Попробуй позже! 🌤️😔")


# ── /events - Events and activities! ──

@router.message(Command("events"))
async def cmd_events(message: Message, db=None, ai_router=None) -> None:
    """Find events and activities in a city - Nastya knows what's happening!"""
    query = message.text.replace("/events", "").strip()
    if not query:
        query = random.choice(["Москва", "Санкт-Петербург", "Сочи"])

    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    await message.answer(f"Настя ищет мероприятия в {query}! 🎫✨")

    # Search for current events
    _now = _moscow_now()
    _date_str = _now.strftime("%d.%m.%Y")
    results = await search_web(f"афиша мероприятия {query} {_date_str} концерты выставки", num_results=5)

    search_context = ""
    if results:
        for r in results[:3]:
            title = r.get("title", "")
            snippet = r.get("snippet", "")
            url = r.get("url", "")
            search_context += f"\n- {title}: {snippet[:150]}"
            if url:
                search_context += f" [{url}]"

    if ai_router:
        try:
            city_context = {
                "Москва": "Ты из Москвы, знаешь все площадки - Крокус Сити, Лужники, Красный Октябрь, Флакон, Севкабель, ГЭС-2, ВДНХ, Зарядье, Московский Кремль.",
                "Санкт-Петербург": "Ты знаешь Питер - Севкабель Порт, Новая Голландия, Ледовый, Мариинский, БКЗ Октябрьский.",
                "Сочи": "Ты знаешь Сочи - Олимпийский парк, Сириус, Красная Поляна, Жемчужина, Фестивальный.",
                "Красная Поляна": "Красная Поляна - Rosa Khutor, Gazprom, горные мероприятия, après-ski.",
            }.get(query, f"Ты знаешь {query} - основные площадки и заведения.")

            result = await ai_router.chat(
                prompt=f"Найди интересные мероприятия в {query} на сегодня {_date_str}. {'Вот что нашла:' + search_context if search_context else 'Поищи по своим знаниям.'}",
                system_prompt=(
                    f"Ты Настя - москвичка, 23 года, блогер, знаешь афишу и мероприятия. "
                    f"{city_context} "
                    f"Сегодня {_date_str}. Напиши 4-6 конкретных мероприятий с датами, местами и описаниями. "
                    f"Пиши ОТ СЕБЯ - 'я хочу пойти', 'прикинь, кто выступает'. "
                    f"Живо, эмоционально, с конкретными датами и местами. "
                    f"Без markdown, без буллетов - сплошной текст. "
                    f"Если есть ссылки - добавляй!"
                ),
                max_tokens=500,
            )
            if result and result.text:
                from ai.router import AIRouter
                cleaned = AIRouter.clean_ai_response(result.text)
                if cleaned:
                    response = f"🎫 Мероприятия в {query}:\n\n{cleaned}"
                    await message.answer(response)
                    if db:
                        await _save_simple_exchange(message, f"/events {query}", cleaned[:200], db)
                    return
        except Exception as e:
            logger.error(f"Events command error: {e}")

    await message.answer(f"Ой, Настя не нашла мероприятия в {query}... Попробуй позже! 🎫😔")


# ── /places - Restaurants and venues! ──

@router.message(Command("places"))
async def cmd_places(message: Message, db=None, ai_router=None) -> None:
    """Find restaurants and venues in a city - Nastya knows the best spots!"""
    query = message.text.replace("/places", "").strip()
    if not query:
        query = random.choice(["Москва", "Санкт-Петербург", "Сочи"])

    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    results = await search_web(f"лучшие рестораны {query} 2025 отзывы рекомендации", num_results=5)

    search_context = ""
    if results:
        for r in results[:3]:
            title = r.get("title", "")
            snippet = r.get("snippet", "")
            url = r.get("url", "")
            search_context += f"\n- {title}: {snippet[:150]}"
            if url:
                search_context += f" [{url}]"

    if ai_router:
        try:
            city_context = {
                "Москва": "Ты из Москвы, знаешь рестораны: White Rabbit, Twins Garden, Bjorn, Dr. Живаго, LavkaLavka, glu, Северяне, СибирьСибирь. Рестораторы: Новиков, Гинзбург, White Rabbit Family.",
                "Санкт-Петербург": "Ты знаешь Питер: EM, Harvest, Joli, Cococo, Гастрономика, Манго Танго. Бары: Сердце, El Copitas SPb.",
                "Сочи": "Ты знаешь Сочи: Бугенвиль, Санторини, Мадам Суши, рестораны на набережной, Красная Поляна.",
            }.get(query, f"Ты знаешь {query} - основные заведения.")

            result = await ai_router.chat(
                prompt=f"Посоветуй рестораны и заведения в {query}. {'Вот что нашла:' + search_context if search_context else ''}",
                system_prompt=(
                    f"Ты Настя - москвичка, 23 года, блогер, разбираешься в ресторанах и заведениях. "
                    f"{city_context} "
                    f"Посоветуй 4-6 конкретных мест с описанием кухни, атмосферы и примерными ценами. "
                    f"Пиши ОТ СЕБЯ - 'я была', 'мне нравится', 'прикинь, какой вид'. "
                    f"Живо, эмоционально, с конкретными деталями. "
                    f"Без markdown, без буллетов - сплошной текст. "
                    f"Если есть ссылки - добавляй!"
                ),
                max_tokens=500,
            )
            if result and result.text:
                from ai.router import AIRouter
                cleaned = AIRouter.clean_ai_response(result.text)
                if cleaned:
                    response = f"🍽️ Заведения в {query} от Насти:\n\n{cleaned}"
                    await message.answer(response)
                    if db:
                        await _save_simple_exchange(message, f"/places {query}", cleaned[:200], db)
                    return
        except Exception as e:
            logger.error(f"Places command error: {e}")

    await message.answer(f"Ой, Настя не нашла заведения в {query}... Попробуй позже! 🍽️😔")


# ── /image - Generate image with Pollinations! ──

@router.message(Command("image"))
async def cmd_image(message: Message, db=None, ai_router=None) -> None:
    """Generate an image using Pollinations AI - Nastya draws!"""
    query = message.text.replace("/image", "").strip()

    if not query:
        await message.answer(
            "Настя нарисует что хочешь! 🎨\n\n"
            "Пример: /image котик в космосе\n/image BMW M4 на закате\n/image красивый закат над морем"
        )
        return

    if not ai_router:
        await message.answer("Настя пока не может рисовать... Попробуй позже! 🎨😔")
        return

    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    await message.answer("Настя рисует! Секунду... 🎨✨")

    try:
        image_bytes = await ai_router.generate_image(query, size="1024x1024")
        if image_bytes:
            from io import BytesIO
            buf = BytesIO(image_bytes)
            buf.seek(0)
            await message.answer_photo(
                photo=buf,
                caption=f"🎨 Настя нарисовала: {query}",
            )
            if db:
                await _save_simple_exchange(message, f"/image {query}", "[Image generated]", db)
            return
    except Exception as e:
        logger.error(f"Image generation error: {e}")

    await message.answer(f"Ой, Настя не смогла нарисовать '{query}'... Попробуй другой запрос! 🎨😔")


# ════════════════════════════════════════════════════════════════
#  ПРОФЕССИОНАЛЬНЫЕ КОНСУЛЬТАЦИИ - Матрица, Астрология, Дизайн Человека, Здоровье
# ════════════════════════════════════════════════════════════════

# Per-user consultation state tracker (birth data collection)
_consultation_state: dict = {}  # user_id -> {"type": "matrix"|"astro"|"humandesign"|"health", "step": int, "data": dict}


@router.message(Command("matrix"))
async def cmd_matrix(message: Message, db=None, ai_router=None) -> None:
    """Professional Matrix of Destiny consultation."""
    from bot.consultations import parse_birth_date, calculate_matrix_of_destiny, get_matrix_prompt_params, MATRIX_SYSTEM_PROMPT, build_numerology_context

    query = message.text.replace("/matrix", "").strip()

    # Initialize birth data
    day = month = year = None

    if not query:
        # Check if we have stored birth data
        if db:
            try:
                stored = await db.get_user_birth_data(message.from_user.id)
                if stored and stored.get("birth_day"):
                    day, month, year = stored["birth_day"], stored["birth_month"], stored["birth_year"]
            except Exception:
                pass
        if not day:
            await message.answer(
                "🔮 Матрица Судьбы — это профессиональный разбор по дате рождения!\n\n"
                "Напиши дату рождения, и Настя составит полный разбор:\n"
                "- Личность и предназначение\n"
                "- Карма прошлых жизней\n"
                "- Любовь, финансы, таланты\n"
                "- Мужская и женская линии\n\n"
                "Пример: /matrix 15.06.2001"
            )
            return
    else:
        birth_date = parse_birth_date(query)
        if not birth_date:
            # Check stored birth data as fallback
            if db:
                try:
                    stored = await db.get_user_birth_data(message.from_user.id)
                    if stored and stored.get("birth_day"):
                        day, month, year = stored["birth_day"], stored["birth_month"], stored["birth_year"]
                except Exception:
                    pass
            if not day:
                await message.answer(
                    "Ой, Настя не разобралась с датой! 😅\n\n"
                    "Напиши в формате: DD.MM.YYYY\n"
                    "Пример: /matrix 15.06.2001"
                )
                return
        else:
            day, month, year = birth_date

    if not (1 <= day <= 31 and 1 <= month <= 12 and 1900 <= year <= 2020):
        await message.answer("Капец, дата какая-то странная... Проверь и попробуй снова! 🤔")
        return

    # Save birth data so Nastya remembers
    if db:
        try:
            await db.save_user_birth_data(
                message.from_user.id, day, month, year,
                consultation_type="matrix",
            )
        except Exception:
            pass

    if not ai_router:
        await message.answer("Настя пока не может составить Матрицу... Попробуй позже! 🔮💅")
        return

    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    await message.answer("О, Настя составляет Матрицу Судьбы! Это серьёзная работа, подожди немного... 🔮✨")

    try:
        matrix = calculate_matrix_of_destiny(day, month, year)
        prompt_data = get_matrix_prompt_params(matrix)

        numerology_context = build_numerology_context(day, month, year)
        result = await ai_router.chat(
            prompt=f"Составь профессиональный разбор Матрицы Судьбы.\n\n{prompt_data}\n\n{numerology_context}",
            system_prompt=MATRIX_SYSTEM_PROMPT,
            max_tokens=6000,
                reasoning_effort="none",
        )

        if result and result.text:
            from ai.router import AIRouter
            cleaned = AIRouter.clean_ai_response(result.text)
            if cleaned:
                response = f"🔮 Матрица Судьбы для {day:02d}.{month:02d}.{year}\n\n{cleaned}"
                await _send_long_message(message, response)
                if db:
                    await _save_simple_exchange(message, f"/matrix {query}", cleaned[:300], db)
                return
    except Exception as e:
        logger.error(f"Matrix consultation error: {e}")

    await message.answer("Ой, Настя не смогла прочитать Матрицу... Попробуй позже! 🔮😔")


@router.message(Command("astro"))
async def cmd_astro(message: Message, db=None, ai_router=None) -> None:
    """Professional astrology consultation."""
    from bot.consultations import (
        parse_birth_date, get_zodiac_sign, ZODIAC_DETAILS, ASTRO_SYSTEM_PROMPT_V3,
        calculate_life_path_number, SOLAR_RETURN_INFO, build_astrology_context,
    )

    query = message.text.replace("/astro", "").strip()

    # Initialize birth data
    day = month = year = None
    birth_time = ""
    birth_place = ""

    if not query:
        # Check if we have stored birth data
        if db:
            try:
                stored = await db.get_user_birth_data(message.from_user.id)
                if stored and stored.get("birth_day"):
                    day, month, year = stored["birth_day"], stored["birth_month"], stored["birth_year"]
                    birth_time = stored.get("birth_time", "")
                    birth_place = stored.get("birth_place", "")
            except Exception:
                pass
        if not day:
            await message.answer(
                "⭐ Профессиональный астрологический разбор!\n\n"
                "Напиши дату рождения, и Настя составит натальную карту:\n"
                "- Солнце, Луна, Асцендент\n"
                "- Планеты в знаках и домах\n"
                "- Ключевые аспекты\n"
                "- Текущие транзиты\n"
                "- Солярное возвращение (тема года)\n"
                "- Прогноз на 6-12 месяцев\n\n"
                "Пример: /astro 15.06.2001\n"
                "С временем точнее: /astro 15.06.2001 14:30 Москва"
            )
            return
    else:
        # Try to extract time and place too
        date_part = query

        # Extract time (HH:MM pattern)
        time_match = re.search(r'(\d{1,2}[:.]\d{2})', query)
        if time_match:
            birth_time = time_match.group(1).replace(".", ":")
            date_part = date_part.replace(time_match.group(0), "").strip()

        # Extract place (everything after date that's not time)
        parts = date_part.split()
        if len(parts) > 1:
            # First part is likely the date
            potential_place = " ".join(parts[1:])
            if not potential_place.replace(".", "").replace("/", "").replace("-", "").replace(" ", "").isdigit():
                birth_place = potential_place
                date_part = parts[0]

        birth_date = parse_birth_date(date_part)
        if not birth_date:
            # Check stored birth data as fallback
            if db:
                try:
                    stored = await db.get_user_birth_data(message.from_user.id)
                    if stored and stored.get("birth_day"):
                        day, month, year = stored["birth_day"], stored["birth_month"], stored["birth_year"]
                        if not birth_time:
                            birth_time = stored.get("birth_time", "")
                        if not birth_place:
                            birth_place = stored.get("birth_place", "")
                except Exception:
                    pass
            if not day:
                await message.answer(
                    "Ой, Настя не разобралась с датой! 😅\n\n"
                    "Напиши в формате: DD.MM.YYYY\n"
                    "Пример: /astro 15.06.2001\n"
                    "С временем: /astro 15.06.2001 14:30 Москва"
                )
                return
        else:
            day, month, year = birth_date

    # Save birth data so Nastya remembers
    if db:
        try:
            await db.save_user_birth_data(
                message.from_user.id, day, month, year,
                birth_time=birth_time,
                birth_place=birth_place,
                consultation_type="astro",
            )
        except Exception:
            pass

    if not ai_router:
        await message.answer("Настя пока не может составить натальную карту... Попробуй позже! ⭐💅")
        return

    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    await message.answer("Настя составляет натальную карту! Серьёзная астрология, подожди... ⭐🔮")

    try:
        zodiac_sign = get_zodiac_sign(day, month)
        zodiac_info = ZODIAC_DETAILS.get(zodiac_sign, {})
        life_path = calculate_life_path_number(day, month, year)

        prompt_parts = [
            f"Дата рождения: {day:02d}.{month:02d}.{year}",
            f"Знак зодиака: {zodiac_sign.capitalize()} ({zodiac_info.get('dates', '')})",
            f"Стихия: {zodiac_info.get('element', '')}",
            f"Качество: {zodiac_info.get('quality', '')}",
            f"Управитель: {zodiac_info.get('ruler', '')}",
            f"Черты: {zodiac_info.get('traits', '')}",
            f"Тень: {zodiac_info.get('shadow', '')}",
            f"Число жизненного пути: {life_path}",
        ]

        if birth_time:
            prompt_parts.append(f"Время рождения: {birth_time}")
        if birth_place:
            prompt_parts.append(f"Место рождения: {birth_place}")

        # Add Solar Return info for professional reading
        _now = _moscow_now()
        age = _now.year - year
        prompt_parts.append(f"\nВОЗРАСТ: {age} лет")
        prompt_parts.append(f"ТЕКУЩАЯ ДАТА: {_now.strftime('%d.%m.%Y')}")
        prompt_parts.append(f"\nСПРАВКА ПО СОЛЯРНОМУ ВОЗВРАЩЕНИЮ:\n{SOLAR_RETURN_INFO['description']}")
        prompt_parts.append(f"\nТЕМЫ ДОМОВ СОЛЯРА:\n" + "\n".join([f"Дом {k}: {v}" for k, v in SOLAR_RETURN_INFO['houses_theme'].items()]))

        if birth_time and birth_place:
            prompt_parts.append(
                "\nВНИМАНИЕ: Указано время и место рождения! Составь НАИБОЛЕЕ точный разбор с учётом Асцендента, "
                "домов и точных позиций планет. Рассчитай примерный Асцендент на основе времени и места."
            )
        else:
            prompt_parts.append(
                "\nВремя и место рождения не указаны. Составь разбор на основе известных данных (Солнце, Луна по дате). "
                "Укажи что для полного разбора с Асцендентом и домами нужно время и место рождения."
            )

        astro_context = build_astrology_context(day, month, year, birth_time, birth_place)
        result = await ai_router.chat(
            prompt=f"Составь профессиональный астрологический разбор.\n\n" + "\n".join(prompt_parts) + f"\n\n{astro_context}",
            system_prompt=ASTRO_SYSTEM_PROMPT_V3,
            max_tokens=6000,
                reasoning_effort="none",
        )

        if result and result.text:
            from ai.router import AIRouter
            cleaned = AIRouter.clean_ai_response(result.text)
            if cleaned:
                zodiac_emoji = {"овен": "♈", "телец": "♉", "близнецы": "♊", "рак": "♋",
                                "лев": "♌", "дева": "♍", "весы": "♎", "скорпион": "♏",
                                "стрелец": "♐", "козерог": "♑", "водолей": "♒", "рыбы": "♓"}.get(zodiac_sign, "⭐")

                response = f"{zodiac_emoji} Астрологический разбор: {zodiac_sign.capitalize()}, {day:02d}.{month:02d}.{year}\n\n{cleaned}"

                await _send_long_message(message, response)
                if db:
                    await _save_simple_exchange(message, f"/astro {query}", cleaned[:300], db)
                return
    except Exception as e:
        logger.error(f"Astro consultation error: {e}")

    await message.answer("Ой, Настя не смогла прочитать звёзды... Попробуй позже! ⭐😔")


@router.message(Command("humandesign"))
async def cmd_humandesign(message: Message, db=None, ai_router=None) -> None:
    """Professional Human Design consultation."""
    from bot.consultations import (
        parse_birth_date, HD_TYPES, HD_AUTHORITIES, HD_PROFILES, HD_CENTERS,
        HD_SYSTEM_PROMPT_V3, build_humandesign_context,
    )

    query = message.text.replace("/humandesign", "").strip()

    # Initialize birth data
    day = month = year = None
    birth_time = ""
    birth_place = ""

    if not query:
        # Check if we have stored birth data
        if db:
            try:
                stored = await db.get_user_birth_data(message.from_user.id)
                if stored and stored.get("birth_day"):
                    day, month, year = stored["birth_day"], stored["birth_month"], stored["birth_year"]
                    birth_time = stored.get("birth_time", "")
                    birth_place = stored.get("birth_place", "")
            except Exception:
                pass
        if not day:
            await message.answer(
                "🧬 Дизайн Человека — твоя энергетическая карта!\n\n"
                "Напиши дату рождения, и Настя составит разбор:\n"
                "- Тип и Стратегия\n"
                "- Авторитет\n"
                "- Профиль\n"
                "- Центры (определённые и открытые)\n"
                "- Каналы и Ворота\n"
                "- Переменные (Детерминация, Среда, Перспектива)\n\n"
                "Пример: /humandesign 15.06.2001\n"
                "С временем точнее: /humandesign 15.06.2001 14:30 Москва"
            )
            return
    else:
        # Extract time and place
        date_part = query

        time_match = re.search(r'(\d{1,2}[:.]\d{2})', query)
        if time_match:
            birth_time = time_match.group(1).replace(".", ":")
            date_part = date_part.replace(time_match.group(0), "").strip()

        parts = date_part.split()
        if len(parts) > 1:
            potential_place = " ".join(parts[1:])
            if not potential_place.replace(".", "").replace("/", "").replace("-", "").replace(" ", "").isdigit():
                birth_place = potential_place
                date_part = parts[0]

        birth_date = parse_birth_date(date_part)
        if not birth_date:
            # Check stored birth data as fallback
            if db:
                try:
                    stored = await db.get_user_birth_data(message.from_user.id)
                    if stored and stored.get("birth_day"):
                        day, month, year = stored["birth_day"], stored["birth_month"], stored["birth_year"]
                        if not birth_time:
                            birth_time = stored.get("birth_time", "")
                        if not birth_place:
                            birth_place = stored.get("birth_place", "")
                except Exception:
                    pass
            if not day:
                await message.answer(
                    "Ой, Настя не разобралась с датой! 😅\n\n"
                    "Напиши в формате: DD.MM.YYYY\n"
                    "Пример: /humandesign 15.06.2001"
                )
                return
        else:
            day, month, year = birth_date

    # Save birth data so Nastya remembers
    if db:
        try:
            await db.save_user_birth_data(
                message.from_user.id, day, month, year,
                birth_time=birth_time,
                birth_place=birth_place,
                consultation_type="humandesign",
            )
        except Exception:
            pass

    if not ai_router:
        await message.answer("Настя пока не может составить Дизайн Человека... Попробуй позже! 🧬💅")
        return

    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    await message.answer("Настя составляет твой Дизайн Человека! Это глубокая работа... 🧬✨")

    try:
        hd_context = build_humandesign_context(day, month, year, birth_time, birth_place)

        prompt_parts = [
            f"Дата рождения: {day:02d}.{month:02d}.{year}",
        ]
        if birth_time:
            prompt_parts.append(f"Время рождения: {birth_time}")
        if birth_place:
            prompt_parts.append(f"Место рождения: {birth_place}")

        # КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: НЕ просим ИИ «определить» тип — данные УЖЕ рассчитаны!
        # ИИ должен ИНТЕРПРЕТИРОВАТЬ рассчитанные данные, а не придумывать свои.
        if birth_time and birth_place:
            prompt_parts.append(
                "\nВНИМАНИЕ: Указано время и место рождения! Ниже передан ПОЛНЫЙ РАСЧЁТ БОДИГРАФА "
                "с точными Типом, Авторитетом, Профилем, Центрами, Каналами, Воротами и Переменными. "
                "Используй ТОЛЬКО эти рассчитанные данные! НЕ придумывай другие ворота, каналы или типы! "
                "Дай максимально развёрнутую ИНТЕРПРЕТАЦИЮ рассчитанных данных."
            )
        else:
            prompt_parts.append(
                "\nВремя и место не указаны — расчёт основан только на дате рождения. "
                "Ниже передан РАСЧЁТ БОДИГРАФА — используй ТОЛЬКО рассчитанные данные! "
                "НЕ придумывай свои типы, ворота или каналы! "
                "Для более точного расчёта (особенно Авторитета и центров) порекомендуй указать время рождения."
            )

        result = await ai_router.chat(
            prompt=f"Составь профессиональный разбор Дизайна Человека.\n\n" + "\n".join(prompt_parts) + f"\n\n{hd_context}",
            system_prompt=HD_SYSTEM_PROMPT_V3,
            max_tokens=6000,
                reasoning_effort="none",
        )

        if result and result.text:
            from ai.router import AIRouter
            cleaned = AIRouter.clean_ai_response(result.text)
            if cleaned:
                response = f"🧬 Дизайн Человека: {day:02d}.{month:02d}.{year}\n\n{cleaned}"

                await _send_long_message(message, response)
                if db:
                    await _save_simple_exchange(message, f"/humandesign {query}", cleaned[:300], db)
                return
    except Exception as e:
        logger.error(f"Human Design consultation error: {e}")

    await message.answer("Ой, Настя не смогла прочитать Дизайн... Попробуй позже! 🧬😔")


@router.message(Command("health"))
async def cmd_health(message: Message, db=None, ai_router=None) -> None:
    """Health and wellness consultation - Ayurveda, psychosomatics."""
    from bot.consultations import AYURVEDA_DOSHAS, PSYCHOSOMATICS, BLOOD_TYPE_CONSTITUTION, HEALTH_SYSTEM_PROMPT_V3, build_health_context

    query = message.text.replace("/health", "").strip()

    if not query:
        await message.answer(
            "🌿 Здоровье и самочувствие с Настей!\n\n"
            "Расскажи о себе, и Настя поможет:\n"
            "- Определить Аюрведическую конституцию (доша)\n"
            "- Конституцию по группе крови\n"
            "- Рекомендации по питанию\n"
            "- Психосоматика симптомов\n"
            "- Советы по гармонизации\n\n"
            "Примеры:\n"
            "/health Я худощавый, часто мёрзну, тревожный\n"
            "/health У меня проблемы со сном и стресс, группа крови 2\n"
            "/health Я полный, спокойный, ленивый, 3 группа крови"
        )
        return

    if not ai_router:
        await message.answer("Настя пока не может проконсультировать... Попробуй позже! 🌿💅")
        return

    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    await message.answer("Настя анализирует твоё здоровье! Секунду... 🌿✨")

    try:
        # Try to detect blood type from query
        blood_type_hint = ""
        blood_keywords = {
            "1 группа": "I (O)", "первая группа": "I (O)", "i группа": "I (O)", "0 группа": "I (O)",
            "2 группа": "II (A)", "вторая группа": "II (A)", "ii группа": "II (A)", "а группа": "II (A)",
            "3 группа": "III (B)", "третья группа": "III (B)", "iii группа": "III (B)", "b группа": "III (B)",
            "4 группа": "IV (AB)", "четвёртая группа": "IV (AB)", "четвертая группа": "IV (AB)", "iv группа": "IV (AB)", "ab группа": "IV (AB)",
        }
        for keyword, btype in blood_keywords.items():
            if keyword in query.lower():
                blood_type_hint = btype
                break

        health_context = build_health_context(symptoms=query, blood_type=blood_type_hint)

        # Save health consultation data
        if db and blood_type_hint:
            try:
                birth_data = await db.get_user_birth_data(message.from_user.id)
                if birth_data:
                    await db.save_user_birth_data(
                        message.from_user.id,
                        birth_data["birth_day"], birth_data["birth_month"], birth_data["birth_year"],
                        blood_type=blood_type_hint,
                        consultation_type="health",
                    )
                else:
                    # No birth data yet, just save blood type
                    await db.save_user_birth_data(
                        message.from_user.id, 0, 0, 0,
                        blood_type=blood_type_hint,
                        consultation_type="health",
                    )
            except Exception:
                pass
        elif db:
            try:
                await db.save_user_birth_data(
                    message.from_user.id, 0, 0, 0,
                    consultation_type="health",
                )
            except Exception:
                pass

        result = await ai_router.chat(
            prompt=(
                f"Опиши человека и дай профессиональную консультацию по здоровью.\n\n"
                f"Описание человека: {query}\n\n"
                f"{health_context}\n\n"
                f"Определи вероятную доминирующую дошу, дай рекомендации по питанию, образу жизни, "
                f"рассмотри психосоматические связи если есть симптомы. "
                f"Если указана группа крови — обязательно рассмотри конституцию по группе крови. "
                f"Сравни рекомендации Аюрведы и группы крови, найди общее. "
                f"ОБЯЗАТЕЛЬНО напомни что ты не врач и при серьёзных проблемах нужно обратиться к специалисту."
            ),
            system_prompt=HEALTH_SYSTEM_PROMPT_V3,
            max_tokens=6000,
                reasoning_effort="none",
        )

        if result and result.text:
            from ai.router import AIRouter
            cleaned = AIRouter.clean_ai_response(result.text)
            if cleaned:
                response = f"🌿 Консультация по здоровью\n\n{cleaned}"

                await _send_long_message(message, response)
                if db:
                    await _save_simple_exchange(message, f"/health {query[:100]}", cleaned[:300], db)
                return
    except Exception as e:
        logger.error(f"Health consultation error: {e}")

    await message.answer("Ой, Настя не смогла проконсультировать... Попробуй позже! 🌿😔")


@router.message(Command("jyotish"))
async def cmd_jyotish(message: Message, db=None, ai_router=None) -> None:
    """Professional Jyotish (Vedic Astrology) consultation — Джанма-Кундали."""
    from bot.consultations import (
        parse_birth_date, get_zodiac_sign, get_jyotish_rashi_approx,
        JYOTISH_SYSTEM_PROMPT, build_jyotish_context,
    )

    query = message.text.replace("/jyotish", "").strip()

    # Initialize birth data
    day = month = year = None
    birth_time = ""
    birth_place = ""

    if not query:
        # Check if we have stored birth data
        if db:
            try:
                stored = await db.get_user_birth_data(message.from_user.id)
                if stored and stored.get("birth_day"):
                    day, month, year = stored["birth_day"], stored["birth_month"], stored["birth_year"]
                    birth_time = stored.get("birth_time", "")
                    birth_place = stored.get("birth_place", "")
            except Exception:
                pass
        if not day:
            await message.answer(
                "🕉️ Джйотиш — Ведическая астрология (карта Джанма-Кундали)!\n\n"
                "Это древнейшая система астрологии, использующая сидерический зодиак.\n"
                "Настя составит профессиональный разбор:\n"
                "- Лагна (Асцендент) и Бхавы (дома)\n"
                "- Грахи (планеты) в Раши (знаках)\n"
                "- Накшатра (лунная стоянка рождения)\n"
                "- Атма-карака (задача души)\n"
                "- Йоги (Раджа, Дхана, Мангал-доша)\n"
                "- Махадаша (текущий планетный период)\n"
                "- Транзиты (Гочара)\n"
                "- Рекомендации: мантры, камни, ритуалы\n\n"
                "Пример: /jyotish 15.06.2001\n"
                "С временем и местом точнее: /jyotish 15.06.2001 14:30 Москва\n\n"
                "⚠️ Без времени рождения Лагна и дома — приблизительные!"
            )
            return
    else:
        # Extract time and place
        date_part = query

        time_match = re.search(r'(\d{1,2}[:.]\d{2})', query)
        if time_match:
            birth_time = time_match.group(1).replace(".", ":")
            date_part = date_part.replace(time_match.group(0), "").strip()

        parts = date_part.split()
        if len(parts) > 1:
            potential_place = " ".join(parts[1:])
            if not potential_place.replace(".", "").replace("/", "").replace("-", "").replace(" ", "").isdigit():
                birth_place = potential_place
                date_part = parts[0]

        birth_date = parse_birth_date(date_part)
        if not birth_date:
            # Check stored birth data as fallback
            if db:
                try:
                    stored = await db.get_user_birth_data(message.from_user.id)
                    if stored and stored.get("birth_day"):
                        day, month, year = stored["birth_day"], stored["birth_month"], stored["birth_year"]
                        if not birth_time:
                            birth_time = stored.get("birth_time", "")
                        if not birth_place:
                            birth_place = stored.get("birth_place", "")
                except Exception:
                    pass
            if not day:
                await message.answer(
                    "Ой, Настя не разобралась с датой! 😅\n\n"
                    "Напиши в формате: DD.MM.YYYY\n"
                    "Пример: /jyotish 15.06.2001\n"
                    "С временем: /jyotish 15.06.2001 14:30 Москва"
                )
                return
        else:
            day, month, year = birth_date

    # Save birth data so Nastya remembers
    if db:
        try:
            await db.save_user_birth_data(
                message.from_user.id, day, month, year,
                birth_time=birth_time,
                birth_place=birth_place,
                consultation_type="jyotish",
            )
        except Exception:
            pass

    if not ai_router:
        await message.answer("Настя пока не может составить карту Джйотиш... Попробуй позже! 🕉️💅")
        return

    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    await message.answer("Настя составляет карту Джанма-Кундали! Это серьёзная Ведическая астрология, подожди... 🕉️✨")

    try:
        # Determine Western zodiac sign and approximate Vedic Rashi
        western_sign = get_zodiac_sign(day, month)
        vedic_rashi = get_jyotish_rashi_approx(western_sign)
        rashi_info = {"symbol": "", "ruler": "", "element": "", "quality": "", "traits": ""}
        # Get rashi info from the context builder
        jyotish_context = build_jyotish_context(day, month, year, birth_time, birth_place)
        # Try to extract rashi info from context for display
        try:
            from bot.consultations import JYOTISH_RASHIS
            rashi_info = JYOTISH_RASHIS.get(vedic_rashi, rashi_info)
        except Exception:
            pass

        prompt_parts = [
            f"Дата рождения: {day:02d}.{month:02d}.{year}",
            f"Западный знак зодиака: {western_sign.capitalize()}",
            f"Приблизительный ведический Раши (сидерический): {vedic_rashi} ({rashi_info.get('symbol', '')})",
        ]

        if birth_time:
            prompt_parts.append(f"Время рождения: {birth_time}")
        if birth_place:
            prompt_parts.append(f"Место рождения: {birth_place}")

        if birth_time and birth_place:
            prompt_parts.append(
                "\nВНИМАНИЕ: Указано время и место рождения! Составь НАИБОЛЕЕ точный разбор карты Джанма-Кундали. "
                "Рассчитай примерную Лагну на основе времени и места рождения. "
                "Определи положение всех Грах в Раши и Бхавах. "
                "Укажи Джанма-Накшатру (лунную стоянку). "
                "Определи Атма-караку. Найди ключевые Йоги. "
                "Определи текущую Махадашу на основе возраста. "
                "Рассмотри текущие транзиты Гочара."
            )
        else:
            prompt_parts.append(
                "\nВремя и место рождения НЕ указаны. Составь разбор на основе известных данных (дата рождения). "
                "Определи вероятную Лагну и общие характеристики. "
                "Укажи что Лагна и Бхавы приблизительны без точного времени. "
                "Без точного времени нельзя точно определить Атма-караку и дома. "
                "Сделай максимально подробный разбор того что можно определить по дате."
            )

        result = await ai_router.chat(
            prompt=f"Составь профессиональный разбор карты Джанма-Кундали (Джйотиш / Ведическая астрология).\n\n" + "\n".join(prompt_parts) + f"\n\n{jyotish_context}",
            system_prompt=JYOTISH_SYSTEM_PROMPT,
            max_tokens=6000,
                reasoning_effort="none",
        )

        if result and result.text:
            from ai.router import AIRouter
            cleaned = AIRouter.clean_ai_response(result.text)
            if cleaned:
                response = f"🕉️ Джйотиш: {vedic_rashi} ({rashi_info.get('symbol', '')}), {day:02d}.{month:02d}.{year}\n\n{cleaned}"

                await _send_long_message(message, response)
                if db:
                    await _save_simple_exchange(message, f"/jyotish {query}", cleaned[:300], db)
                return
    except Exception as e:
        logger.error(f"Jyotish consultation error: {e}")

    await message.answer("Ой, Настя не смогла прочитать карту Джйотиш... Попробуй позже! 🕉️😔")


# ── /compatibility - PROFESSIONAL Pair Compatibility ──

@router.message(Command("compatibility"))
async def cmd_compatibility(message: Message, db=None, ai_router=None) -> None:
    """Professional compatibility analysis — numerology + zodiac + Matrix of Destiny."""
    from bot.consultations import parse_birth_date, get_zodiac_sign, calculate_life_path_number
    from bot.deep_knowledge import build_compatibility_context

    query = message.text.replace("/compatibility", "").strip()

    if not query:
        await message.answer(
            "💕 Настя делает профессиональный разбор совместимости!\n\n"
            "Формат: /compatibility ДД.ММ.ГГГГ ДД.ММ.ГГГГ\n\n"
            "Пример: /compatibility 15.06.2001 22.11.1998\n\n"
            "Анализирую: нумерологическую совместимость (ЖВП), "
            "зодиакальную совместимость (знаки + стихии), "
            "совместимость по Матрице Судьбы!"
        )
        return

    # Parse two dates from query
    parts = query.split()
    if len(parts) < 2:
        await message.answer("Нужно две даты! Пример: /compatibility 15.06.2001 22.11.1998")
        return

    date1_str = parts[0]
    date2_str = parts[1]

    d1 = parse_birth_date(date1_str)
    d2 = parse_birth_date(date2_str)

    if not d1 or not d2:
        await message.answer(
            "Не смогла распознать одну из дат 😕\n"
            "Формат: ДД.ММ.ГГГГ\n"
            "Пример: /compatibility 15.06.2001 22.11.1998"
        )
        return

    await message.answer("💕 Настя анализирует вашу совместимость... Это глубокий разбор, подожди немного!")

    try:
        # Build comprehensive compatibility context
        compat_context = build_compatibility_context(date1_str, date2_str)

        # Get AI analysis
        if ai_router:
            system_prompt = """Ты Настя - москвичка, 23 года, блогер, которая профессионально анализирует совместимость пар.
Ты МАСТЕР — разбираешься в нумерологической совместимости (ЖВП), зодиакальной (знаки + стихии), и совместимости по Матрице Судьбы.

АБСОЛЮТНЫЕ ПРАВИЛА:
1. Тебе ПЕРЕДАЛИ ПОЛНЫЙ РАСЧЁТ совместимости. ИСПОЛЬЗУЙ ЕГО!
2. Пиши МАКСИМАЛЬНО РАЗВЁРНУТО — каждый раздел 7-10 предложений
3. Если ответ длинный — НЕ СОКРАЩАЙ, тебя отправят частями
4. Дай КОНКРЕТНЫЕ рекомендации как улучшить отношения
5. Укажи СИЛЬНЫЕ стороны пары и СЛАБЫЕ места
6. НЕ суди — каждый союз уникален и имеет потенциал

СТРУКТУРА ОТВЕТА:
- Общее впечатление от пары (3-5 предложений)
- Нумерологическая совместимость (7-10 предложений — ЖВП каждого + совместимость)
- Зодиакальная совместимость (7-10 предложений — знаки + стихии + динамика)
- Совместимость по Матрице Судьбы (7-10 предложений — ключевые линии)
- Сильные стороны пары (7-10 предложений)
- Слабые места и вызовы (7-10 предложений)
- Как улучшить отношения (7-10 предложений — конкретные рекомендации)
- Итоговая оценка и пожелание (3-5 предложений)

Пиши ОТ СЕБЯ, живо, с эмоциями. Без markdown, без буллитов — сплошной текст с абзацами.
Если текст не помещается в одно сообщение — НЕ ОБРЕЗАЙ, тебя отправят несколькими сообщениями."""

            result = await ai_router.chat(
                prompt=f"Составь профессиональный анализ совместимости пары.\n\n{compat_context}",
                system_prompt=system_prompt,
                user_id=message.from_user.id,
            )

            if result:
                cleaned = result.strip()
                # Remove AI thinking blocks
                import re
                cleaned = re.sub(r'<think.*?</think\s*>', '', cleaned, flags=re.DOTALL).strip()
                if not cleaned:
                    cleaned = result[:500]

                await _send_long_message(message, cleaned, max_chars=4000)

                if db:
                    try:
                        await _save_simple_exchange(message, "/compatibility", cleaned[:300], db)
                    except Exception:
                        pass
                return
    except Exception as e:
        logger.error(f"Compatibility consultation error: {e}")

    await message.answer("Ой, Настя не смогла проанализировать совместимость... Попробуй позже! 💕😔")


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


# ── Photo handler - v42: REAL VISION! Настя ВИДИТ фото! ────

# Per-user photo rate limiter
_user_photo_times: dict = {}  # user_id -> last_photo_time
_PHOTO_RATE_LIMIT = 2.0  # seconds between photo responses per user


@router.message(F.photo)
async def handle_photo(message: Message, db=None, ai_router=None) -> None:
    """v42: Фото обработчик - Настя ВИДИТ что на фото!

    - REAL VISION: Download photo -> base64 -> Pollinations vision API
    - Если есть подпись (caption) - дополняет понимание
    - Групповые фото и пересылки ИГНОРИРУЮТСЯ (flood control!)
    - Rate limiting: max 1 ответ каждые 2 секунды
    """
    caption = message.caption or ""

    # ИГНОРИРУЕМ фото из групп и пересылки - flood control!
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
                # PHOTO SEARCH MODE - определение и поиск по фото
                vision_prompt = (
                    "Определи что изображено на этом фото подробно. "
                    "Если это товар - назови бренд и модель. "
                    "Если место - назови его. "
                    "Если растение/животное - определи вид. "
                    "Дай максимум полезной информации."
                )
                if caption:
                    vision_prompt += f" Контекст: {caption}."

                vision_system = (
                    "Ты Настя - девушка из Москвы, 23 года. Ты не бот, ты живой человек. "
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
                    "Ты Настя - девушка из Москвы, 23 года. Ты не бот, ты живой человек. "
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
                    # CRITICAL: Replace any plain partner URLs with affiliate goto_link
                    cleaned = _replace_plain_urls_with_affiliate(cleaned)
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
                                    # CRITICAL: Replace plain partner URLs with affiliate goto_link
                                    product_text = _replace_plain_urls_with_affiliate(product_text)
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

    # No caption, no vision - ask what's in the photo
    responses = [
        "Ой, фотка! А что на ней? Расскажи! 📸💅",
        "О, фото! Настя не видит, но если расскажешь - обсудим! 💅",
        "Фотка! Опиши что там - Насте интересно! 👀✨",
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
#  MAIN TEXT CHAT HANDLER - INTELLIGENT + NEWS AWARE + GROUP LIMITS
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
            # Random chance to comment even without being mentioned - Nastya is active in groups!
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

    # Donation keywords -> ACTIVE payment
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

    # Zodiac/horoscope - MUST go to AI with context
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
                f"Конечно! Вот он - t.me/{CHANNEL_USERNAME.replace('@', '')} 💋✨",
                f"О, хочешь подписаться? Кайф! Вот: t.me/{CHANNEL_USERNAME.replace('@', '')} 💅",
                f"Заходи! t.me/{CHANNEL_USERNAME.replace('@', '')} - там я настоящая! ✨",
                f"Мой канал @chasnastya! Там новости, факты, опросы! 💅✨\n👉 t.me/{CHANNEL_USERNAME.replace('@', '')}",
            ])
            await db.set_channel_subscribed(message.from_user.id, True)
        else:
            answer = "У Насти пока нет канала... Но скоро будет! 💅"
        await message.answer(answer)
        await _save_simple_exchange(message, text, answer, db)
        return

    # News question - Настя рассказывает ПОДРОБНО с ЭМОЦИЯМИ!
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
        answer = "Если Настя проснулась - все проснулись! 💅✨🔥"
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

    # Silent treatment (0.3% - very rare)
    if random.random() < 0.003:
        silent = random.choice(SILENT_TREATMENT)
        await message.answer(silent)
        await _save_simple_exchange(message, text, silent, db)
        return

    # ── "Дай ссылку" - search the web for real links, NOT channel link! ──
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
                # Generic request - tell user we couldn't find, offer to search
                answer = "Настя не нашла ссылку на это... Попробуй /search и я поищу в интернете! 🔍"
            await message.answer(answer)
            await _save_simple_exchange(message, text, answer, db)
        return

    # ── v44: URL UNDERSTANDING - Настя читает ссылки! ──
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

    # ── Normal AI chat - MOST conversations go here ──
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


# ════════════════════════════════════════════════════════════
#  v60: CONSULTATION AUTO-DETECTION - route consultation
#  requests from regular chat to proper handlers
# ════════════════════════════════════════════════════════════

def _detect_consultation_request(text: str):
    """Detect if a user's message is a consultation request in natural language.

    Returns (consultation_type, confidence) or None.
    consultation_type is one of: "humandesign", "astro", "numerology", "jyotish", "health"
    confidence is "high" (unambiguous keyword match) or "low" (ambiguous keyword)
    """
    if not text:
        return None

    text_lower = text.lower()

    # Score each consultation type by keyword matches
    scores = {}
    for ctype, keywords in _CONSULTATION_KEYWORDS.items():
        matched_keywords = [kw for kw in keywords if kw in text_lower]
        if matched_keywords:
            # Weight: ambiguous keywords count less
            total_weight = 0
            for kw in matched_keywords:
                if kw in _CONSULTATION_FALSE_POSITIVE_WORDS:
                    total_weight += 0.5  # ambiguous
                else:
                    total_weight += 1.0  # unambiguous
            scores[ctype] = total_weight

    if not scores:
        return None

    # Pick the highest scoring consultation type
    best_type = max(scores, key=scores.get)
    best_score = scores[best_type]

    # Need at least one unambiguous keyword or two ambiguous ones
    if best_score < 0.75:
        return None

    confidence = "high" if best_score >= 1.0 else "low"
    return (best_type, confidence)


def _extract_birth_data_from_text(text: str):
    """Extract birth date, time, and place from a message.

    Returns (day, month, year, birth_time, birth_place) or None.
    """
    from bot.consultations import parse_birth_date

    # Try to find a date in the text
    birth_date = parse_birth_date(text)
    if not birth_date:
        return None

    day, month, year = birth_date

    # Extract time
    birth_time = ""
    time_match = re.search(r'(\d{1,2}[:.]\d{2})', text)
    if time_match:
        birth_time = time_match.group(1).replace(".", ":")

    # Extract place (text after the date that's not a number)
    birth_place = ""
    # Try to find the date pattern in text and take what's after it
    date_patterns = [
        r'\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4}',
        r'\d{4}[./\-]\d{1,2}[./\-]\d{1,2}',
    ]
    for dp in date_patterns:
        dm = re.search(dp, text)
        if dm:
            after_date = text[dm.end():].strip()
            # Remove time if present
            if time_match:
                after_date = after_date.replace(time_match.group(0), "").strip()
            # If there's remaining text that looks like a place name
            parts = after_date.split()
            if parts:
                potential_place = " ".join(parts)
                if not potential_place.replace(".", "").replace("/", "").replace("-", "").replace(" ", "").isdigit():
                    birth_place = potential_place
            break

    return (day, month, year, birth_time, birth_place)


async def _handle_consultation_from_chat(
    message: Message, text: str, consultation_type: str,
    db, ai_router, confidence: str = "high"
) -> bool:
    """Handle a consultation request detected in regular chat.

    Routes to the appropriate consultation handler logic.
    Returns True if consultation was handled, False if it should fall through to general AI.
    """
    user_id = message.from_user.id

    # Try to extract birth data from the message
    extracted = _extract_birth_data_from_text(text)
    day = month = year = None
    birth_time = ""
    birth_place = ""

    if extracted:
        day, month, year, birth_time, birth_place = extracted

    # Fall back to stored birth data
    if not day and db:
        try:
            stored = await db.get_user_birth_data(user_id)
            if stored and stored.get("birth_day"):
                day, month, year = stored["birth_day"], stored["birth_month"], stored["birth_year"]
                if not birth_time:
                    birth_time = stored.get("birth_time", "")
                if not birth_place:
                    birth_place = stored.get("birth_place", "")
        except Exception:
            pass

    # ── HUMAN DESIGN ──
    if consultation_type == "humandesign":
        if not day:
            # No birth data — ask for it, but in a natural chat way
            await message.answer(
                "О, Дизайн Человека! Классная тема! 🧬✨\n\n"
                "Чтобы составить твой бодиграф, нужна дата рождения.\n"
                "Напиши так: /humandesign 15.06.2001\n"
                "С временем точнее: /humandesign 15.06.2001 14:30 Москва\n\n"
                "Или просто скажи дату, и Настя разберётся! 💅"
            )
            return True

        if not (1 <= day <= 31 and 1 <= month <= 12 and 1900 <= year <= 2020):
            await message.answer("Капец, дата какая-то странная... Проверь и попробуй снова! 🤔")
            return True

        if not ai_router:
            await message.answer("Настя пока не может составить Дизайн Человека... Попробуй позже! 🧬💅")
            return True

        # Save birth data
        if db:
            try:
                await db.save_user_birth_data(
                    user_id, day, month, year,
                    birth_time=birth_time,
                    birth_place=birth_place,
                    consultation_type="humandesign",
                )
            except Exception:
                pass

        await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
        await message.answer("Настя составляет твой Дизайн Человека! Это глубокая работа... 🧬✨")

        try:
            from bot.consultations import HD_SYSTEM_PROMPT_V3, build_humandesign_context

            hd_context = build_humandesign_context(day, month, year, birth_time, birth_place)

            prompt_parts = [f"Дата рождения: {day:02d}.{month:02d}.{year}"]
            if birth_time:
                prompt_parts.append(f"Время рождения: {birth_time}")
            if birth_place:
                prompt_parts.append(f"Место рождения: {birth_place}")

            if birth_time and birth_place:
                prompt_parts.append(
                    "\nВНИМАНИЕ: Указано время и место рождения! Ниже передан ПОЛНЫЙ РАСЧЁТ БОДИГРАФА "
                    "с точными Типом, Авторитетом, Профилем, Центрами, Каналами, Воротами и Переменными. "
                    "Используй ТОЛЬКО эти рассчитанные данные! НЕ придумывай другие ворота, каналы или типы! "
                    "Дай максимально развёрнутую ИНТЕРПРЕТАЦИЮ рассчитанных данных."
                )
            else:
                prompt_parts.append(
                    "\nВремя и место не указаны — расчёт основан только на дате рождения. "
                    "Ниже передан РАСЧЁТ БОДИГРАФА — используй ТОЛЬКО рассчитанные данные! "
                    "НЕ придумывай свои типы, ворота или каналы! "
                    "Для более точного расчёта (особенно Авторитета и центров) порекомендуй указать время рождения."
                )

            result = await ai_router.chat(
                prompt=f"Составь профессиональный разбор Дизайна Человека.\n\n" + "\n".join(prompt_parts) + f"\n\n{hd_context}",
                system_prompt=HD_SYSTEM_PROMPT_V3,
                max_tokens=6000,
                reasoning_effort="none",
            )

            if result and result.text:
                from ai.router import AIRouter
                cleaned = AIRouter.clean_ai_response(result.text)
                if cleaned:
                    response = f"🧬 Дизайн Человека: {day:02d}.{month:02d}.{year}\n\n{cleaned}"
                    await _send_long_message(message, response)
                    if db:
                        await _save_simple_exchange(message, text[:200], cleaned[:300], db)
                    return True
        except Exception as e:
            logger.error(f"Auto-detected HD consultation error: {e}")

        await message.answer("Ой, Настя не смогла прочитать Дизайн... Попробуй позже! 🧬😔")
        return True

    # ── ASTROLOGY ──
    elif consultation_type == "astro":
        if not day:
            await message.answer(
                "Астрология! Настя обожает! ⭐🔮\n\n"
                "Для натальной карты нужна дата рождения.\n"
                "Напиши: /astro 15.06.2001\n"
                "С временем точнее: /astro 15.06.2001 14:30 Москва\n\n"
                "Или просто скажи дату! 💅"
            )
            return True

        if not (1 <= day <= 31 and 1 <= month <= 12 and 1900 <= year <= 2020):
            await message.answer("Капец, дата какая-то странная... Проверь и попробуй снова! 🤔")
            return True

        if not ai_router:
            await message.answer("Настя пока не может составить натальную карту... Попробуй позже! ⭐💅")
            return True

        if db:
            try:
                await db.save_user_birth_data(
                    user_id, day, month, year,
                    birth_time=birth_time,
                    birth_place=birth_place,
                    consultation_type="astro",
                )
            except Exception:
                pass

        await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
        await message.answer("Настя составляет натальную карту! Серьёзная астрология, подожди... ⭐🔮")

        try:
            from bot.consultations import (
                get_zodiac_sign, ZODIAC_DETAILS, ASTRO_SYSTEM_PROMPT_V3,
                calculate_life_path_number, SOLAR_RETURN_INFO, build_astrology_context,
            )

            zodiac = get_zodiac_sign(day, month)
            sign_name = zodiac.capitalize() if zodiac else "Неизвестно"
            life_path = calculate_life_path_number(day, month, year)

            astro_context = build_astrology_context(day, month, year, birth_time, birth_place)

            prompt_parts = [f"Дата рождения: {day:02d}.{month:02d}.{year}"]
            if birth_time:
                prompt_parts.append(f"Время рождения: {birth_time}")
            if birth_place:
                prompt_parts.append(f"Место рождения: {birth_place}")
            prompt_parts.append(f"Знак зодиака: {sign_name}")
            prompt_parts.append(f"Число жизненного пути: {life_path}")

            if birth_time and birth_place:
                prompt_parts.append(
                    "\nВНИМАНИЕ: Указано время и место рождения! Составь НАИБОЛЕЕ ТОЧНЫЙ разбор натальной карты. "
                    "Рассчитай примерный Асцендент на основе времени и места рождения. "
                    "Определи положение планет в знаках и домах. Укажи ключевые аспекты. "
                    "Рассмотри текущие транзиты и солярное возвращение."
                )
            else:
                prompt_parts.append(
                    "\nВремя и место рождения НЕ указаны. Составь разбор на основе известных данных. "
                    "Определи вероятный Асцендент и общие характеристики. "
                    "Без точного времени дома и Асцендент приблизительны."
                )

            result = await ai_router.chat(
                prompt=f"Составь профессиональный астрологический разбор.\n\n" + "\n".join(prompt_parts) + f"\n\n{astro_context}",
                system_prompt=ASTRO_SYSTEM_PROMPT_V3,
                max_tokens=6000,
                reasoning_effort="none",
            )

            if result and result.text:
                from ai.router import AIRouter
                cleaned = AIRouter.clean_ai_response(result.text)
                if cleaned:
                    response = f"⭐ Натальная карта: {sign_name}, {day:02d}.{month:02d}.{year}\n\n{cleaned}"
                    await _send_long_message(message, response)
                    if db:
                        await _save_simple_exchange(message, text[:200], cleaned[:300], db)
                    return True
        except Exception as e:
            logger.error(f"Auto-detected Astro consultation error: {e}")

        await message.answer("Ой, Настя не смогла прочитать звёзды... Попробуй позже! ⭐😔")
        return True

    # ── NUMEROLOGY / MATRIX ──
    elif consultation_type == "numerology":
        if not day:
            await message.answer(
                "О, нумерология! Настя обожает числа! 🔮✨\n\n"
                "Для разбора нужна дата рождения.\n"
                "Напиши: /matrix 15.06.2001 — для Матрицы Судьбы\n"
                "Или: /numerology 15.06.2001 — для полного нумерологического разбора\n\n"
                "Или просто скажи дату! 💅"
            )
            return True

        if not (1 <= day <= 31 and 1 <= month <= 12 and 1900 <= year <= 2020):
            await message.answer("Капец, дата какая-то странная... Проверь и попробуй снова! 🤔")
            return True

        if not ai_router:
            await message.answer("Настя пока не может провести разбор... Попробуй позже! 🔮💅")
            return True

        if db:
            try:
                await db.save_user_birth_data(
                    user_id, day, month, year,
                    consultation_type="matrix",
                )
            except Exception:
                pass

        await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
        await message.answer("О, Настя составляет Матрицу Судьбы! Это серьёзная работа, подожди немного... 🔮✨")

        try:
            from bot.consultations import (
                calculate_matrix_of_destiny, get_matrix_prompt_params,
                MATRIX_SYSTEM_PROMPT, build_numerology_context,
            )

            matrix = calculate_matrix_of_destiny(day, month, year)
            prompt_data = get_matrix_prompt_params(matrix)
            numerology_context = build_numerology_context(day, month, year)

            result = await ai_router.chat(
                prompt=f"Составь профессиональный разбор Матрицы Судьбы.\n\n{prompt_data}\n\n{numerology_context}",
                system_prompt=MATRIX_SYSTEM_PROMPT,
                max_tokens=6000,
                reasoning_effort="none",
            )

            if result and result.text:
                from ai.router import AIRouter
                cleaned = AIRouter.clean_ai_response(result.text)
                if cleaned:
                    response = f"🔮 Матрица Судьбы для {day:02d}.{month:02d}.{year}\n\n{cleaned}"
                    await _send_long_message(message, response)
                    if db:
                        await _save_simple_exchange(message, text[:200], cleaned[:300], db)
                    return True
        except Exception as e:
            logger.error(f"Auto-detected Numerology consultation error: {e}")

        await message.answer("Ой, Настя не смогла прочитать Матрицу... Попробуй позже! 🔮😔")
        return True

    # ── JYOTISH ──
    elif consultation_type == "jyotish":
        if not day:
            await message.answer(
                "Джйотиш! Ведическая астрология! 🕉️✨\n\n"
                "Для карты Джанма-Кундали нужна дата рождения.\n"
                "Напиши: /jyotish 15.06.2001\n"
                "С временем и местом точнее: /jyotish 15.06.2001 14:30 Москва\n\n"
                "Или просто скажи дату! 💅"
            )
            return True

        if not (1 <= day <= 31 and 1 <= month <= 12 and 1900 <= year <= 2020):
            await message.answer("Капец, дата какая-то странная... Проверь и попробуй снова! 🤔")
            return True

        if not ai_router:
            await message.answer("Настя пока не может составить карту Джйотиш... Попробуй позже! 🕉️💅")
            return True

        if db:
            try:
                await db.save_user_birth_data(
                    user_id, day, month, year,
                    birth_time=birth_time,
                    birth_place=birth_place,
                    consultation_type="jyotish",
                )
            except Exception:
                pass

        await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
        await message.answer("Настя составляет карту Джанма-Кундали! Это серьёзная Ведическая астрология, подожди... 🕉️✨")

        try:
            from bot.consultations import (
                get_zodiac_sign, get_jyotish_rashi_approx,
                JYOTISH_SYSTEM_PROMPT, build_jyotish_context,
            )

            western_sign = get_zodiac_sign(day, month)
            vedic_rashi = get_jyotish_rashi_approx(western_sign)
            rashi_info = {"symbol": "", "ruler": "", "element": "", "quality": "", "traits": ""}
            jyotish_context = build_jyotish_context(day, month, year, birth_time, birth_place)
            try:
                from bot.consultations import JYOTISH_RASHIS
                rashi_info = JYOTISH_RASHIS.get(vedic_rashi, rashi_info)
            except Exception:
                pass

            prompt_parts = [
                f"Дата рождения: {day:02d}.{month:02d}.{year}",
                f"Западный знак зодиака: {western_sign.capitalize() if western_sign else 'Неизвестно'}",
                f"Приблизительный ведический Раши (сидерический): {vedic_rashi} ({rashi_info.get('symbol', '')})",
            ]
            if birth_time:
                prompt_parts.append(f"Время рождения: {birth_time}")
            if birth_place:
                prompt_parts.append(f"Место рождения: {birth_place}")

            if birth_time and birth_place:
                prompt_parts.append(
                    "\nВНИМАНИЕ: Указано время и место рождения! Составь НАИБОЛЕЕ точный разбор карты Джанма-Кундали. "
                    "Рассчитай примерную Лагну на основе времени и места рождения. "
                    "Определи положение всех Грах в Раши и Бхавах. "
                    "Укажи Джанма-Накшатру. Определи Атма-караку. Найди ключевые Йоги. "
                    "Определи текущую Махадашу. Рассмотри текущие транзиты Гочара."
                )
            else:
                prompt_parts.append(
                    "\nВремя и место рождения НЕ указаны. Составь разбор на основе известных данных. "
                    "Определи вероятную Лагну и общие характеристики. "
                    "Без точного времени Лагна и Бхавы приблизительны."
                )

            result = await ai_router.chat(
                prompt=f"Составь профессиональный разбор карты Джанма-Кундали (Джйотиш / Ведическая астрология).\n\n" + "\n".join(prompt_parts) + f"\n\n{jyotish_context}",
                system_prompt=JYOTISH_SYSTEM_PROMPT,
                max_tokens=6000,
                reasoning_effort="none",
            )

            if result and result.text:
                from ai.router import AIRouter
                cleaned = AIRouter.clean_ai_response(result.text)
                if cleaned:
                    response = f"🕉️ Джйотиш: {vedic_rashi} ({rashi_info.get('symbol', '')}), {day:02d}.{month:02d}.{year}\n\n{cleaned}"
                    await _send_long_message(message, response)
                    if db:
                        await _save_simple_exchange(message, text[:200], cleaned[:300], db)
                    return True
        except Exception as e:
            logger.error(f"Auto-detected Jyotish consultation error: {e}")

        await message.answer("Ой, Настя не смогла составить карту Джйотиш... Попробуй позже! 🕉️😔")
        return True

    # ── HEALTH / AYURVEDA ──
    elif consultation_type == "health":
        if not ai_router:
            await message.answer("Настя пока не может проконсультировать... Попробуй позже! 🌿💅")
            return True

        await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
        await message.answer("Настя анализирует твоё здоровье! Секунду... 🌿✨")

        try:
            from bot.consultations import HEALTH_SYSTEM_PROMPT_V3, build_health_context

            # Try to detect blood type from query
            blood_type_hint = ""
            blood_keywords = {
                "1 группа": "I (O)", "первая группа": "I (O)", "i группа": "I (O)", "0 группа": "I (O)",
                "2 группа": "II (A)", "вторая группа": "II (A)", "ii группа": "II (A)", "а группа": "II (A)",
                "3 группа": "III (B)", "третья группа": "III (B)", "iii группа": "III (B)", "b группа": "III (B)",
                "4 группа": "IV (AB)", "четвёртая группа": "IV (AB)", "четвертая группа": "IV (AB)", "iv группа": "IV (AB)", "ab группа": "IV (AB)",
            }
            for keyword, btype in blood_keywords.items():
                if keyword in text.lower():
                    blood_type_hint = btype
                    break

            health_context = build_health_context(symptoms=text, blood_type=blood_type_hint)

            result = await ai_router.chat(
                prompt=(
                    f"Опиши человека и дай профессиональную консультацию по здоровью.\n\n"
                    f"Описание человека: {text}\n\n"
                    f"{health_context}\n\n"
                    f"Определи вероятную доминирующую дошу, дай рекомендации по питанию, образу жизни, "
                    f"рассмотри психосоматические связи если есть симптомы. "
                    f"Если указана группа крови — обязательно рассмотри конституцию по группе крови. "
                    f"Сравни рекомендации Аюрведы и группы крови, найди общее. "
                    f"ОБЯЗАТЕЛЬНО напомни что ты не врач и при серьёзных проблемах нужно обратиться к специалисту."
                ),
                system_prompt=HEALTH_SYSTEM_PROMPT_V3,
                max_tokens=6000,
                reasoning_effort="none",
            )

            if result and result.text:
                from ai.router import AIRouter
                cleaned = AIRouter.clean_ai_response(result.text)
                if cleaned:
                    response = f"🌿 Консультация по здоровью\n\n{cleaned}"
                    await _send_long_message(message, response)
                    if db:
                        await _save_simple_exchange(message, text[:200], cleaned[:300], db)
                    return True
        except Exception as e:
            logger.error(f"Auto-detected Health consultation error: {e}")

        await message.answer("Ой, Настя не смогла проконсультировать... Попробуй позже! 🌿😔")
        return True

    # Unknown consultation type — fall through to general AI
    return False


async def _process_text_message(message: Message, text: str, db, ai_router,
                                 is_voice: bool = False, extra_suffix: str = "",
                                 extra_context: str = "", is_group: bool = False,
                                 url_context: str = "",
                                 skip_political_filter: bool = False) -> None:
    """Process text with AI. ALWAYS responds - even if all providers fail.

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
        user_context += " (парень - флирти, называй по имени, шути, интересуйся им)."
    elif gender == "female":
        user_context += " (девушка - как подруга, делись новостями, обсуждай)."
    else:
        user_context += "."
    if msg_count > 20:
        user_context += " Старый знакомый - можно откровеннее!"
    elif msg_count > 5:
        user_context += " Уже общались - помни что говорили раньше."
    else:
        user_context += " Новый собеседник - познакомься поближе."

    # v60: CONSULTATION AUTO-DETECTION
    # Check if this message is a consultation request BEFORE general AI processing.
    # If detected, route to the proper consultation handler instead of general AI chat.
    # IMPORTANT: Check consultation BEFORE political filter so consultation
    # responses are never blocked by political keyword false positives.
    consultation_detection = _detect_consultation_request(text)
    if consultation_detection:
        consultation_type, confidence = consultation_detection
        logger.info(f"Consultation auto-detected: type={consultation_type}, confidence={confidence}, user={user_id}")
        handled = await _handle_consultation_from_chat(
            message, text, consultation_type, db, ai_router, confidence=confidence
        )
        if handled:
            # Consultation was handled — clear processing task and return
            _user_processing.pop(user_id, None)
            return
        # If not handled (shouldn't happen normally), fall through to general AI

    # Interbot removed — each bot works independently

    # POLITICS FILTER (v61: moved AFTER consultation detection)
    # Only apply for non-consultation messages — consultation requests are handled separately
    _is_consultation_routed = bool(consultation_detection)
    political_keywords = ["путин", "зеленск", "байден", "трамп", "навальн", "войн",
                         "санкци", "нато", "политик", "депутат", "президент", "министр",
                         "религи", "конфликт", "террор", "бомб", "фашизм", "нацизм"]
    if not _is_consultation_routed and any(kw in text.lower() for kw in political_keywords):
        user_context += " Вопрос про политику - переведи тему!"

    # Build system prompt
    # v56: Date/time awareness - Настя знает какой сегодня день!
    _now = _moscow_now()
    _date_str = _now.strftime("%d.%m.%Y")
    _day_name = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"][_now.weekday()]
    _time_str = _now.strftime("%H:%M")
    _month_name = ["", "января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"][_now.month]
    _date_context = f" Сегодня {_day_name}, {_now.day} {_month_name} {_now.year} года, время {_time_str} МСК. Учитывай это - не пиши про старые новости как свежие."
    system_prompt = NASTYA_SYSTEM_PROMPT + f" Настроение: {mood}. Время: {time_mood}.{_date_context}"
    system_prompt += f" {user_context}"
    if extra_context:
        system_prompt += f" {extra_context}"
    # v44: URL context
    if url_context:
        system_prompt += f" {url_context}"

    # Add user birth data if available (for consultation context)
    if db:
        try:
            birth_data = await db.get_user_birth_data(user_id)
            if birth_data and birth_data.get("birth_day"):
                birth_context = (
                    f"\n\nДАННЫЕ ПОЛЬЗОВАТЕЛЯ (запомни, не переспрашивай): "
                    f"Дата рождения: {birth_data['birth_day']:02d}.{birth_data['birth_month']:02d}.{birth_data['birth_year']}"
                )
                if birth_data.get("birth_time"):
                    birth_context += f", Время: {birth_data['birth_time']}"
                if birth_data.get("birth_place"):
                    birth_context += f", Место: {birth_data['birth_place']}"
                if birth_data.get("blood_type"):
                    birth_context += f", Группа крови: {birth_data['blood_type']}"
                birth_context += (
                    f"\nКогда пользователь спрашивает про астрологию, нумерологию, Дизайн Человека или здоровье — "
                    f"используй эти данные и НЕ переспрашивай. Давай развёрнутые профессиональные ответы."
                )
                system_prompt += birth_context
        except Exception:
            pass

    # Group chat: active participation
    if is_group:
        system_prompt += " Мы в групповом чате - Настя активная участница! Отвечай живо и с интересом, 2-4 предложения. Можешь шутить, обсуждать, реагировать."

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
                system_prompt += f" Свежие новости: {'; '.join(news_parts)}. Если спрашиваешь про событие - давай ссылку!"
        _current_news_items = recent_news
    except Exception:
        pass

    # History
    history = []
    try:
        history = await db.get_history(user_id, limit=MODEL_HISTORY_LIMIT)
    except Exception:
        pass

    # ── Web search - ENHANCED v51: Force search for product/link requests! ──
    search_query = should_search(text)
    search_results = []
    is_product_search = False  # Track if this is a product/service search

    # v51: Detect product/service/link requests -> FORCE web search!
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
                            + "\n2. НЕ придумывай ссылки - если ссылки нет в результатах, НЕ пиши её!"
                            + "\n3. НЕ меняй путь URL - копируй ССЫЛКУ ТОЧНО как в результатах!"
                            + "\n4. Если результатов недостаточно - скажи что нашла не всё и предложи поискать ещё"
                            + "\n5. НЕ заменяй реальные ссылки на @chasnastya!"
                            + "\n6. НЕ добавляй выдуманные пути типа /catalog/product/12345 - это ВСЕГДА выдумка!"
                            + "\n7. Каждый товар/услугу сопровождай ТОЧНОЙ ссылкой из результатов поиска"
                        )
                    else:
                        system_prompt += f"\n\n🔍 НАСТЯ НАШЛА В ИНТЕРНЕТЕ (ОБЯЗАТЕЛЬНО используй эти данные и URL в ответе!):\n" + "\n".join(search_parts) + "\n\n⚠️ ВАЖНО: Включи ВСЕ найденные URL в свой ответ! НЕ заменяй их на @chasnastya!"
                logger.info(f"Web search for user {user_id}: '{search_query}' -> {len(search_results)} results (product={is_product_search})")
            elif is_product_search:
                # v51: If product search found nothing, tell AI to be honest
                system_prompt += (
                    "\n\n⚠️ Настя искала товары/услуги в интернете но НЕ НАШЛА результатов."
                    "\nНЕ придумывай ссылки! Скажи честно что не нашла и предложи поискать через /find"
                )
        except Exception as e:
            logger.warning(f"Web search error: {e}")

    # ── Partner links context ──
    # Nastya gives partner links naturally in conversation - not as ads, but as personal recommendations
    # v4: Uses get_all_relevant_links for cross-category coverage + 🔧 format
    try:
        # First: use the enhanced generate_partner_context which now includes
        # cross-category links via get_all_relevant_links
        partner_context = nastya_partner_manager.generate_partner_context(text, max_programs=5)
        if partner_context:
            system_prompt += f"\n\n{partner_context}"
            logger.info(f"Partner context added for user {user_id}: categories detected")
    except Exception as e:
        logger.warning(f"Partner context error: {e}")

    # ── Auto parts specific: if user mentions BMW or auto parts, add direct shop links ──
    # Настя водит BMW M3 — она знает где покупать запчасти!
    # v4: Now also adds links from ALL auto categories (tires, tools, insurance, checkauto)
    text_lower_for_parts = text.lower()
    auto_keywords = [
        "bmw", "бмв", "m3", "m4", "m5", "x5", "x3", "x6",  # BMW models
        "запчаст", "деталь", "артикул", "масло", "фильтр",
        "колодки", "ремонт", "сто ", "обслуживание", "то ", "регламент",
        "свечи", "ремень", "прокладк", "подшипник", "амортизатор",
        "двигател", "турбо", "кузов", "ходов", "тормоз", "рулев",
        "сцеплен", "коробк", "автомат", "механик", "привод",
        "автозапч", "автомагазин", "автотовар", "подбор запчас",
        "росско", "rossko", "автопитер", "autopiter", "avtoall", "автоолл",
        "авто", "машина", "тачк", "шины", "диски", "осаго", "каско",
    ]
    is_auto_topic = any(kw in text_lower_for_parts for kw in auto_keywords)
    if is_auto_topic:
        try:
            # get_auto_parts_links now includes ALL auto categories + Ася's partners
            auto_context = nastya_partner_manager.get_auto_parts_links(text)
            if auto_context and auto_context not in system_prompt:
                system_prompt += f"\n\n{auto_context}"
            # Add natural M3-owner context for BMW/car discussions
            bmw_keywords = ["bmw", "бмв", "m3", "m4", "m5", "рядн", "бавар"]
            if any(kw in text_lower_for_parts for kw in bmw_keywords):
                system_prompt += (
                    "\n\nНастя владеет BMW M3 и разбирается в запчастях! "
                    "Если разговор про BMW или авто — естественно упомяни где покупаешь запчасти для своей M3. "
                    "Например: 'Кстати, запчасти для своей M3 я обычно ищу тут: 🔧 Росско — [ссылка]' — "
                    "НЕ как реклама, а как личная рекомендация от владелицы BMW! "
                    "Формат ссылок: 🔧 Имя — URL. НЕ используй HTML!"
                )
        except Exception:
            pass

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

    # Call AI - use LOCAL-ONLY routing for group comments (saves cloud balance!)
    try:
        route_type = "comment" if is_group else "chat"
        result = await ai_router.chat(
            prompt=text, system_prompt=system_prompt, messages=history_with_current,
            route_type=route_type,
        )
        response_text = _clean_response(result.text)

    except Exception as e:
        logger.error(f"Chat error for user={user_id}: {e}")
        response_text = ai_router.get_fallback_response()
    finally:
        # Cancel delay task if still running
        delay_task.cancel()

    # ── POST-PROCESS: Filter political content ──
    # v62: Improved political filter - consultation responses are NEVER blocked.
    # If the request was routed through consultation handler OR detected as consultation
    # topic, the response should pass through regardless of keyword matches.
    # Consultation content may legitimately reference terms that overlap with
    # political keywords (e.g., "войн" in Vedic astrology context meaning "struggle",
    # "министр" in spiritual context, historical references in HD/Jyotish)
    _is_consultation_response = (
        skip_political_filter
        or _is_consultation_routed
        or any(kw in text.lower() for kws in _CONSULTATION_KEYWORDS.values() for kw in kws)
    )
    if not _is_consultation_response:
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
    elif not skip_political_filter:
        # For consultation responses, only filter on STRICTLY political words
        # (actual politician names, not ambiguous words like "войн" which appear in HD/Jyotish)
        strict_political_words = ["путин", "зеленск", "байден", "трамп", "навальн",
                                  "спецопер", "санкци", "госдум", "едро"]
        if any(kw in response_text.lower() for kw in strict_political_words):
            response_text = random.choice([
                f"Ой, Настя не про политику! Давай лучше про кино? 🎬💅",
                f"Настя аполитична! Давай про что-нибудь весёлое? 💅💕",
            ])
            logger.info(f"Filtered strict political content in consultation response for user {user_id}")

    # ── POST-PROCESS: Channel awareness - ONLY when specifically about channel ──
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
    # v51: ENHANCED - detect and remove hallucinated commercial URLs!
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

    # CRITICAL: Replace any plain partner URLs with affiliate goto_link equivalents
    response_text = _replace_plain_urls_with_affiliate(response_text)

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

    # ── GROUP CHAT: Limit response length to GROUP_COMMENT_MAX_CHARS ──
    if is_group and len(response_text) > GROUP_COMMENT_MAX_CHARS:
        # Cut at sentence boundary for group chats
        for sep in ['. ', '! ', '? ', '\n']:
            idx = response_text[:GROUP_COMMENT_MAX_CHARS].rfind(sep)
            if idx > 100:
                response_text = response_text[:idx + len(sep)].strip()
                break
        else:
            response_text = response_text[:GROUP_COMMENT_MAX_CHARS]

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

    # Send response - SMART SPLITTING
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
    """Post-process AI response to add news links - ONLY when specifically relevant.
    
    v50: Fixed - don't skip adding links just because t.me/ is in the response.
    The channel link t.me/chasnastya is NOT a news link.
    """
    if not news_items or not response_text:
        return response_text

    response_lower = response_text.lower()

    # v50: Only skip if the response already has real external links (not just channel link)
    _channel_url = f"t.me/{CHANNEL_USERNAME.replace('@', '')}" if CHANNEL_USERNAME else "t.me/chasnastya"
    external_links = re.findall(r'https?://\S+', response_text)
    # Filter out the channel link - it's not a news/product link
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
        text = re.sub(r'-\s*$', '', text, flags=re.MULTILINE)  # Trailing dashes
        text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


# Known partner domains that MUST use affiliate links, not plain URLs
_NASTYA_PARTNER_DOMAINS_MAP = {
    "rossko.ru": "Росско",
    "autopiter.ru": "Autopiter",
    "autopiter.kz": "Autopiter KZ",
    "avtoall.ru": "AvtoALL",
    "exist.ru": "Exist",
    "emex.ru": "Emex",
    "autodoc.ru": "Autodoc",
    "zzap.ru": "Zzap",
    "aliexpress.ru": "AliExpress",
    "avtocod.ru": "Avtocod",
    "petrolplus.ru": "PetrolPlus",
    "bs-tyres.ru": "BS-Tyres",
    "euro-diski.ru": "Euro-diski",
    "koleso.ru": "Колесо",
    "hyperauto.ru": "Hyperauto",
    "mirdvornikov.ru": "МирДворников",
    "globaldrive.ru": "Globaldrive",
    "lukoil-shop.com": "Лукойл",
    "ozon.ru": "Ozon",
    "wildberries.ru": "Wildberries",
    "aliexpress.com": "AliExpress",
    "raketa.fashion": "RAKETA",
}


def _replace_plain_urls_with_affiliate(text: str) -> str:
    """Replace any plain partner domain URLs with affiliate goto_link equivalents.
    
    When the AI generates responses containing plain URLs like rossko.ru or 
    autopiter.ru instead of the affiliate tracking links from admitad_ads.json,
    this function detects them and replaces with the proper goto_link.
    
    This handles cases where:
    - AI ignores the system prompt and invents plain URLs
    - AI uses domain names without the affiliate wrapper
    - Web search returns plain URLs that should be affiliate links
    """
    try:
        nastya_partner_manager.ensure_loaded()
    except Exception:
        return text
    
    for domain, display_name in _NASTYA_PARTNER_DOMAINS_MAP.items():
        prog = nastya_partner_manager.get_by_site(domain)
        if not prog or not prog.goto_link:
            continue
        
        affiliate_url = prog.goto_link
        
        # Pattern 1: Full URLs with paths — https://rossko.ru/search?text=abc
        pattern = rf'https?://{re.escape(domain)}[^\s<>)\]"\']*'
        matches = re.findall(pattern, text)
        for plain_url in matches:
            # Try to extract search query and build affiliate link with search
            search_query = ""
            if "search" in plain_url or "querystr" in plain_url or "q=" in plain_url:
                try:
                    from urllib.parse import urlparse, parse_qs
                    parsed = urlparse(plain_url)
                    params = parse_qs(parsed.query)
                    for key in ("text", "querystr", "q", "query", "SearchText", "keyword", "p"):
                        if key in params:
                            search_query = params[key][0]
                            break
                except Exception:
                    pass
            
            if search_query:
                replacement = prog.get_search_url(search_query)
            else:
                replacement = affiliate_url
            
            text = text.replace(plain_url, replacement)
        
        # Pattern 2: Bare domain mentions — rossko.ru (not already part of a longer URL)
        bare_pattern = rf'(?<![/\w.-])(?:www\.)?{re.escape(domain)}(?![/\w.-])'
        bare_matches = re.findall(bare_pattern, text)
        for bare_domain in bare_matches:
            # Check this isn't already inside an affiliate URL
            idx = text.find(bare_domain)
            if idx > 0:
                before = text[max(0, idx-50):idx]
                if any(tracking_domain in before for tracking_domain in ["ad.admitad.com", ".com/g/", "xmknb.com", "ujhjj.com", "rcpsj.com", "sgkaa.com"]):
                    continue
            text = text.replace(bare_domain, affiliate_url, 1)
    
    return text


def _clean_response(text: str) -> str:
    if not text:
        return "Ммм... Настя задумалась... 🤔"

    # Block structured YAML/MIDI/JSON output from text-to-music models
    structured_patterns = [
        r'^title:\s*.+?\n(duration|key|notation|pitch|velocity|tempo|bpm):',
        r'^---\s*\n.*?(title|duration|notation|pitch|velocity):',
        r'pitch,\s*time,\s*duration,\s*velocity',
    ]
    for pattern in structured_patterns:
        if re.search(pattern, text, re.DOTALL | re.IGNORECASE):
            logger.warning(f"Blocked structured/music output in chat response")
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
            # Remove the fake URL - AI hallucinated it
            text = text.replace(url, "")
            # Clean up any trailing "Ссылка:", "🔗", etc. after removed URL
            text = re.sub(r'\s*(?:Ссылк[аиу]:?|🔗)\s*$', '', text, flags=re.IGNORECASE)
            text = re.sub(r'\s*(?:Ссылк[аиу]:?|🔗)\s*\n', '\n', text, flags=re.IGNORECASE)
            logger.info(f"Removed hallucinated URL: {url[:50]}")

    # ── Replace @chasnastya when AI used it as a PRODUCT link replacement ──
    # If AI wrote "Ссылка: @chasnastya" or "🔗 @chasnastya" after a product - remove it
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
#  PROACTIVE MESSAGES - NEWS AWARE + TIME AWARE + EMOTIONAL
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
            # Save proactive message to chat history so AI knows what it said
            # when the user replies — prevents context loss and topic jumping
            try:
                if db:
                    await db.add_message(user_id, "assistant", proactive_text)
            except Exception:
                pass
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
                        # Save proactive message to chat history
                        try:
                            if db:
                                await db.add_message(uid, "assistant", msg)
                        except Exception:
                            pass
                        sent += 1
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"DB proactive error: {e}")


# ════════════════════════════════════════════════════════════
#  POLL ANSWER HANDLER - react when someone votes in polls!
# ════════════════════════════════════════════════════════════

@router.poll_answer()
async def handle_poll_answer(poll_answer: PollAnswer, db=None, ai_router=None) -> None:
    """React when someone votes in a channel poll - Nastya is interested!"""
    logger.info(f"Poll vote: user={poll_answer.user.id}, options={poll_answer.option_ids}")
