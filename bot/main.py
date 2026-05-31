"""Nastya Bot 7.0 — Main Entry Point. 24/7 via GitHub Actions with keep-alive.

Architecture v7.0:
  - Web search integration — Nastya can find and verify information!
  - OpenRouter as PRIMARY (25+ free models, Gemma 4 31B, Nemotron 120B, vision)
  - Cloudflare Workers AI as SECONDARY (free, reliable, many models, vision)
  - Groq as TERTIARY (free, ultra-fast LPU, great Russian, 30 RPM)
  - HuggingFace as QUATERNARY (free tier with token, many models)
  - Chutes as QUINARY (free DeepSeek V3, rate-limited)
  - Pollinations as FALLBACK #1 (always free, always available, ads cleaned)
  - GitHub Models as FALLBACK #2 (needs PAT with 'models' permission)
  - DeepSeek REMOVED ENTIRELY (was returning 402 Insufficient Balance)
  - Web search: DuckDuckGo-based, auto-triggers on questions/events
  - Real Telegram polls in channel using send_poll() with vote buttons
  - Poll answer reactions — Nastya reacts when someone votes!
  - /search command for explicit web searches
  - Expanded vocabulary: "Точняк!", "Офигеть!", "Кайф!", "Жесть!" etc. 30+ words
  - Knowledge injection by 10 topics: auto, zodiac, psychology, facts, Moscow,
    cinema, cooking, relationships, fashion, travel, tech
  - Context memory: zodiac signs, names, city, preferences — NEVER forgets
  - Channel @chasnastya — ALWAYS remembered, links in news!
  - NEWS ENGINE: RSS + AI commentary + links (15 min cycle)
  - CHANNEL MANAGER: diverse posts, REAL POLLS, events, reactions (20 min)
  - Stars donations with ACTIVE Pay buttons
  - MOSCOW TIMEZONE — Настя из Москвы!
  - Keep-alive chain via GH PAT trigger
  - NO "голова разболелась" — Nastya ALWAYS responds in character
"""
import asyncio
import logging
import os
import sys
import time
import traceback
import random

from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.enums import ParseMode
from aiogram.types import TelegramObject, Message, CallbackQuery
from aiogram.client.default import DefaultBotProperties

logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO"), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("nastya-bot")

from bot.config import (
    BOT_TOKEN, ADMIN_IDS, DB_PATH, SESSION_DURATION_SECONDS, OWNER_ID,
    NEWS_FETCH_INTERVAL, CHANNEL_POST_INTERVAL, CHANNEL_ID, CHANNEL_USERNAME,
)

if not BOT_TOKEN:
    logger.critical("Missing BOT_TOKEN")
    sys.exit(1)


# ════════════════════════════════════════════════════════════
#  MIDDLEWARE
# ════════════════════════════════════════════════════════════

class ErrorHandlingMiddleware(BaseMiddleware):
    """Catch and log errors without crashing the bot.

    NEVER sends error messages to users — they should only see Nastya's personality.
    """

    async def __call__(self, handler, event: TelegramObject, data: dict):
        try:
            return await handler(event, data)
        except (SystemExit, KeyboardInterrupt):
            raise
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Unhandled error: {e}\n{traceback.format_exc()}")
            return None


class LoggingMiddleware(BaseMiddleware):
    """Log all incoming updates for monitoring."""

    async def __call__(self, handler, event, data: dict):
        start = time.time()
        user_info = ""

        if isinstance(event, Message) and event.from_user:
            user_info = f"user={event.from_user.id} ({event.from_user.username or 'no_username'})"
            if event.text:
                logger.info(f"MSG {user_info}: {event.text[:100]}")
        elif isinstance(event, CallbackQuery) and event.from_user:
            user_info = f"user={event.from_user.id} cb={event.data}"
            logger.info(f"CB {user_info}")

        result = await handler(event, data)
        elapsed = time.time() - start
        if elapsed > 3.0:
            logger.warning(f"Slow handler: {elapsed:.2f}s for {user_info}")
        return result


