"""Nastya Channel Manager 5.0 — diverse, substantive posts to @chasnastya.

Architecture:
  - Posts news with Nastya's commentary to the channel
  - Posts personality-only content (no news) — EXPANDED variety
  - Mixes news posts with personal posts for variety (50/50)
  - Deduplication: tracks recent posts, avoids repeats
  - Posts more frequently — events are happening all the time!
  - Knowledge posts: interesting facts, quizzes, polls
  - Time-aware content: morning/day/evening/night moods
  - Invites users to channel from private chats
  - Cross-references channel content in conversations

v5.0: More diverse posts — NOT just about shoes!
  - Event reactions: world events, tech, sports, politics
  - Substantive posts with context and opinion
  - More knowledge and fact posts
  - Less repetitive fashion/shopping content
"""
import logging
import random
import time
from typing import Dict, List, Optional

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.config import CHANNEL_ID, CHANNEL_USERNAME, BOT_USERNAME, KNOWLEDGE_TOPICS

logger = logging.getLogger(__name__)

# Track recently posted texts to avoid repetition
_recent_posts: List[str] = []
_MAX_RECENT = 50


def _is_recent_post(text: str) -> bool:
    """Check if this exact post (or very similar) was recently made."""
    text_lower = text.lower().strip()[:100]  # First 100 chars for comparison
    for recent in _recent_posts:
        recent_lower = recent.lower().strip()[:100]
        # Check for exact match or high overlap
        if text_lower == recent_lower:
            return True
        # Check if 80% of words overlap
        text_words = set(text_lower.split())
        recent_words = set(recent_lower.split())
        if text_words and recent_words:
            overlap = len(text_words & recent_words) / max(len(text_words), len(recent_words))
            if overlap > 0.8:
                return True
    return False


def _track_post(text: str) -> None:
    """Track a post to avoid repetition."""
    _recent_posts.append(text)
    while len(_recent_posts) > _MAX_RECENT:
        _recent_posts.pop(0)


# ── Channel Post Templates — lively, engaging, NOT boring ──

NEWS_POST_TEMPLATES = [
    "💅 {comment}\n\n📖 {title}",
    "Прикинь! {comment}\n\n{title}",
    "Оооо! {comment}\n\n{title}",
    "Слушайте что я нашла! {comment}\n\n{title}",
    "Блин! {comment}\n\n{title}",
    "Котятки! {comment}\n\n{title}",
    "Точняк, надо знать! {comment}\n\n{title}",
    "Жесть! {comment}\n\n{title}",
    "Офигеть! {comment}\n\n{title}",
    "Кайф! {comment}\n\n{title}",
    "Реально?! {comment}\n\n{title}",
    "Капец! {comment}\n\n{title}",
]

# ── EXPANDED personality posts — DIVERSE topics, not just shoes! ──

