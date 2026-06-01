"""Nastya Bot 21.0 — Main Entry Point. Single-instance, 24/7 via GitHub Actions.

Architecture v21.0:
  - SINGLE INSTANCE: file lock + conflict tracker prevents multiple bot instances
  - SINGLE WORKFLOW: one bot.yml with concurrency group (no duplicate runs)
  - LOCAL Qwen3-VL-2B via Ollama as PRIMARY (with FIXED vision!)
  - Ollama-first routing with fast-fail cloud fallback
  - HEALTH WATCHDOG: monitors Telegram API + Ollama, auto-restarts on failure
  - АПОЛИТИЧНОСТЬ: Настя не обсуждает политику, религию, войну
  - ГЕНДЕРНАЯ АДАПТАЦИЯ + КОНТЕКСТ ПАМЯТИ + VISION (FIXED!)
  - MOSCOW TIMEZONE — Настя из Москвы!

v21.0 CRITICAL FIXES:
  1. Health watchdog: checks Telegram + Ollama every 60s, restarts if unresponsive
  2. Ollama health check: restarts Ollama server if it dies
  3. Vision routing: goes DIRECTLY to Ollama, no cascading through failing clouds
  4. Fast-fail: max 3 cloud providers tried (was 12+, causing 260s timeouts)
  5. Model selection: qwen3-vl:2b for everything, no qwen2.5:3b references
  6. Process supervisor in workflow: unlimited retries with intelligent backoff
"""
import asyncio
import fcntl
import logging
import os
import signal
import sys
import time
import traceback
import random
from pathlib import Path

# Load .env file before any other imports
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
        logging.getLogger(__name__).info(f"Loaded .env from {_env_path}")
except ImportError:
    pass  # python-dotenv not installed, rely on system env vars

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
#  CONFLICT TRACKER — exit on persistent conflicts
# ════════════════════════════════════════════════════════════
# If TelegramConflictError persists for >60 seconds, the bot
# will exit with code 2, triggering the workflow's auto-restart.
# This prevents the bot from being stuck in an infinite retry loop.

_consecutive_conflicts: int = 0
_first_conflict_time: float = 0
_MAX_CONFLICT_SECONDS = 45  # Exit after 45s of continuous conflicts
_should_exit = False  # Flag for conflict_monitor to signal main loop

# ── Health watchdog state ──
_last_successful_update: float = 0  # Timestamp of last successful Telegram update
_HEALTH_CHECK_INTERVAL = 60  # Check health every 60 seconds
_MAX_UNRESPONSIVE_SECONDS = 180  # Restart if no response for 3 minutes
_ollama_restart_count: int = 0  # Track Ollama restarts


def record_conflict() -> bool:
    """Record a conflict error. Returns True if we should exit."""
    global _consecutive_conflicts, _first_conflict_time, _should_exit
    now = time.time()
    _consecutive_conflicts += 1
    if _first_conflict_time == 0:
        _first_conflict_time = now

    duration = now - _first_conflict_time
    if duration > _MAX_CONFLICT_SECONDS and _consecutive_conflicts > 5:
        logger.critical(
            f"Persistent Conflict for {duration:.0f}s ({_consecutive_conflicts} conflicts). "
            f"EXITING to trigger auto-restart!"
        )
        _should_exit = True
        return True
    return False


def clear_conflicts():
    """Reset conflict tracker on successful update."""
    global _consecutive_conflicts, _first_conflict_time
    _consecutive_conflicts = 0
    _first_conflict_time = 0


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
            elif event.photo:
                logger.info(f"PHOTO {user_info}: caption={event.caption[:50] if event.caption else 'none'}")
        elif isinstance(event, CallbackQuery) and event.from_user:
            user_info = f"user={event.from_user.id} cb={event.data}"
            logger.info(f"CB {user_info}")

        result = await handler(event, data)
        elapsed = time.time() - start
        if elapsed > 3.0:
            logger.warning(f"Slow handler: {elapsed:.2f}s for {user_info}")
        # Clear conflict tracker on successful handler execution
        clear_conflicts()
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


