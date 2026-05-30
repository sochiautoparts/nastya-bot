"""Nastya Bot — Main Entry Point 🎀

Runs Telegram bot (aiogram 3.x) + Flask API server.
24/7 via GitHub Actions.
"""
import asyncio
import json
import logging
import os
import sys
import time
import threading
from pathlib import Path

logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO"), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("nastya-bot")

from bot.config import (
    BOT_TOKEN, ADMIN_IDS, API_HOST, API_PORT, DB_PATH,
    SESSION_DURATION_SECONDS, OWNER_ID, validate_config,
)

# Validate
def validate_config():
    if not BOT_TOKEN:
        return ["BOT_TOKEN"]
    return []

missing = validate_config()
if missing:
    logger.critical(f"Missing: {', '.join(missing)}")
    sys.exit(1)

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from bot.database import Database
from bot.handlers import all_routers
from ai.router import AIRouter

# Globals
db: Database = None
ai_router: AIRouter = None
bot: Bot = None
dp: Dispatcher = None
_start_time: float = 0


async def on_startup(**kwargs) -> None:
    global db, ai_router, _start_time, bot
    _start_time = time.time()

    logger.info("=== Nastya Bot Starting 🎀 ===")

    db = Database(DB_PATH)
    await db.init()
    logger.info("Database initialized")

    ai_router = AIRouter()
    await ai_router.init()
    logger.info(f"AI Router: {len(ai_router.providers)} providers")

    # Ensure owner
    try:
        await db.get_or_create_user(OWNER_ID, "owner", "Owner")
    except Exception as e:
        logger.error(f"Owner setup error: {e}")

    # Set workflow_data
    dp_ref = kwargs.get("dispatcher") or kwargs.get("router") or dp
    if dp_ref:
        dp_ref.workflow_data["db"] = db
        dp_ref.workflow_data["ai_router"] = ai_router

    # Notify admins
    if bot:
        for admin_id in ADMIN_IDS:
            if admin_id:
                try:
                    provider_list = ", ".join(ai_router.providers.keys())
                    await bot.send_message(
                        admin_id,
                        f"🎀 <b>Настя проснулась!</b>\n\n"
                        f"🤖 AI: {len(ai_router.providers)} провайдеров\n"
                        f"⚡ Цепочка: {provider_list}\n"
                        f"⏱ Сессия: {SESSION_DURATION_SECONDS // 60} мин\n"
                        f"😤 Настроение: капризное (как обычно)",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

    logger.info("=== Nastya Bot Ready 🎀 ===")


async def on_shutdown(**kwargs) -> None:
    global db, ai_router
    logger.info("=== Nastya Bot Shutting Down 🎀 ===")

    if bot:
        for admin_id in ADMIN_IDS:
            try:
                uptime = int(time.time() - _start_time) if _start_time else 0
                h, rem = divmod(uptime, 3600)
                m, s = divmod(rem, 60)
                await bot.send_message(admin_id, f"🎀 Настя уснула... Uptime: {h}ч {m}м {s}с 😴", parse_mode="HTML")
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
        return jsonify({"status": "ok", "bot": "nastya-bot", "version": "1.0.0"})

    @app.route("/api/stats", methods=["GET"])
    def stats():
        try:
            with open("data/stats.json") as f:
                return jsonify(json.load(f))
        except Exception:
            return jsonify({"error": "no stats"}), 404

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

    dp = setup_dispatcher()

    # Flask in background
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    session_end = time.time() + SESSION_DURATION_SECONDS

    try:
        async def session_timeout():
            await asyncio.sleep(SESSION_DURATION_SECONDS)
            raise SystemExit(0)

        timeout_task = asyncio.create_task(session_timeout())
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
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