class RateLimitMiddleware(BaseMiddleware):
    """Per-user rate limiting to prevent abuse."""

    def __init__(self, max_per_minute: int = 30):
        self.max_per_minute = max_per_minute
        self._user_requests: dict = {}

    async def __call__(self, handler, event, data: dict):
        user_id = None
        if isinstance(event, (Message, CallbackQuery)) and event.from_user:
            user_id = event.from_user.id

        if not user_id:
            return await handler(event, data)

        now = time.time()
        if user_id not in self._user_requests:
            self._user_requests[user_id] = []

        self._user_requests[user_id] = [
            t for t in self._user_requests[user_id] if now - t < 60
        ]

        if len(self._user_requests[user_id]) >= self.max_per_minute:
            if isinstance(event, Message):
                await event.answer("Настя не успевает отвечать! Подожди минуточку! 💅")
            elif isinstance(event, CallbackQuery):
                await event.answer("Слишком быстро!", show_alert=True)
            return

        self._user_requests[user_id].append(now)
        return await handler(event, data)


# ── Global instances ───────────────────────────────────────
from bot.database import Database
from bot.handlers import all_routers
from ai.router import AIRouter

db: Database = None
ai_router: AIRouter = None
bot: Bot = None
dp: Dispatcher = None
_start_time: float = 0


# ════════════════════════════════════════════════════════════
#  BACKGROUND TASKS
# ════════════════════════════════════════════════════════════

async def news_scheduler(bot_instance: Bot) -> None:
    """Background task: periodically fetch news and generate Nastya's commentary."""
    from news import run_news_cycle

    # Wait for startup
    await asyncio.sleep(30)

    while True:
        try:
            if db and ai_router:
                commented = await run_news_cycle(db, ai_router)
                if commented > 0:
                    logger.info(f"News scheduler: {commented} items commented")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"News scheduler error: {e}")

        await asyncio.sleep(NEWS_FETCH_INTERVAL)


async def channel_scheduler(bot_instance: Bot) -> None:
    """Background task: periodically post to Telegram channel @chasnastya."""
    from channel import run_channel_cycle

    # Wait for startup + initial news fetch
    await asyncio.sleep(60)

    while True:
        try:
            if db and ai_router and CHANNEL_ID:
                posted = await run_channel_cycle(bot_instance, db, ai_router)
                if posted > 0:
                    logger.info(f"Channel scheduler: {posted} posts made")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Channel scheduler error: {e}")

        await asyncio.sleep(CHANNEL_POST_INTERVAL)


async def proactive_scheduler(bot_instance: Bot) -> None:
    """Background task: periodically send proactive messages."""
    from bot.handlers.chat import check_and_send_proactive

    # Wait for startup
    await asyncio.sleep(120)

    while True:
        try:
            wait_time = random.randint(300, 600)
            await asyncio.sleep(wait_time)
            if db and ai_router:
                await check_and_send_proactive(bot_instance, db, ai_router)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Proactive scheduler error: {e}")
            await asyncio.sleep(60)


async def periodic_db_cleanup() -> None:
    """Background task: periodically clean up old DB records."""
    while True:
        try:
            await asyncio.sleep(86400)  # Once per day
            if db:
                deleted = await db.cleanup_old_history(max_age_hours=720)
                if deleted > 0:
                    logger.info(f"DB cleanup: removed {deleted} old messages")

                # Also cleanup AI cache
                cache_deleted = await db.cache_cleanup(max_age=7200)
                if cache_deleted > 0:
                    logger.info(f"Cache cleanup: removed {cache_deleted} old entries")

                # Cleanup old news
                from bot.config import NEWS_MAX_ITEMS
                news_deleted = await db.cleanup_old_news(NEWS_MAX_ITEMS)
                if news_deleted > 0:
                    logger.info(f"News cleanup: removed {news_deleted} old news items")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"DB cleanup error: {e}")


async def memory_cleanup() -> None:
    """Background task: periodically clean in-memory trackers to prevent leaks."""
    while True:
        try:
            await asyncio.sleep(3600)  # Every hour
            from bot.handlers.chat import _cleanup_trackers
            _cleanup_trackers()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Memory cleanup error: {e}")


