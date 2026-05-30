"""Nastya Bot — Main Entry Point. 24/7 via GitHub Actions with keep-alive.

Architecture (v4.0):
  - ErrorHandlingMiddleware — catches ALL exceptions, bot NEVER crashes
  - AI Router with fallback chain: Chutes → Pollinations → OpenRouter → Cerebras
  - Per-operation DB connections — no stale connections
  - Stars donations with ACTIVE Pay buttons via send_invoice
  - Deep links from GitHub Pages → /start donate_NNN → sends invoice
  - Proactive messages via asyncio background task
  - Keep-alive chain via GH PAT trigger
  - No Flask — simplified, runs pure aiogram polling
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
from aiogram.types import TelegramObject
from aiogram.client.default import DefaultBotProperties

logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO"), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("nastya-bot")

from bot.config import BOT_TOKEN, ADMIN_IDS, DB_PATH, SESSION_DURATION_SECONDS, OWNER_ID, GH_PAT_TOKEN

if not BOT_TOKEN:
    logger.critical("Missing BOT_TOKEN")
    sys.exit(1)


# ════════════════════════════════════════════════════════════
#  ERROR HANDLING MIDDLEWARE — prevents ALL crashes!
# ════════════════════════════════════════════════════════════

class ErrorHandlingMiddleware(BaseMiddleware):
    """Catch and log errors without crashing the bot."""

    async def __call__(self, handler, event: TelegramObject, data: dict):
        try:
            return await handler(event, data)
        except (SystemExit, KeyboardInterrupt):
            raise
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Unhandled error: {e}\n{traceback.format_exc()}")
            # Try to notify user
            try:
                chat_id = None
                if hasattr(event, 'chat') and event.chat:
                    chat_id = event.chat.id
                elif hasattr(event, 'from_user') and event.from_user:
                    chat_id = event.from_user.id
                elif hasattr(event, 'message') and event.message:
                    chat_id = event.message.chat.id
                if chat_id and bot:
                    await bot.send_message(
                        chat_id,
                        "Ой, Настя на секунду зависла... но уже вернулась! 😵‍💫💕",
                    )
            except Exception:
                pass
        return None


# ── Global instances ───────────────────────────────────────
from bot.database import Database
from bot.handlers import all_routers
from ai.router import AIRouter

db: Database = None
ai_router: AIRouter = None
bot: Bot = None
dp: Dispatcher = None
_start_time: float = 0


async def proactive_scheduler(bot_instance: Bot) -> None:
    """Background task: periodically send proactive messages."""
    from bot.handlers.chat import check_and_send_proactive
    while True:
        try:
            await asyncio.sleep(random.randint(300, 600))
            if db and ai_router:
                await check_and_send_proactive(bot_instance, db, ai_router)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Proactive scheduler error: {e}")
            await asyncio.sleep(60)


async def on_startup(**kwargs) -> None:
    global db, ai_router, _start_time
    _start_time = time.time()
    logger.info("=== Nastya Bot Starting ===")

    db = Database(DB_PATH)
    await db.init()
    logger.info("Database initialized")

    ai_router = AIRouter()
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

    if bot:
        asyncio.create_task(proactive_scheduler(bot))
        # Fun startup message — NO tech info!
        from bot.nastya import get_random_fact
        thought = get_random_fact()
        for admin_id in ADMIN_IDS:
            if admin_id:
                try:
                    await bot.send_message(
                        admin_id,
                        f"💅 <b>Настя проснулась!</b>\n\n{thought}",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

    logger.info("=== Nastya Bot Ready ===")


async def on_shutdown(**kwargs) -> None:
    global db, ai_router
    logger.info("=== Nastya Bot Shutting Down ===")

    if bot:
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(admin_id, "😴 Настя уснула... 💤")
            except Exception:
                pass

    if ai_router:
        await ai_router.close()
    if db:
        await db.close()

    logger.info("=== Nastya Bot Stopped ===")


def setup_dispatcher() -> Dispatcher:
    global dp
    dp = Dispatcher()
    for router in all_routers:
        dp.include_router(router)

    # Add error handling middleware — bot NEVER crashes!
    dp.message.middleware(ErrorHandlingMiddleware())
    dp.callback_query.middleware(ErrorHandlingMiddleware())
    dp.pre_checkout_query.middleware(ErrorHandlingMiddleware())

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
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())

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