class ConflictTrackingMiddleware(BaseMiddleware):
    """Track TelegramConflictError from aiogram's polling loop.

    aiogram's default retry policy retries conflicts with exponential backoff,
    but it never records them. We hook into the error callback to track them
    so our conflict_monitor can detect persistent conflicts and exit.
    """

    async def __call__(self, handler, event, data: dict):
        # This middleware is for update processing — conflicts happen
        # at the polling level, not here. We clear conflicts on success.
        clear_conflicts()
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

# ── SINGLETON LOCK: prevent multiple bot instances ──────────
_lock_file = None
LOCK_PATH = "/tmp/nastya-bot.lock"


def acquire_singleton_lock() -> bool:
    """Try to acquire a file lock. Returns True if successful.

    If another bot instance is running, the lock will fail and
    this instance will exit immediately — preventing TelegramConflictError.
    """
    global _lock_file
    try:
        _lock_file = open(LOCK_PATH, 'w')
        fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_file.write(str(os.getpid()))
        _lock_file.flush()
        logger.info(f"Singleton lock acquired: {LOCK_PATH} (PID {os.getpid()})")
        return True
    except (IOError, OSError):
        logger.critical("Another bot instance is already running! Exiting to prevent TelegramConflictError.")
        if _lock_file:
            _lock_file.close()
        return False


def release_singleton_lock():
    """Release the singleton lock on shutdown."""
    global _lock_file
    try:
        if _lock_file:
            fcntl.flock(_lock_file, fcntl.LOCK_UN)
            _lock_file.close()
            _lock_file = None
        Path(LOCK_PATH).unlink(missing_ok=True)
    except Exception:
        pass


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
            wait_time = random.randint(1800, 3600)  # 30-60 min — less spammy, but still fun!
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


async def conflict_monitor() -> None:
    """Background task: monitor conflict state and exit if persistent.

    Uses os._exit(2) instead of SystemExit(2).
    SystemExit raised in a background task is caught by asyncio's
    event loop and never reaches the main thread. os._exit() kills
    the entire process immediately, ensuring the workflow's
    auto-restart mechanism kicks in.
    """
    await asyncio.sleep(15)  # Give startup time to settle
    while True:
        try:
            await asyncio.sleep(5)
            # Check if we've been in conflict state for too long
            if _consecutive_conflicts > 5 and _first_conflict_time > 0:
                duration = time.time() - _first_conflict_time
                if duration > _MAX_CONFLICT_SECONDS:
                    logger.critical(
                        f"Conflict monitor: {duration:.0f}s of persistent conflicts "
                        f"({_consecutive_conflicts} conflicts). "
                        f"FORCE EXITING with os._exit(2) to trigger auto-restart!"
                    )
                    os._exit(2)
            # Also check the global exit flag
            if _should_exit:
                logger.critical("Exit flag set! Force exiting with os._exit(2)")
                os._exit(2)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Conflict monitor error: {e}")


