"""Настя Main — starts OpenClaw gateway subprocess + aiogram bot + channel scheduler."""
import asyncio, logging, os, signal, subprocess, sys, time, random
from pathlib import Path
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from bot.config import config
from bot import database as db
from bot.mood import mood_loop, current_mood_descriptor
from bot.partners import partner_manager
from ai import client as ai_client

logging.basicConfig(level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO), format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger("nastya.main")
for noisy in ["aiogram.event", "httpx", "httpcore", "aiosqlite"]: logging.getLogger(noisy).setLevel(logging.WARNING)

from bot.handlers.chat import chat_router
from bot.handlers.groups import group_router
from bot.handlers.channels import channel_router
from bot.handlers.admin import admin_router
from bot.handlers.inline import inline_router
from bot.handlers.payment import payment_router
from bot.handlers.fun import fun_router

OPENCLAW_STATE_DIR = os.getenv("OPENCLAW_STATE_DIR", str(Path.cwd() / ".openclaw-state"))
_openclaw_proc = None

def _generate_openclaw_config():
    state_dir = OPENCLAW_STATE_DIR
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    out = str(Path(state_dir) / "openclaw.json")
    gen = str(Path(__file__).resolve().parent.parent / "scripts" / "gen_openclaw_config.py")
    env = os.environ.copy(); env["OPENCLAW_STATE_DIR"] = state_dir
    r = subprocess.run([sys.executable, gen, "--out", out, "--state-dir", state_dir], env=env)
    if r.returncode != 0: raise RuntimeError(f"OpenClaw config generation failed (code {r.returncode})")
    return out

def _start_openclaw_gateway(config_path):
    env = os.environ.copy()
    env["OPENCLAW_STATE_DIR"] = OPENCLAW_STATE_DIR
    env["OPENCLAW_CONFIG_PATH"] = config_path
    npm_global = os.path.expanduser("~/.npm-global/bin")
    env["PATH"] = npm_global + ":" + env.get("PATH", "")
    cmd = [config.OPENCLAW_BIN, "gateway", "--port", str(config.OPENCLAW_PORT), "--auth", "none", "--bind", "loopback", "--allow-unconfigured"]
    log_path = str(Path(OPENCLAW_STATE_DIR) / "gateway.log")
    logger.info(f"Starting OpenClaw Gateway: {' '.join(cmd)}")
    log_f = open(log_path, "a", buffering=1)
    return subprocess.Popen(cmd, env=env, stdout=log_f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)

async def _wait_for_gateway(timeout=120.0):
    import httpx
    url = f"{config.OPENCLAW_URL}/v1/models"
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            async with httpx.AsyncClient() as c:
                r = await c.get(url, timeout=5.0)
                if r.status_code == 200: return True
        except: pass
        if _openclaw_proc is not None and _openclaw_proc.poll() is not None: return False
        await asyncio.sleep(2.0)
    return False

def _stop_openclaw_gateway():
    global _openclaw_proc
    if _openclaw_proc is not None:
        try:
            _openclaw_proc.terminate()
            try: _openclaw_proc.wait(timeout=10)
            except: _openclaw_proc.kill()
        except: pass
        _openclaw_proc = None

