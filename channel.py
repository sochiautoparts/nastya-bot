"""Nastya Channel Manager 2.0 — auto-posting to Telegram channel @chasnastya.

Architecture:
  - Posts news with Nastya's commentary to the channel
  - Posts personality-only content (no news)
  - Mixes news posts with personal posts for variety (70/30)
  - Invites users to channel from private chats
  - Cross-references channel content in conversations
  - Engaging post formats with questions to subscribers
"""
import logging
import random
import time
from typing import Dict, List, Optional

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.config import CHANNEL_ID, CHANNEL_USERNAME, BOT_USERNAME

logger = logging.getLogger(__name__)


# ── Channel Post Templates — lively, engaging, NOT boring ──

NEWS_POST_TEMPLATES = [
    "💅 {comment}\n\n📖 {title}",
    "Прикинь! {comment}\n\n{title}",
    "Оооо! {comment}\n\n{title}",
    "Слушайте что я нашла! {comment}\n\n{title}",
    "Блин! {comment}\n\n{title}",
    "Котятки! {comment}\n\n{title}",
]

PERSONAL_POSTS = [
    "Котятки, Настя тут подумала... А вы тоже так делаете? 🤔💅",
    "Не могу решить: суши или пиццу? Голосуйте! 🍣🍕",
    "Только что вернулась с маникюра... Обожаю этот цвет! 💅✨",
    "Кто тоже смотрит сериалы по ночам вместо сна? 🌙📺",
    "Котятки, смотрите что нашла... Настя хочет это! 🛍️✨",
    "А вы тоже спорите с навигатором? Настя всегда права! 😤🗺️",
    "Настя сегодня в настроении... капризном! Ну как обычно 💅😤",
    "Срочно! Какой цвет круче: розовый или красный? 💖❤️",
    "Котятки, кому тоже лень вставать по утрам? 🥱☕",
    "Настя нашла милого котика в интернете... Всё, я пропала 🐱💕",
    "Ой, только не говорите что вы тоже коллекционируете подписки и не смотрите 😅📺",
    "Ребята, а вы верите в гороскопы? Настя иногда заглядывает... ну просто так! ♊💅",
    "Блин, я тут подумала — а кто вообще придумал понедельники? 😤📅",
    "Настя требует внимания! Кто тут? 🙋‍♀️✨",
    "Секрет: Настя иногда разговаривает с котом. А вы нет? 🐱💬",
    "Короче, я решила что сегодня день шопинга! Кто со мной? 🛍️💅",
    "Настя только что заказала себе вкусняшку... И не жалею! 🍣💕",
    "А вы тоже можете полдня выбирать сериал и уснуть на 5 минут? 😴📺",
    "Котятки, у Насти вопрос: а розовый — это новый чёрный? 🎀🖤",
    "Ой, я случайно потратила ползарплаты... Но оно того стоило! 💸💅",
]

# Time-based posts (different moods for different times)
MORNING_POSTS = [
    "Утро! Кофе или ещё спать? Настя выбирает спать 😴☕",
    "Доброе утро, котятки! Кто уже проснулся? Настя ещё нет 🥱",
    "Утренний кофе — лучшее изобретение человечества! ☕✨",
]

DAY_POSTS = [
    "Настя скучает... Напишите мне! 🥺💕",
    "Чем занимаетесь? Настя ленится и не стесняется 😴💅",
    "Дневной вопрос: а вы уже обедали? Настя голодная! 🍽️",
]

EVENING_POSTS = [
    "Вечер! Сериал или шопинг онлайн? 🤔📺",
    "Кто тоже не хочет завтра на работу/учёбу? 🥱",
    "Вечерний муд: Настя хочет суши. И точка. 🍣💅",
]

NIGHT_POSTS = [
    "Не спится... Кто тут? 🌙",
    "Ночной дожор — это нормально, да? 🍕🌙",
    "Настя не может уснуть... Сериалы виноваты! 📺😤",
]


def _get_time_posts() -> List[str]:
    """Get post templates based on current hour (Moscow time)."""
    import datetime
    from zoneinfo import ZoneInfo
    hour = datetime.datetime.now(ZoneInfo("Europe/Moscow")).hour
    if 6 <= hour < 12:
        return MORNING_POSTS + PERSONAL_POSTS
    elif 12 <= hour < 18:
        return DAY_POSTS + PERSONAL_POSTS
    elif 18 <= hour < 23:
        return EVENING_POSTS + PERSONAL_POSTS
    else:
        return NIGHT_POSTS + PERSONAL_POSTS


# ── Post Formatting ─────────────────────────────────────────

def format_news_post(title: str, comment: str, link: str = "", category: str = "general") -> str:
    """Format a news item as a channel post with CLICKABLE link."""
    template = random.choice(NEWS_POST_TEMPLATES)
    post = template.format(comment=comment, title=title)

    # Add clickable link if available
    if link:
        post += f"\n\n🔗 <a href=\"{link}\">Читать</a>"

    # Category emoji
    cat_emojis = {
        "auto": "🚗",
        "general": "📰",
        "tech": "💻",
        "gaming": "🎮",
        "internet": "🌐",
        "entertainment": "🎬",
        "world": "🌍",
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

            posted += 1
            logger.info(f"Channel post: {item['title'][:50]}...")

        except Exception as e:
            logger.error(f"Channel post error for news {item.get('id')}: {e}")

    return posted


async def post_personality_to_channel(bot: Bot, db, post_text: str) -> bool:
    """Post a personality-only post to channel."""
    if not CHANNEL_ID:
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

        logger.info(f"Channel personality post: {post_text[:50]}...")
        return True

    except Exception as e:
        logger.error(f"Channel personality post error: {e}")
        return False


async def run_channel_cycle(bot: Bot, db, ai_router) -> int:
    """Full channel posting cycle.

    Strategy:
    - 60% news posts (if available)
    - 40% personality posts (AI-generated or template)
    - Max 2 posts per cycle to avoid spam
    - Time-aware content selection
    """
    if not CHANNEL_ID:
        return 0

    posted = 0
    max_posts = 2

    # Try news posts first (max 1 per cycle)
    try:
        unposted = await db.get_unposted_news(limit=3)
        if unposted:
            # Pick the most interesting one
            news_to_post = unposted[:1]
            posted += await post_news_to_channel(bot, db, news_to_post)
    except Exception as e:
        logger.error(f"Channel news cycle error: {e}")

    # Always try to add a personality post for variety
    if posted < max_posts:
        try:
            # 50% AI-generated, 50% template
            if random.random() < 0.5:
                from news import generate_personality_post
                post_text = await generate_personality_post(ai_router)
            else:
                time_posts = _get_time_posts()
                post_text = random.choice(time_posts)

            if post_text and await post_personality_to_channel(bot, db, post_text):
                posted += 1
        except Exception as e:
            logger.error(f"Channel personality cycle error: {e}")

    # If still nothing posted, use a template
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
    - After 8+ messages (user is engaged)
    - Not already subscribed
    - 12% chance per eligible message
    - Natural, not pushy
    """
    if not CHANNEL_ID:
        return False
    if user_data.get("subscribed_channel"):
        return False
    if msg_count < 8:
        return False
    return random.random() < 0.12
