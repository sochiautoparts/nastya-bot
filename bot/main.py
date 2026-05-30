"""Nastya Bot — Main Entry Point. Runs Telegram bot + Flask API. 24/7 via GitHub Actions."""
import asyncio
import json
import logging
import os
import sys
import time
import threading

logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO"), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("nastya-bot")

from bot.config import BOT_TOKEN, ADMIN_IDS, API_HOST, API_PORT, DB_PATH, SESSION_DURATION_SECONDS, OWNER_ID

if not BOT_TOKEN:
    logger.critical("Missing BOT_TOKEN")
    sys.exit(1)

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
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
            await asyncio.sleep(300)
            if db and ai_router:
                await check_and_send_proactive(bot_instance, db, ai_router)
        except Exception as e:
            logger.error(f"Proactive scheduler error: {e}")


async def on_startup(**kwargs) -> None:
    global db, ai_router, _start_time, bot
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
        provider_list = ", ".join(ai_router.providers.keys())
        for admin_id in ADMIN_IDS:
            if admin_id:
                try:
                    await bot.send_message(
                        admin_id,
                        f"Настя проснулась!\n\n"
                        f"AI: {len(ai_router.providers)} провайдеров\n"
                        f"Цепочка: {provider_list}\n"
                        f"Настроение: капризное (как обычно)",
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
                uptime = int(time.time() - _start_time) if _start_time else 0
                h, rem = divmod(uptime, 3600)
                m, s = divmod(rem, 60)
                await bot.send_message(admin_id, f"Настя уснула... Uptime: {h}ч {m}м {s}с")
            except Exception:
                pass

    if ai_router:
        await ai_router.close()
    if db:
        await db.close()


def setup_dispatcher() -> Dispatcher:
    global dp
    dp = Dispatcher()
    for router in all_routers:
        dp.include_router(router)
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    return dp


def create_flask_app():
    from flask import Flask, jsonify
    app = Flask(__name__)

    @app.route("/api/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok", "bot": "nastya-bot", "version": "3.0.0"})

    return app


def run_flask():
    app = create_flask_app()
    app.run(host=API_HOST, port=API_PORT, threaded=True, use_reloader=False)


async def main():
    global bot
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dispatcher = setup_dispatcher()

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    try:
        async def session_timeout():
            await asyncio.sleep(SESSION_DURATION_SECONDS)
            raise SystemExit(0)

        timeout_task = asyncio.create_task(session_timeout())
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    except SystemExit:
        logger.info("Session timeout")
    except Exception as e:
        logger.critical(f"Bot error: {e}", exc_info=True)
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