class NastyaBot:
    def __init__(self):
        if not config.BOT_TOKEN: raise RuntimeError("BOT_TOKEN not set")
        self.bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=None))
        self.dp = Dispatcher(storage=MemoryStorage())
        self.dp.include_router(admin_router)
        self.dp.include_router(fun_router)
        self.dp.include_router(payment_router)
        self.dp.include_router(chat_router)
        self.dp.include_router(group_router)
        self.dp.include_router(channel_router)
        self.dp.include_router(inline_router)
        from aiogram.types import ErrorEvent
        @self.dp.error()
        async def on_error(event: ErrorEvent):
            try:
                exc = event.exception
                from aiogram.exceptions import TelegramRetryAfter
                if isinstance(exc, TelegramRetryAfter): logger.warning(f"Flood control (RetryAfter {exc.retry_after}s)")
                else: logger.error(f"Handler error (suppressed): {type(exc).__name__}: {exc}", exc_info=False)
            except: pass

    async def start(self):
        logger.info("=== Настя (OpenClaw) стартует ===")
        try:
            me = await self.bot.get_me()
            config.BOT_ID = me.id
            config.BOT_USERNAME = (me.username or config.BOT_USERNAME or "").lstrip("@")
            logger.info(f"Bot: @{config.BOT_USERNAME} (id={config.BOT_ID}) «{me.first_name or ''}», owner={config.OWNER_ID}")
        except Exception as e: logger.warning(f"get_me failed: {e}")
        await db.init_db()
        logger.info("DB initialized")
        try:
            await db.load_posted_news_from_file()
        except Exception as e:
            logger.warning(f"load_posted_news_from_file failed: {e}")
        try:
            await partner_manager.load()
            logger.info(f"Partners loaded: {len(partner_manager.campaigns)} campaigns")
        except: pass
        await ai_client.initialize()
        logger.info(f"AI client ready — {config.providers_status()}")
        asyncio.create_task(mood_loop(), name="mood_loop")
        asyncio.create_task(db.run_periodic_cleanup(), name="cleanup_loop")
        try:
            from bot.proactive import proactive_loop, summary_loop, set_bot
            set_bot(self.bot)
            asyncio.create_task(proactive_loop(), name="proactive_loop")
            asyncio.create_task(summary_loop(), name="summary_loop")
            logger.info("Proactive + summary loops enabled")
        except Exception as e: logger.warning(f"Proactive failed: {e}")
        # Channel scheduler — Настя posts to @chasnastya
        if config.CHANNEL_ID:
            asyncio.create_task(self._channel_scheduler(), name="channel_scheduler")
            logger.info(f"Channel scheduler enabled (@{config.CHANNEL_USERNAME})")
        await self._notify_owner()
        try: await self.bot.delete_webhook(drop_pending_updates=False)
        except: pass
        allowed = ["message", "edited_message", "channel_post", "edited_channel_post", "inline_query", "chosen_inline_result", "pre_checkout_query"]
        logger.info("=== Настя в сети — слушаю сообщения ===")
        polling_retries = 0
        while True:
            try:
                await self.dp.start_polling(self.bot, allowed_updates=allowed)
                break
            except Exception as e:
                polling_retries += 1
                logger.error(f"Polling error (attempt {polling_retries}): {type(e).__name__}: {e}")
                if polling_retries > 50: break
                await asyncio.sleep(5 if polling_retries <= 5 else 10)
        try: await ai_client.close()
        except: pass

    async def _channel_scheduler(self):
        """Background task: post to @chasnastya channel.
        50% RSS news, 20% web search news, 20% facts, 10% AI posts.
        Includes political filter and dedup.
        """
        from bot.persona import CHANNEL_POST_PROMPT, NASTYA_FACTS
        from bot.config import NEWS_SOURCES, POLITICAL_KEYWORDS
        from bot.web_search import search_ddg_html, fetch_article
        import feedparser
        await asyncio.sleep(120)
        post_interval = 1200  # 20 min
        
        def _is_political(text):
            t = (text or "").lower()
            return any(kw in t for kw in POLITICAL_KEYWORDS)
        
        async def _fetch_rss_news():
            """Fetch news from RSS sources."""
            import httpx
            results = []
            headers = {"User-Agent": "Mozilla/5.0 (compatible; NastyaBot/3.0; RSS Reader)"}
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers=headers) as client:
                for source in NEWS_SOURCES:
                    try:
                        resp = await client.get(source["url"])
                        if resp.status_code == 200:
                            feed = feedparser.parse(resp.text)
                            for entry in feed.entries[:3]:
                                title = entry.get("title", "")
                                link = entry.get("link", "")
                                summary = entry.get("summary", "") or entry.get("description", "")
                                # Strip HTML
                                import re
                                summary = re.sub(r"<[^>]+>", "", summary)[:300]
                                if title and link and not _is_political(title) and not _is_political(summary):
                                    results.append({
                                        "title": title,
                                        "url": link,
                                        "summary": summary,
                                        "source": source["name"],
                                        "category": source["category"],
                                    })
                    except: pass
            return results
        
        while True:
            try:
                channel_id = int(config.CHANNEL_ID)
                mood = await current_mood_descriptor()
                post_type = random.choices(["rss_news", "web_news", "fact", "ai_post"], weights=[5, 2, 2, 1])[0]
                
                if post_type == "fact":
                    fact = random.choice(NASTYA_FACTS)
                    await self.bot.send_message(channel_id, f"🎀 Факт от Насти:\n\n{fact}")
                    logger.info(f"Channel: posted fact")
                
                elif post_type == "rss_news":
                    # Fetch RSS news
                    news_items = await _fetch_rss_news()
                    # Dedup by URL
                    unposted = []
                    for item in news_items:
                        url_key = item["url"].split("?")[0].split("#")[0].rstrip("/").lower()
                        if not await db.is_news_posted(url_key):
                            unposted.append(item)
                    
                    if unposted:
                        item = random.choice(unposted)
                        prompt = (
                            f"Напиши пост для канала @chasnastya на основе этой новости.\n\n"
                            f"Заголовок: {item['title']}\n"
                            f"Источник: {item['source']}\n"
                            f"Краткое содержание: {item['summary'][:300]}\n\n"
                            f"Настроение: {mood}. Напиши 3-5 предложений, живо, с эмодзи, как Настя. "
                            f"Перескажи своими словами, добавь мнение. Без политики/войны. По-русски."
                        )
                        post = await ai_client.chat(prompt, system=CHANNEL_POST_PROMPT, max_tokens=400, allow_static_fallback=False, prefer_pollinations=True)
                        if post:
                            await self.bot.send_message(channel_id, post[:4000])
                            url_key = item["url"].split("?")[0].split("#")[0].rstrip("/").lower()
                            await db.mark_news_posted(url_key, item["title"])
                            logger.info(f"Channel: posted RSS news ({len(post)} chars) — {item['title'][:40]}")
                        else:
                            logger.warning("RSS news AI post empty — skip")
                    else:
                        logger.info("No unposted RSS news — fallback to AI post")
                        topics = ["мода и тренды", "новый фильм", "астрология", "шопинг", "BMW M3", "психология", "кофе", "путешествия"]
                        topic = random.choice(topics)
                        prompt = f"Напиши пост для канала @chasnastya на тему: {topic}. Настроение: {mood}. 3-5 предложений, живо, с эмодзи."
                        post = await ai_client.chat(prompt, system=CHANNEL_POST_PROMPT, max_tokens=300, allow_static_fallback=False, prefer_pollinations=True)
                        if post:
                            await self.bot.send_message(channel_id, post[:4000])
                            logger.info(f"Channel: posted AI fallback post ({len(post)} chars)")
                
                elif post_type == "web_news":
                    # Web search news
                    topics = ["мода тренды 2026", "новинки кино", "лайфстайл тренды", "технологии гаджеты", "красота новинки"]
                    topic = random.choice(topics)
                    results = []
                    try:
                        results = await search_ddg_html(topic, max_results=3)
                    except: pass
                    
                    if results:
                        unposted = []
                        for r in results:
                            url_key = r.url.split("?")[0].split("#")[0].rstrip("/").lower()
                            if not await db.is_news_posted(url_key):
                                unposted.append(r)
                        if unposted:
                            result = random.choice(unposted)
                            prompt = (
                                f"Напиши пост для канала @chasnastya на основе этой новости.\n\n"
                                f"Заголовок: {result.title}\n"
                                f"Источник: {result.source}\n"
                                f"Краткое содержание: {result.snippet[:300]}\n\n"
                                f"Настроение: {mood}. 3-5 предложений, живо, с эмодзи. Без политики/войны. По-русски."
                            )
                            post = await ai_client.chat(prompt, system=CHANNEL_POST_PROMPT, max_tokens=400, allow_static_fallback=False, prefer_pollinations=True)
                            if post:
                                await self.bot.send_message(channel_id, post[:4000])
                                url_key = result.url.split("?")[0].split("#")[0].rstrip("/").lower()
                                await db.mark_news_posted(url_key, result.title)
                                logger.info(f"Channel: posted web news ({len(post)} chars) — {result.title[:40]}")
                
                else:  # ai_post
                    topics = ["мода и тренды этого сезона", "новый фильм на Netflix", "астрология и знаки зодиака", "шопинг и скидки", "BMW M3 — лучшая тачка", "психология отношений", "тренды в соцсетях", "кофе и лайфстайл", "путешествия и Стамбул", "что нового в мире технологий"]
                    topic = random.choice(topics)
                    prompt = f"Напиши пост для канала @chasnastya на тему: {topic}. Настроение: {mood}. 3-5 предложений, живо, с эмодзи."
                    post = await ai_client.chat(prompt, system=CHANNEL_POST_PROMPT, max_tokens=300, allow_static_fallback=False, prefer_pollinations=True)
                    if post:
                        await self.bot.send_message(channel_id, post[:4000])
                        logger.info(f"Channel: posted AI post ({len(post)} chars)")
                        
            except asyncio.CancelledError: break
            except Exception as e:
                logger.error(f"Channel scheduler error: {e}")
            await asyncio.sleep(post_interval)

    async def _notify_owner(self):
        mood = await current_mood_descriptor()
        try:
            await self.bot.send_message(config.OWNER_ID, f"Я на связи 🎀 Настя, сейчас я {mood}. OpenClaw: {config.OPENCLAW_URL}. Провайдеры: {config.providers_status()}. Канал: @{config.CHANNEL_USERNAME}. Пиши или добавь в группу 💬")
        except: pass

async def main():
    global _openclaw_proc
    cfg_path = _generate_openclaw_config()
    _openclaw_proc = _start_openclaw_gateway(cfg_path)
    ready = await _wait_for_gateway(120.0)
    if not ready:
        logger.error("OpenClaw Gateway did not become ready — exiting")
        _stop_openclaw_gateway()
        sys.exit(1)
    bot = NastyaBot()
    def _sig(*_): asyncio.create_task(bot.dp.stop_polling())
    for sig in (signal.SIGINT, signal.SIGTERM):
        try: asyncio.get_running_loop().add_signal_handler(sig, _sig)
        except: pass
    try: await bot.start()
    finally: _stop_openclaw_gateway()

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
    except Exception as e:
        logger.exception(f"Fatal: {e}")
        _stop_openclaw_gateway()
        sys.exit(1)