PERSONAL_POSTS = [
    # Morning
    "Утро! Кофе или ещё спать? Настя выбирает спать 😴☕",
    "Доброе утро! Кто уже проснулся? Настя ещё нет 🥱",
    "Кофе — лучшее изобретение! Вот тут Настя согласна ☕✨",
    "Утренний совет от Насти: не вставайте. Просто не вставайте 🛏️💅",
    # Day
    "Настя скучает... Напишите мне! 🥺💕",
    "Чем занимаетесь? Настя ленится и не стесняется 😴💅",
    "Дневной вопрос: а вы уже обедали? Настя голодная! 🍽️",
    "Кто сегодня работает? Настя типа тоже... ну, лежит и скроллит 📱💅",
    # Evening
    "Вечер! Сериал или шопинг онлайн? 🤔📺",
    "Кто тоже не хочет завтра на работу/учёбу? 🥱",
    "Вечерний муд: Настя хочет суши. И точка. 🍣💅",
    "Вечерние мысли: почему сериалы лучше реальной жизни? 🤔📺",
    # Night
    "Не спится... Кто тут? 🌙",
    "Ночной дожор — это нормально, да? 🍕🌙",
    "Настя не может уснуть... Сериалы виноваты! 📺😤",
    "Ночная философия: если Настя не видела луну — она существует? 🌙🤔",
    # Event reactions — DIVERSE topics!
    "Прикинь, что в мире творится! Настя в шоке 😱🌍",
    "Офигеть, вы видели что сегодня произошло?! Настя не верит! 😤📰",
    "Жесть, какие события! Настя следит за всем 👀🔥",
    "Капец, сегодня день был! Настя даже не успела всё прочитать 📱",
    "Точняк, это надо обсудить! Кто что думает? 🤔💬",
    "Реально, мир сходит с ума! Настя в курсе и шокирована 😤",
    # Personality — varied topics, NOT just shoes!
    "Котятки, Настя тут подумала... А вы тоже так делаете? 🤔💅",
    "Не могу решить: суши или пиццу? Голосуйте! 🍣🍕",
    "Только что вернулась с маникюра... Обожаю этот цвет! 💅✨",
    "Кто тоже смотрит сериалы по ночам вместо сна? 🌙📺",
    "Котятки, смотрите что нашла... Настя хочет это! 🛍️✨",
    "А вы тоже спорите с навигатором? Настя всегда права! 😤🗺️",
    "Настя сегодня в настроении... капризном! Ну как обычно 💅😤",
    "Срочно! Какой цвет круче: розовый или красный? 💖❤️",
    "Настя нашла милого котика в интернете... Всё, я пропала 🐱💕",
    "Ребята, а вы верите в гороскопы? Настя иногда заглядывает... ♊💅",
    "Блин, я тут подумала — а кто вообще придумал понедельники? 😤📅",
    "Настя требует внимания! Кто тут? 🙋‍♀️✨",
    "Секрет: Настя иногда разговаривает с котом 🐱💬",
    "Короче, я решила что сегодня день шопинга! Кто со мной? 🛍️💅",
    "Настя только что заказала себе вкусняшку... И не жалею! 🍣💕",
    "А вы тоже можете полдня выбирать сериал и уснуть на 5 минут? 😴📺",
    # Technology & science
    "Настя тут прочитала про нейросети... Они нас заменят?! 😱💻",
    "Прикинь, ИИ уже картины рисует! Настя тоже так может... ну, почти 🎨🤖",
    "Технологии — это магия! Настя в этом уверена 💻✨",
    "Кто уже попробовал ChatGPT? Настя ревнует! 😤🤖",
    # News & events
    "Настя следит за новостями! Вы тоже? Что думаете про последние события? 📰🤔",
    "Ой, только что прочитала новость! Настя в шоке! Пишите в комменты 👀🔥",
    "Котятки, вы в курсе что происходит? Настя в курсе и делится! 📰💅",
    # Sports & active
    "Настя решила заняться спортом... завтра 😤🏃‍♀️",
    "Кто смотрел матч? Настя типа болеет... за красивых! ⚽💅",
    # Psychology & deep
    "А вы тоже думаете о смысле жизни в 3 часа ночи? Настя да! 🌙🤔",
    "Психология говорит: Настя всегда права. Наука не ошибается! 💅🧠",
    "Интересный факт: люди, которые поздно ложатся, креативнее. Настя — сова! 🦉✨",
    # Cooking & food
    "Секрет Насти: лучшая еда — это чужая еда. Доставка, я люблю тебя! 🍕💕",
    "Настя открыла для себя матча... Теперь я эстет! 🍵✨",
    "Тирамису значит 'подними меня'. Именно так Настя чувствует после него 🍰",
    # Travel
    "Настя хочет на море! Прям щас! Кто со мной? 🏖️✈️",
    "Стамбул, Дубай, Бали... Настя хочет везде! 🌍💅",
    "Сочи — летняя столица! Настя знает! 🌴☀️",
    # New vocabulary-based posts
    "Точняк, сегодня тот день когда хочется всё и сразу! Кто со мной? 😤✨",
    "Офигеть, я только узнала что... ладно, в следующем посте расскажу! 👀💅",
    "Жесть, котятки! Спорим вы не знали этот факт? 🤔💡",
    "Реально, кто придумал вставать рано? Настя протестует! 😤🛏️",
    "Неа, я не ленивая. Я энергосберегающая! 💅😴",
    "Фигушки, Настя не будет сегодня готовить! Доставка — моё всё! 🍕💅",
]

# ── Knowledge posts — inject interesting facts! ──

KNOWLEDGE_POST_TEMPLATES = [
    "Прикинь, Настя только узнала! {fact} 🤯",
    "Котятки, а вы знали что {fact}? Настя в шоке! 😱",
    "Точняк не знали! {fact} 🤓💅",
    "Офигеть! {fact} Настя теперь самая умная! 💡💅",
    "Жесть факт! {fact} 🤯✨",
    "Реально?! {fact} Настя не верит! 😱💅",
    "Капец, вот это да! {fact} 🤯✨",
    "Знание дня! {fact} Настя в шоке! 💡🤯",
]

# ── Quiz/poll posts ──