# ════════════════════════════════════════════════════════════
#  STARTUP / SHUTDOWN
# ════════════════════════════════════════════════════════════

async def on_startup(**kwargs) -> None:
    global db, ai_router, _start_time
    _start_time = time.time()
    logger.info("=== Nastya Bot 7.1 Starting (OpenRouter PRIMARY) ===")

    db = Database(DB_PATH)
    await db.init()
    logger.info("Database initialized")

    ai_router = AIRouter(db)
    await ai_router.init()
    logger.info(f"AI Router: {len(ai_router.providers)} providers, chain: {ai_router._chain}")

    try:
        await db.get_or_create_user(OWNER_ID, "owner", "Owner")
    except Exception as e:
        logger.error(f"Owner setup error: {e}")

    dp_ref = kwargs.get("dispatcher") or kwargs.get("router") or dp
    if dp_ref:
        dp_ref.workflow_data["db"] = db
        dp_ref.workflow_data["ai_router"] = ai_router
        logger.info(f"workflow_data set: db={db is not None}, ai_router={ai_router is not None}")

    if bot:
        asyncio.create_task(news_scheduler(bot))
        asyncio.create_task(channel_scheduler(bot))
        asyncio.create_task(proactive_scheduler(bot))
        asyncio.create_task(periodic_db_cleanup())
        asyncio.create_task(memory_cleanup())

        # Startup notification — Nastya-style, NO technical info
        for admin_id in ADMIN_IDS:
            if admin_id:
                try:
                    from bot.nastya import get_random_fact
                    thought = get_random_fact()
                    await bot.send_message(
                        admin_id,
                        f"💅 Настя проснулась!\n\n{thought}",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

    logger.info("=== Nastya Bot 7.1 Ready ===")


async def on_shutdown(**kwargs) -> None:
    global db, ai_router
    logger.info("=== Nastya Bot Shutting Down ===")

    if bot:
        for admin_id in ADMIN_IDS:
            try:
                sleepy = random.choice([
                    "Я спать 💤",
                    "Я спать! Не буди! 😤💤",
                    "Настя спать... 💤",
                    "Всё, я спать! 💅💤",
                    "Спать хочу! Ночи! 🌙💤",
                ])
                await bot.send_message(admin_id, sleepy)
            except Exception:
                pass

    if ai_router:
        await ai_router.close()
    if db:
        await db.close()

    logger.info("=== Nastya Bot Stopped ===")


def setup_dispatcher() -> Dispatcher:
    """Configure dispatcher with all routers and middleware."""
    global dp
    dp = Dispatcher()

    dp.message.middleware(RateLimitMiddleware(max_per_minute=30))
    dp.callback_query.middleware(RateLimitMiddleware(max_per_minute=30))
    dp.message.middleware(LoggingMiddleware())
    dp.callback_query.middleware(LoggingMiddleware())
    dp.message.outer_middleware(ErrorHandlingMiddleware())
    dp.callback_query.outer_middleware(ErrorHandlingMiddleware())
    dp.pre_checkout_query.middleware(ErrorHandlingMiddleware())

    for router in all_routers:
        dp.include_router(router)

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    return dp


async def main():
    global bot

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dispatcher = setup_dispatcher()

    try:
        async def session_timeout():
            await asyncio.sleep(SESSION_DURATION_SECONDS)
            logger.info("Session timeout, shutting down...")
            raise SystemExit(0)

        timeout_task = asyncio.create_task(session_timeout())

        await bot.delete_webhook(drop_pending_updates=True)
        # Include poll_answer updates so Nastya can react to poll votes
        allowed_updates = dispatcher.resolve_used_update_types()
        if "poll_answer" not in allowed_updates:
            allowed_updates.append("poll_answer")
        await dispatcher.start_polling(bot, allowed_updates=allowed_updates)

    except SystemExit:
        logger.info("Bot stopped (session timeout)")
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.critical(f"Bot crashed: {e}\n{traceback.format_exc()}")
    finally:
        try:
            timeout_task.cancel()
        except Exception:
            pass
        await on_shutdown()
        try:
            await bot.session.close()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.critical(f"Fatal: {e}")
        sys.exit(1)
