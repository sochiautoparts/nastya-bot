"""Люба Safe Send — rate-limit-safe message sending."""
import asyncio, logging, time
from aiogram import Bot
from aiogram.types import Message
from aiogram.exceptions import TelegramRetryAfter

logger = logging.getLogger("nastya.safe_send")
_chat_buckets: dict = {}
_CHAT_WINDOW = 60

def _can_send(chat_id, max_per_min, priority):
    now = time.time()
    cap = max_per_min + (5 if priority else 0)
    bucket = _chat_buckets.get(chat_id, [])
    bucket[:] = [t for t in bucket if now - t < _CHAT_WINDOW]
    if len(bucket) >= cap: return False
    bucket.append(now)
    _chat_buckets[chat_id] = bucket
    return True

async def safe_reply(bot, message, text, always_reply=True, priority=False, max_per_min=15):
    if not text: return False
    chat_id = message.chat.id
    if not _can_send(chat_id, max_per_min, priority):
        logger.info(f"rate-limited skip in {chat_id}")
        return False
    for attempt in range(3):
        try:
            if always_reply: await message.reply(text, disable_web_page_preview=False)
            else: await bot.send_message(chat_id, text, disable_web_page_preview=False)
            return True
        except TelegramRetryAfter as e:
            wait = e.retry_after + 1
            logger.warning(f"RetryAfter {wait}s in {chat_id}")
            await asyncio.sleep(wait)
        except Exception as e:
            msg = str(e)
            if "message is too long" in msg.lower():
                text = text[:4000]
                try: await message.reply(text); return True
                except: return False
            logger.debug(f"send failed in {chat_id}: {e}")
            return False
    return False

async def safe_send(bot, chat_id, text, priority=False, max_per_min=15):
    if not text: return False
    if not _can_send(chat_id, max_per_min, priority):
        logger.info(f"rate-limited skip send to {chat_id}")
        return False
    for attempt in range(3):
        try:
            await bot.send_message(chat_id, text[:4000], disable_web_page_preview=False)
            return True
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except Exception as e:
            logger.debug(f"safe_send failed in {chat_id}: {e}")
            return False
    return False