QUIZ_POSTS = [
    "Котятки, опрос! 📊\n\nНастя или Алиса — кто лучше? 💅🤖\n\nПишите в комменты!",
    "Срочно! 🚨\n\nСуши или пицца? Настя не может решить! 🍣🍕\n\nГолосуйте!",
    "Опрос дня! 📊\n\nКакой знак зодиака самый капризный? ♊😤\n\nНастя точно знает ответ!",
    "Котятки, важный вопрос! 🤔\n\nШопинг онлайн или в магазине? 🛍️🏬\n\nНастя за онлайн!",
    "Быстрый опрос! ⚡\n\nКофе или чай? Настя кофе! ☕\n\nА вы?",
    "Котятки, решите спор! 😤\n\nМаникюр гель или обычный? 💅\n\nНастя за гель, точняк!",
    "Опрос! 📊\n\nКакой сериал лучше? Настя выбирает... ну, все! 📺\n\nПишите!",
    "Кто круче? 🤔\n\nКотики или собачки? Настя за котиков! 🐱🐶\n\nГолосуйте!",
]

# ── Event reaction posts — react to what's happening! ──

EVENT_REACTION_POSTS = [
    "Вы видели что происходит?! Настя в шоке! 😱🔥",
    "Офигеть, какие новости! Настя не может молчать! 😤📰",
    "Капец, сегодня день! Настя следит за всем 👀✨",
    "Точняк, это надо обсудить! Что вы думаете? 🤔💬",
    "Жесть, мир с ума сошёл! Настя в курсе 😤🌍",
    "Прикинь, что творится! Настя не верит своим глазам! 😱",
    "Реально, события невероятные! Настя обсуждает в комментах! 💬🔥",
]

# ── Channel promo posts (invite to bot) ──

PROMO_POSTS = [
    "Кстати, со мной можно поболтать лично! Жми кнопку ниже 👇💅",
    "Хочешь обсудить? Настя на связи! Кнопка внизу 👀✨",
    "Настя не только постит — она и болтает! Попробуй! 💅💬",
]


def _get_time_posts() -> List[str]:
    """Get post templates based on current hour (Moscow time)."""
    import datetime
    from zoneinfo import ZoneInfo
    hour = datetime.datetime.now(ZoneInfo("Europe/Moscow")).hour
    if 6 <= hour < 12:
        return PERSONAL_POSTS[:4] + PERSONAL_POSTS[11:]  # Morning + general
    elif 12 <= hour < 18:
        return PERSONAL_POSTS[4:8] + PERSONAL_POSTS[11:]  # Day + general
    elif 18 <= hour < 23:
        return PERSONAL_POSTS[8:12] + PERSONAL_POSTS[11:]  # Evening + general
    else:
        return PERSONAL_POSTS[12:16] + PERSONAL_POSTS[11:]  # Night + general


# ── Post Formatting ─────────────────────────────────────────

def format_news_post(title: str, comment: str, link: str = "", category: str = "general") -> str:
    """Format a news item as a channel post with CLICKABLE link."""
    template = random.choice(NEWS_POST_TEMPLATES)
    post = template.format(comment=comment, title=title)

    # Add clickable link if available
    if link:
        post += f"\n\n🔗 <a href=\"{link}\">Читать</a>"
    else:
        post += f"\n\n📺 Подробнее в @chasnastya"

    # Category emoji
    cat_emojis = {
        "auto": "🚗",
        "general": "📰",
        "tech": "💻",
        "gaming": "🎮",
        "internet": "🌐",
        "entertainment": "🎬",
        "world": "🌍",
        "science": "🔬",
    }
    cat_emoji = cat_emojis.get(category, "📰")
    post += f"\n{cat_emoji} #{category.capitalize()}"

    # Always add channel signature
    if CHANNEL_USERNAME:
        post += f" | @{CHANNEL_USERNAME}"

    return post


def format_personality_post(text: str) -> str:
    """Format a personality post for the channel."""
    post = text

    # Add signature
    if CHANNEL_USERNAME:
        post += f"\n\n💅 @{CHANNEL_USERNAME}"

    return post


def format_knowledge_post(fact: str) -> str:
    """Format a knowledge fact as a channel post."""
    template = random.choice(KNOWLEDGE_POST_TEMPLATES)
    post = template.format(fact=fact)

    if CHANNEL_USERNAME:
        post += f"\n\n💅 @{CHANNEL_USERNAME}"

    return post


# ── Channel Posting ─────────────────────────────────────────

