"""
Emoji reactions for AI Mega Bot (Василий).

Picks context-appropriate emoji(s) for a message and sets them via Telegram's
setMessageReaction. Supports up to 3 reactions per message (Telegram limit).
Falls back gracefully when the bot lacks reaction rights or Telegram rate-limits.
De-duplicates so we never react twice to the same msg.
"""

import asyncio
import logging
import random
from typing import Optional, List

from aiogram import Bot
from aiogram.types import ReactionTypeEmoji

from bot.config import config
from bot import database as db

logger = logging.getLogger("nastya.reactions")

# Positive emoji pool — used for channel posts (3 reactions per post).
_POSITIVE_POOL = ["👍", "❤️", "🔥", "😄", "👏", "🎉", "💪", "✨", "👌", "🙌"]

# Single-emoji pools — chosen by light keyword matching on the message text.
_POSITIVE = ["👍", "❤️", "🔥", "😄", "👏", "🎉", "💪", "✨"]
_LOVE = ["❤️", "😍", "🥰", "💙", "💜"]
_FUN = ["😄", "😂", "🤣", "😆", "😎"]
_WOW = ["😮", "😱", "🤯", "👀", "🔥"]
_SAD = ["😢", "😔", "🙏", "💔"]
_THINK = ["🤔", "👀", "🧐", "💡"]
_NEUTRAL = ["👍", "👌", "🙌", "✨"]


def _pick_emoji(text: str) -> str:
    t = (text or "").lower()
    if any(w in t for w in ["люблю", "обожаю", "супер", "огонь", "класс", "топ", "🔥", "❤"]):
        return random.choice(_LOVE + ["🔥"])
    if any(w in t for w in ["смешн", "лол", "ха", "ржу", "😂", "🤣", "шутк"]):
        return random.choice(_FUN)
    if any(w in t for w in ["ого", "вау", "шок", "жесть", "😱", "невероятн", "удивил"]):
        return random.choice(_WOW)
    if any(w in t for w in ["грустн", "печаль", "жаль", "соболезн", " умер", "погиб"]):
        return random.choice(_SAD)
    if any(w in t for w in ["почему", "как так", "интересн", "думаю", "вопрос", "?"]):
        return random.choice(_THINK)
    if any(w in t for w in ["спасибо", "благодар", "спс"]):
        return random.choice(["🙏", "👍", "❤️"])
    return random.choice(_POSITIVE)


def _pick_3_positive(text: str) -> List[str]:
    """Pick 3 different positive emojis for a channel post.

    Tries to match the text mood (love, fun, wow) but always picks from
    positive pool. Returns exactly 3 unique emojis.
    """
    pool = list(_POSITIVE_POOL)  # copy
    random.shuffle(pool)
    # If text has love/fun/wow keywords, prioritize matching emojis
    t = (text or "").lower()
    preferred = []
    if any(w in t for w in ["люблю", "обожаю", "супер", "класс", "❤", "🔥"]):
        preferred = [e for e in ["❤️", "🔥", "👏"] if e in pool]
    elif any(w in t for w in ["смешн", "лол", "ха", "😂", "шутк"]):
        preferred = [e for e in ["😄", "🎉", "✨"] if e in pool]
    elif any(w in t for w in ["ого", "вау", "шок", "жесть", "невероятн"]):
        preferred = [e for e in ["🔥", "💪", "✨"] if e in pool]
    # Combine preferred + random from pool, ensure 3 unique
    result = []
    for e in preferred:
        if e not in result:
            result.append(e)
        if len(result) >= 3:
            break
    for e in pool:
        if e not in result:
            result.append(e)
        if len(result) >= 3:
            break
    return result[:3]


async def maybe_react(
    bot: Bot,
    chat_id: int,
    message_id: int,
    text: str = "",
    prob: Optional[float] = None,
    force: bool = False,
    count: int = 1,
) -> bool:
    """Set emoji reaction(s) on a message.

    prob: override reaction probability (default config.REACTION_PROB).
    force: if True, skip the probability check (caller already decided).
    count: number of reactions (1-3). count=3 picks 3 different positive
           emojis (used for channel posts). count=1 picks a single
           context-appropriate emoji (used for group messages).
    Returns True if reaction(s) were actually set.
    """
    if not force:
        p = prob if prob is not None else config.REACTION_PROB
        if random.random() > p:
            return False

    # De-duplicate: never react twice to the same message.
    if await db.already_reacted(chat_id, message_id):
        return False

    # Pick emoji(s) based on count
    if count >= 3:
        emojis = _pick_3_positive(text)
    elif count == 2:
        emojis = _pick_3_positive(text)[:2]
    else:
        emojis = [_pick_emoji(text)]

    try:
        reaction_types = [ReactionTypeEmoji(type="emoji", emoji=e) for e in emojis]
        await bot.set_message_reaction(chat_id, message_id, reaction_types)
        await db.mark_reacted(chat_id, message_id)
        return True
    except Exception as e:
        msg = str(e)
        if "REACTION_INVALID" in msg or "not enough rights" in msg.lower():
            logger.debug(f"no reaction rights in {chat_id}")
        elif "RetryAfter" in msg:
            logger.debug(f"reaction rate-limited in {chat_id}")
        else:
            logger.debug(f"reaction failed ({chat_id}/{message_id}): {e}")
        return False