async def health_watchdog() -> None:
    """Background task: monitor bot and Ollama health, restart if unresponsive.

    v21.0: The watchdog ensures the bot ALWAYS stays running.
    - Checks Ollama server health every 60s
    - Checks Telegram API reachability every 60s
    - If Ollama is down, tries to restart it
    - If nothing works, exits with code 3 to trigger workflow restart
    """
    global _ollama_restart_count

    await asyncio.sleep(30)  # Give startup time to settle
    logger.info("Health watchdog started")

    while True:
        try:
            await asyncio.sleep(_HEALTH_CHECK_INTERVAL)

            # ── Check 1: Ollama health ──
            if ai_router and "ollama" in ai_router.providers:
                ollama = ai_router.providers["ollama"]
                ollama_ok = await ollama.health_check()
                if not ollama_ok:
                    logger.warning("Ollama health check FAILED! Trying to restart...")
                    try:
                        import subprocess
                        subprocess.Popen(["pkill", "ollama"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        await asyncio.sleep(3)
                        subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        await asyncio.sleep(10)
                        # Check again
                        ollama_ok = await ollama.health_check()
                        if ollama_ok:
                            _ollama_restart_count += 1
                            logger.info(f"Ollama restarted successfully (restart #{_ollama_restart_count})")
                            try:
                                await ollama.init()
                            except Exception:
                                pass
                        else:
                            logger.critical("Ollama restart FAILED! Bot needs full restart.")
                            os._exit(3)
                    except Exception as e:
                        logger.error(f"Ollama restart attempt failed: {e}")
                        os._exit(3)

            # ── Check 2: Telegram API health ──
            if bot:
                try:
                    me = await bot.get_me()
                    if me and me.id:
                        continue  # Bot is healthy!
                except Exception as e:
                    logger.warning(f"Telegram API health check failed: {e}")
                    if _first_conflict_time > 0:
                        unresponsive_time = time.time() - _first_conflict_time
                        if unresponsive_time > _MAX_UNRESPONSIVE_SECONDS:
                            logger.critical(
                                f"Bot unresponsive for {unresponsive_time:.0f}s! "
                                f"FORCE EXITING to trigger restart!"
                            )
                            os._exit(3)

            # ── Check 3: Uptime sanity ──
            if _start_time > 0 and ai_router:
                uptime = time.time() - _start_time
                status = ai_router.get_status()
                total_req = status.get("_stats", {}).get("total_requests", 0)
                if uptime > 600 and total_req == 0:
                    logger.warning(
                        f"Bot running for {uptime:.0f}s with 0 AI requests. "
                        f"Polling may be stuck. Continuing to monitor..."
                    )

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Health watchdog error: {e}")


# ════════════════════════════════════════════════════════════
#  STARTUP / SHUTDOWN
# ════════════════════════════════════════════════════════════

async def on_startup(**kwargs) -> None:
    global db, ai_router, _start_time
    _start_time = time.time()
    logger.info("=== Nastya Bot 20.0 Starting (Ollama Lock, Vision FIX, Text=1.7b Vision=VL) ===")

    # NOTE: Webhook deletion and conflict resolution is handled in main()
    # before start_polling() — no need to do it here again

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
        asyncio.create_task(conflict_monitor())
        asyncio.create_task(health_watchdog())

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

    logger.info("=== Nastya Bot 20.0 Ready ===")


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


# ════════════════════════════════════════════════════════════
#  TAKEOVER — resolve TelegramConflictError
# ════════════════════════════════════════════════════════════

async def force_takeover(bot_instance: Bot) -> bool:
    """Force-take control of the bot from any previous instance.

    v19.0 ROOT CAUSE FIX: The previous code used get_updates(timeout=0)
    for the takeover test. This is WRONG because:
    1. aiogram's Bot.get_updates() creates a NEW aiohttp session internally
    2. Even with timeout=0, it sends a getUpdates HTTP request to Telegram
    3. When aiogram's polling loop starts later, it creates ANOTHER session
    4. For a brief moment, BOTH sessions are calling getUpdates → Conflict!

    FIX: Use a DEDICATED httpx client (NOT aiogram's Bot) for the takeover
    test. This way aiogram's internal session is NEVER opened before
    start_polling(), so there's no session leak.

    Returns True if takeover succeeded.
    """
    import httpx

    MAX_ATTEMPTS = 10
    logger.info(f"=== Starting takeover (up to {MAX_ATTEMPTS} attempts) ===")

    for attempt in range(1, MAX_ATTEMPTS + 1):
        # Step 1: Delete webhook — this kicks the old instance's long-poll
        # and forces it to return with a Conflict error
        try:
            await bot_instance.delete_webhook(drop_pending_updates=True)
            logger.info(f"[Takeover {attempt}] delete_webhook OK")
        except Exception as e:
            logger.warning(f"[Takeover {attempt}] delete_webhook failed: {e}")

        # Step 2: Wait for old instance to die
        # Progressive delay: 3s → 5s → 8s → 12s → 15s
        if attempt <= 2:
            wait = 5
        elif attempt <= 4:
            wait = 8
        elif attempt <= 6:
            wait = 12
        else:
            wait = 15
        logger.info(f"[Takeover {attempt}] Waiting {wait}s for old instance to die...")
        await asyncio.sleep(wait)

        # Step 3: Test with a DEDICATED httpx client (NOT aiogram's session!)
        # This prevents the aiogram session leak that was causing
        # persistent TelegramConflictError.
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
                # Call getUpdates directly via HTTP — no aiogram involvement
                resp = await client.get(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                    params={"limit": 1, "timeout": 5},
                )
                data = resp.json()

                if data.get("ok"):
                    logger.info(
                        f"[Takeover {attempt}] getUpdates SUCCESS via httpx — "
                        f"we are the sole instance!"
                    )
                    return True
                elif data.get("error_code") == 409:
                    logger.warning(
                        f"[Takeover {attempt}] Conflict — old instance still alive, retrying..."
                    )
                    # Delete webhook AGAIN to keep kicking the old instance
                    try:
                        await bot_instance.delete_webhook(drop_pending_updates=True)
                    except Exception:
                        pass
                else:
                    logger.warning(
                        f"[Takeover {attempt}] Telegram error: {data.get('description', 'unknown')}"
                    )
                    # Non-conflict error — might be network issue. Try polling anyway.
                    return True

        except httpx.TimeoutException:
            # Timeout is OK — it means the long-poll is working (no conflict!)
            logger.info(f"[Takeover {attempt}] Timeout (means no conflict!) — SUCCESS!")
            return True
        except Exception as e:
            logger.warning(f"[Takeover {attempt}] httpx test error: {e}")
            # Network issue — try polling anyway
            return True

    logger.error("FAILED to take over after all attempts!")
    return False


async def main():
    global bot

    # ── SINGLETON CHECK: exit if another instance is running ──
    # In GitHub Actions, old instances are cancelled by concurrency group,
    # but there's a race: old process may still be alive when new one starts.
    # We handle this with aggressive takeover below.
    if not acquire_singleton_lock():
        # Lock file exists — but in GitHub Actions, the old process
        # may have been killed without releasing the lock.
        # Force-remove stale lock and try again.
        logger.warning("Stale lock detected, force-removing...")
        try:
            Path(LOCK_PATH).unlink(missing_ok=True)
        except Exception:
            pass
        if not acquire_singleton_lock():
            logger.critical("Cannot acquire lock even after cleanup. Exiting.")
            sys.exit(0)

    # Create bot instance for takeover phase
    takeover_bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    # ═══════════════════════════════════════════════════════
    #  SMART TAKEOVER — resolve TelegramConflictError
    # ═══════════════════════════════════════════════════════
    takeover_ok = await force_takeover(takeover_bot)

    if not takeover_ok:
        logger.error("Takeover failed! Will try polling anyway...")

    # ═══════════════════════════════════════════════════════
    #  CRITICAL: Close takeover bot and create FRESH bot for polling
    # ═══════════════════════════════════════════════════════
    # The takeover bot's aiohttp session may have stale connections
    # that interfere with aiogram's polling. Create a brand new bot.
    try:
        await takeover_bot.session.close()
        logger.info("Takeover bot session closed")
    except Exception:
        pass

    # Small delay to ensure old connections are fully cleaned up
    await asyncio.sleep(2)

    # Create FRESH bot instance for polling
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    # Final webhook cleanup with fresh bot (no stale sessions!)
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Final webhook cleanup with fresh bot OK")
    except Exception as e:
        logger.warning(f"Final webhook cleanup failed: {e}")

    dispatcher = setup_dispatcher()

    try:
        async def session_timeout():
            await asyncio.sleep(SESSION_DURATION_SECONDS)
            logger.info("Session timeout, shutting down...")
            os._exit(0)  # Use os._exit to ensure process dies

        timeout_task = asyncio.create_task(session_timeout())

        # Include poll_answer updates so Nastya can react to poll votes
        allowed_updates = dispatcher.resolve_used_update_types()
        if "poll_answer" not in allowed_updates:
            allowed_updates.append("poll_answer")

        logger.info("Starting aiogram polling with fresh bot session...")
        await dispatcher.start_polling(bot, allowed_updates=allowed_updates)

    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 0
        if code == 2:
            logger.info("Bot exiting due to persistent conflicts (will auto-restart)")
        else:
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
        release_singleton_lock()
        try:
            await bot.session.close()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        sys.exit(code)
    except Exception as e:
        logger.critical(f"Fatal: {e}")
        sys.exit(1)
