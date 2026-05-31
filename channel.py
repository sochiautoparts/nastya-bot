"""Nastya Channel Manager — auto-posting to Telegram channel.

Architecture:
  - Posts news with Nastya's commentary to the channel
  - Posts personality-only content (no news)
  - Mixes news posts with personal posts for variety
  - Invites users to channel from private chats
  - Cross-references channel content in conversations
"""
import logging
import random
import time
from typing import Dict, List, Optional

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.config import CHANNEL_ID, CHANNEL_USERNAME

logger = logging.getLogger(__name__)


# ── Channel Post Templates ──────────────────────────────────

NEWS_POST_TEMPLATES = [
    "🔥 {comment}\n\n{title}",
    "📱 {comment}\n\n{title}",
    "⚡ {comment}\n\n{title}",
    "👀 {comment}\n\n{title}",
    "💅 {comment}\n\n{title}",
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
]


# ── Post Formatting ─────────────────────────────────────────

def format_news_post(title: str, comment: str, link: str = "", category: str = "general") -> str:
    """Format a news item as a channel post."""
    template = random.choice(NEWS_POST_TEMPLATES)
    post = template.format(comment=comment, title=title)

    # Add link if available
    if link:
        post += f"\n\n🔗 Читать"

    # Category emoji
    cat_emojis = {
        "general": "📰",
        "tech": "💻",
        "gaming": "🎮",
        "internet": "🌐",
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

            # Add inline button to discuss with Nastya
            keyboard = None
            if CHANNEL_USERNAME:
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="💬 Обсудить с Настей",
                        url=f"https://t.me/{CHANNEL_USERNAME.replace('@', '')}",
                    )],
                ])

            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=post_text,
                reply_markup=keyboard,
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

        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=formatted,
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
    - 70% news posts (if available)
    - 30% personality posts
    - Max 3 posts per cycle to avoid spam
    """
    if not CHANNEL_ID:
        return 0

    posted = 0
    max_posts = 3

    # Try news posts first
    try:
        unposted = await db.get_unposted_news(limit=max_posts)
        if unposted:
            news_to_post = unposted[:max_posts - 1]  # Leave room for personality
            posted += await post_news_to_channel(bot, db, news_to_post)
    except Exception as e:
        logger.error(f"Channel news cycle error: {e}")

    # Mix in personality post (30% chance or if no news)
    if posted == 0 or random.random() < 0.3:
        try:
            from news import generate_personality_post
            post_text = await generate_personality_post(ai_router)
            if post_text and await post_personality_to_channel(bot, db, post_text):
                posted += 1
        except Exception as e:
            logger.error(f"Channel personality cycle error: {e}")

    # If still nothing posted and we haven't used template posts
    if posted == 0:
        try:
            template_post = random.choice(PERSONAL_POSTS)
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
]

CHANNEL_DISCUSSION_PHRASES = [
    "Прикинь, я тут новость прочитала... {reaction}",
    "Ты видел что случилось?! {reaction}",
    "Ой, я сейчас в шоке... {reaction}",
    "Слушай, я тут узнала... {reaction}",
    "Короче, новость дня! {reaction}",
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
    - After 10+ messages (user is engaged)
    - Not already subscribed
    - 15% chance per eligible message
    - No more than once per session
    """
    if not CHANNEL_ID:
        return False
    if user_data.get("subscribed_channel"):
        return False
    if msg_count < 10:
        return False
    return random.random() < 0.15