async def post_news_to_channel(bot: Bot, db, news_items: List[Dict]) -> int:
    """Post unposted news items to channel. Returns count of posts made."""
    if not CHANNEL_ID:
        logger.debug("No CHANNEL_ID configured, skipping channel post")
        return 0

    posted = 0
    for item in news_items:
        try:
            post_text = format_news_post(
                title=item["title"],
                comment=item.get("nastya_comment", "Интересно..."),
                link=item.get("link", ""),
                category=item.get("category", "general"),
            )

            # Skip if recently posted (dedup)
            if _is_recent_post(post_text):
                logger.debug(f"Skipping duplicate post: {item['title'][:50]}...")
                continue

            # Add inline button to discuss with Nastya bot
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="💬 Обсудить с Настей",
                    url=f"https://t.me/{BOT_USERNAME}",
                )],
            ])

            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=post_text,
                reply_markup=keyboard,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

            # Mark as posted
            await db.mark_news_posted(item["id"])
            await db.add_channel_post(
                news_id=item["id"],
                post_text=post_text,
                post_type="news",
            )
            _track_post(post_text)

            posted += 1
            logger.info(f"Channel post: {item['title'][:50]}...")

        except Exception as e:
            logger.error(f"Channel post error for news {item.get('id')}: {e}")

    return posted


async def post_personality_to_channel(bot: Bot, db, post_text: str) -> bool:
    """Post a personality-only post to channel."""
    if not CHANNEL_ID:
        return False

    # Skip if recently posted (dedup)
    if _is_recent_post(post_text):
        logger.debug("Skipping duplicate personality post")
        return False

    try:
        formatted = format_personality_post(post_text)

        # Add discussion button for personality posts too
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="💬 Написать Насте",
                url=f"https://t.me/{BOT_USERNAME}",
            )],
        ])

        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=formatted,
            reply_markup=keyboard,
        )

        await db.add_channel_post(
            news_id=0,
            post_text=formatted,
            post_type="personality",
        )
        _track_post(post_text)

        logger.info(f"Channel personality post: {post_text[:50]}...")
        return True

    except Exception as e:
        logger.error(f"Channel personality post error: {e}")
        return False


async def post_knowledge_to_channel(bot: Bot, db, fact: str) -> bool:
    """Post a knowledge fact to channel."""
    if not CHANNEL_ID:
        return False

    post_text = format_knowledge_post(fact)

    if _is_recent_post(post_text):
        return False

    try:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="💬 Спросить Настю",
                url=f"https://t.me/{BOT_USERNAME}",
            )],
        ])

        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=post_text,
            reply_markup=keyboard,
        )

        await db.add_channel_post(
            news_id=0,
            post_text=post_text,
            post_type="knowledge",
        )
        _track_post(post_text)

        logger.info(f"Channel knowledge post: {fact[:50]}...")
        return True

    except Exception as e:
        logger.error(f"Channel knowledge post error: {e}")
        return False


async def run_channel_cycle(bot: Bot, db, ai_router) -> int:
    """Full channel posting cycle.

    Strategy:
    - 45% news posts (if available) — MORE news, more events!
    - 20% personality posts (AI-generated or template)
    - 20% knowledge posts (interesting facts)
    - 10% event reaction posts
    - 5% quiz/poll posts
    - Max 3 posts per cycle to keep channel active
    - Deduplication: never repeat same content
    - Time-aware content selection
    """
    if not CHANNEL_ID:
        return 0

    posted = 0
    max_posts = 3
    roll = random.random()

    # Try news posts first (45% chance, max 1 per cycle) — MORE NEWS!
    if roll < 0.45:
        try:
            unposted = await db.get_unposted_news(limit=5)
            if unposted:
                # Pick a random one for variety
                news_to_post = [random.choice(unposted)]
                posted += await post_news_to_channel(bot, db, news_to_post)
        except Exception as e:
            logger.error(f"Channel news cycle error: {e}")

    # Personality posts (20% chance)
    if posted < max_posts and (roll >= 0.45 or posted == 0):
        try:
            # 60% AI-generated, 40% template
            if random.random() < 0.60:
                from news import generate_personality_post
                post_text = await generate_personality_post(ai_router)
            else:
                time_posts = _get_time_posts()
                post_text = random.choice(time_posts)

            if post_text and await post_personality_to_channel(bot, db, post_text):
                posted += 1
        except Exception as e:
            logger.error(f"Channel personality cycle error: {e}")

    # Knowledge posts (20% chance) — MORE FACTS!
    if posted < max_posts and random.random() < 0.50:
        try:
            # Pick a random topic and fact — avoid only fashion/shoes!
            # Weight topics: more tech, science, psychology, fun_facts, auto
            weighted_topics = [
                "tech", "fun_facts", "psychology", "auto", "science" if "science" in KNOWLEDGE_TOPICS else "fun_facts",
                "cinema", "cooking", "relationships", "travel", "moscow",
                "fashion", "zodiac",
            ]
            available_topics = [t for t in weighted_topics if t in KNOWLEDGE_TOPICS]
            if available_topics:
                topic_key = random.choice(available_topics)
                topic_data = KNOWLEDGE_TOPICS[topic_key]
                fact = random.choice(topic_data["facts"])
                if await post_knowledge_to_channel(bot, db, fact):
                    posted += 1
        except Exception as e:
            logger.error(f"Channel knowledge cycle error: {e}")

    # Event reaction posts (10% chance)
    if posted < max_posts and random.random() < 0.25:
        try:
            reaction = random.choice(EVENT_REACTION_POSTS)
            if await post_personality_to_channel(bot, db, reaction):
                posted += 1
        except Exception as e:
            logger.error(f"Channel event reaction error: {e}")

    # Quiz posts (5% chance)
    if posted < max_posts and random.random() < 0.15:
        try:
            quiz = random.choice(QUIZ_POSTS)
            if await post_personality_to_channel(bot, db, quiz):
                posted += 1
        except Exception as e:
            logger.error(f"Channel quiz cycle error: {e}")

    # Promo posts (3% chance — rare, not spammy)
    if posted < max_posts and random.random() < 0.03:
        try:
            promo = random.choice(PROMO_POSTS)
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="💬 Написать Насте",
                    url=f"https://t.me/{BOT_USERNAME}",
                )],
            ])
            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=promo,
                reply_markup=keyboard,
            )
            posted += 1
        except Exception as e:
            logger.error(f"Channel promo post error: {e}")

    # If still nothing posted, use a template (fallback)
    if posted == 0:
        try:
            time_posts = _get_time_posts()
            template_post = random.choice(time_posts)
            if await post_personality_to_channel(bot, db, template_post):
                posted += 1
        except Exception as e:
            logger.error(f"Channel template post error: {e}")

    logger.info(f"Channel cycle: {posted} posts made")
    return posted


# ── Channel Invite in Conversations ─────────────────────────

CHANNEL_INVITE_PHRASES = [
    "Кстати, у меня есть канал! Загляни — не пожалеешь! 💅✨",
    "Я там пост написала, заходи посмотри! 👀",
    "Подписывайся на мой канал, я там самое интересное нахожу! 💋",
    "Хочешь знать что я думаю про всё? Мой канал — туда! 💅",
    "А ты подписан на мой канал? Там весело! 🎀",
    "Заходи ко мне на канал, я сегодня злая и интересная! 😤✨",
    "Кстати, мой канал @chasnastya — там я настоящая! 💅",
    "Мой канал живёт! Подпишись, а? 🥺✨",
    "Точняк, подписывайся на @chasnastya! Там кайф! 💅✨",
    "Офигеть, у меня канал есть! @chasnastya — заходи! 💅🔥",
    "Я про это в @chasnastya написала! Заходи читай! 📰💅",
    "Подписывайся на @chasnastya — я там новости и факты постю! 📺✨",
]

CHANNEL_DISCUSSION_PHRASES = [
    "Прикинь, я тут читала... {reaction}",
    "Ты видел что случилось?! {reaction}",
    "Ой, я сейчас в шоке... {reaction}",
    "Слушай, я тут узнала... {reaction}",
    "Короче, новость дня! {reaction}",
    "Блин, представляешь?! {reaction}",
    "Серьёзно?! {reaction}",
    "Вау, вот это да! {reaction}",
    "Точняк, я об этом читала! {reaction}",
    "Офигеть, прикинь! {reaction}",
]


def get_channel_invite() -> str:
    """Get a random channel invite phrase."""
    invite = random.choice(CHANNEL_INVITE_PHRASES)
    if CHANNEL_USERNAME:
        invite += f"\n👉 t.me/{CHANNEL_USERNAME.replace('@', '')}"
    return invite


def get_news_discussion(news_comment: str) -> str:
    """Get a phrase to discuss a news item in conversation."""
    template = random.choice(CHANNEL_DISCUSSION_PHRASES)
    return template.format(reaction=news_comment)


def should_invite_to_channel(user_data: Dict, msg_count: int) -> bool:
    """Determine if we should invite this user to the channel.

    Strategy:
    - After 6+ messages (user is engaged)
    - Not already subscribed
    - 15% chance per eligible message
    - Natural, not pushy
    """
    if not CHANNEL_ID:
        return False
    if user_data.get("subscribed_channel"):
        return False
    if msg_count < 6:
        return False
    return random.random() < 0.15
