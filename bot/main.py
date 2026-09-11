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
        try: await self.bot.delete_webhook(drop_pending_updates=True)
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
        from bot.post_quality import (POST_STYLE, STRUCTURED_POST_RULES,
            ANTI_HALLUCINATION_RULES, RETRY_CRITIQUE_TMPL, build_hook_avoid,
            parse_structured_post, quality_gate, smart_hashtags,
            assemble_html_post, send_channel_post, prime_time_interval, notify_owner,
            sanitize_text)
        import feedparser
        await asyncio.sleep(120)
        post_interval = 1200  # 20 min day / 40 min night (prime-time cadence)
        failure_streak = 0

        async def _quality_channel_post(self_ref, channel_id: int, context_prompt: str, mood: str):
            """Shared quality pipeline for Nastya's channel posts (v2).
            Structured generation → gate → retry → HTML assembly → send → hooks.
            Returns True if posted."""
            try:
                hooks = await db.get_recent_hooks(8)
            except Exception:
                hooks = []
            hook_note = build_hook_avoid(hooks)
            style = POST_STYLE
            prompt = (
                f"Напиши пост для канала {style.channel}.\n\n"
                f"Контекст: настроение: {mood}\n\n"
                f"{context_prompt}\n\n"
                f"{STRUCTURED_POST_RULES}\n\n"
                f"{ANTI_HALLUCINATION_RULES}\n\n"
                f"{hook_note}\n\n"
                f"СТИЛЬ: живо, как настоящая Настя, эмодзи умеренно, женский род, по-русски. "
                f"НЕ начинай с 'Настя:'."
            )
            raw = await ai_client.chat(prompt, system=CHANNEL_POST_PROMPT,
                                       max_tokens=700, temperature=0.8,
                                       allow_static_fallback=False, prefer_pollinations=True)
            parsed = parse_structured_post(raw)
            if parsed:
                ok, reason = quality_gate(parsed, min_body=180)
            else:
                ok, reason = False, "unparseable"
            if not ok and raw:
                retry_prompt = (RETRY_CRITIQUE_TMPL.format(reason=reason, prev=raw[:1000])
                                + "\n\nИсходное задание:\n" + prompt)
                raw2 = await ai_client.chat(retry_prompt, system=CHANNEL_POST_PROMPT,
                                            max_tokens=700, temperature=0.75,
                                            allow_static_fallback=False, prefer_pollinations=True)
                parsed2 = parse_structured_post(raw2)
                if parsed2:
                    ok2, _ = quality_gate(parsed2, min_body=180)
                    if ok2:
                        parsed, ok = parsed2, True
            if parsed and not ok and reason == "no_question":
                parsed["question"] = ""
                ok, reason = True, "fixed_no_question"
            if not parsed and raw:
                import re as _re
                fallback_body = " ".join(raw.split())
                fallback_body = fallback_body.split("ХЭШТЕГИ")[0].strip()
                for marker in ("ЗАГОЛОВОК:", "ТЕКСТ:", "ВОПРОС:"):
                    fallback_body = fallback_body.replace(marker, "")
                if len(fallback_body) >= 180:
                    first_sent = _re.split(r"(?<=[.!?])" + chr(92) + "s+", fallback_body)[0][:110].strip()
                    parsed = {"headline": first_sent or "Новости от Насти", "body": fallback_body,
                              "question": "", "hashtags": []}
                    ok, reason = True, "fallback_plain"
            if not parsed or not ok:
                logger.warning(f"Nastya quality pipeline failed ({reason})")
                return False
            body = " ".join(sanitize_text(parsed["body"]).split())
            headline = sanitize_text(parsed["headline"])[:110]
            question = parsed.get("question") or style.default_question
            hashtags = parsed.get("hashtags") or smart_hashtags(
                f"{headline} {body}", style.hashtag_map, style.default_hashtags)
            if "#chasnastya" not in hashtags:
                hashtags = (hashtags + ["#chasnastya"])[:4]
            html_post, plain_post = assemble_html_post(
                headline, body, question, hashtags,
                footer=style.footer, headline_emoji=style.headline_emoji)
            sent = await send_channel_post(self_ref.bot, int(channel_id),
                                           html_post, plain_post, [], log=logger)
            if sent:
                try:
                    await self_ref._react_to_own_post(int(channel_id),
                                                      sent.message_id, plain_post[:200])
                except Exception:
                    pass
                await db.save_hook(plain_post[:70])
                return True
            return False
        
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
                ok_cycle = False
                channel_id = int(config.CHANNEL_ID)
                mood = await current_mood_descriptor()
                post_type = random.choices(["rss_news", "web_news", "fact", "ai_post"], weights=[5, 2, 2, 1])[0]
                
                if post_type == "fact":
                    fact = random.choice(NASTYA_FACTS)
                    html_post, plain_post = assemble_html_post(
                        "Факт от Насти", fact, "", ["#chasnastya"],
                        footer=POST_STYLE.footer, headline_emoji="🎀")
                    msg = await send_channel_post(self.bot, channel_id, html_post, plain_post,
                                                  [], log=logger)
                    if msg:
                        try:
                            await self._react_to_own_post(channel_id, msg.message_id, plain_post[:200])
                        except Exception:
                            pass
                        await db.save_hook(plain_post[:70])
                        ok_cycle = True
                        logger.info("Channel: posted fact (HTML)")

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
                        context_prompt = (
                            f"Основа — свежая новость:\n"
                            f"Заголовок: {item['title']}\n"
                            f"Источник: {item['source']}\n"
                            f"Краткое содержание: {item['summary'][:600]}\n\n"
                            f"Перескажи своими словами от лица Насти, добавь своё мнение "
                            f"и полезный контекст. Не копируй заголовок."
                        )
                        if await _quality_channel_post(self, channel_id, context_prompt, mood):
                            ok_cycle = True
                            url_key = item["url"].split("?")[0].split("#")[0].rstrip("/").lower()
                            await db.mark_news_posted(url_key, item["title"])
                            logger.info(f"Channel: posted RSS news (quality) — {item['title'][:40]}")
                        else:
                            logger.warning("RSS news quality post failed — skip")
                    else:
                        logger.info("No unposted RSS news — fallback to AI post")
                        topics = ["мода и тренды", "новый фильм", "астрология", "шопинг",
                                  "BMW M3", "психология", "кофе", "путешествия"]
                        topic = random.choice(topics)
                        if await _quality_channel_post(self, channel_id, f"Тема поста: {topic}.", mood):
                            ok_cycle = True
                            logger.info(f"Channel: posted AI fallback post (quality) — {topic}")

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
                            context_prompt = (
                                f"Основа — находка из интернета:\n"
                                f"Заголовок: {result.title}\n"
                                f"Источник: {result.source}\n"
                                f"Краткое содержание: {result.snippet[:600]}\n\n"
                                f"Расскажи об этом от лица Насти — почему это интересно сейчас, "
                                f"добавь своё мнение и деталь из трендов."
                            )
                            if await _quality_channel_post(self, channel_id, context_prompt, mood):
                                ok_cycle = True
                                url_key = result.url.split("?")[0].split("#")[0].rstrip("/").lower()
                                await db.mark_news_posted(url_key, result.title)
                                logger.info(f"Channel: posted web news (quality) — {result.title[:40]}")

                else:  # ai_post
                    topics = ["мода и тренды этого сезона", "новый фильм на Netflix", "астрология и знаки зодиака", "шопинг и скидки", "BMW M3 — лучшая тачка", "психология отношений", "тренды в соцсетях", "кофе и лайфстайл", "путешествия и Стамбул", "что нового в мире технологий"]
                    topic = random.choice(topics)
                    if await _quality_channel_post(self, channel_id, f"Тема поста: {topic}.", mood):
                        ok_cycle = True
                        logger.info(f"Channel: posted AI post (quality) — {topic}")

            except asyncio.CancelledError: break
            except Exception as e:
                logger.error(f"Channel scheduler error: {e}")

            # Failure streak → alert owner (rate-limited)
            try:
                if not ok_cycle:
                    failure_streak += 1
                    if failure_streak >= 3:
                        await notify_owner(
                            self.bot,
                            "Настя: 3 цикла подряд без постов в канал @chasnastya. "
                            "Проверь логи GitHub Actions.", min_gap_s=7200)
                        failure_streak = 0
                else:
                    failure_streak = 0
            except Exception:
                pass

            # Prime-time cadence: 20 min day / 40 min night (01:00-08:00 MSK)
            post_interval = prime_time_interval(day_s=1200, night_s=2400)
            await asyncio.sleep(post_interval)

    async def _react_to_own_post(self, channel_id: int, message_id: int, text: str = ""):
        """Set 3 positive reactions on own channel post with fallback to 1."""
        try:
            import random
            from aiogram.types import ReactionTypeEmoji
            # Only guaranteed Telegram-supported reaction emojis (no ❤️ variation selector)
            pool = ["👍", "❤", "🔥", "😄", "👏", "🎉"]
            emojis = random.sample(pool, 3)
            reaction_types = [ReactionTypeEmoji(type="emoji", emoji=e) for e in emojis]
            await self.bot.set_message_reaction(channel_id, message_id, reaction_types)
            logger.info(f"Reacted to own post (3): {channel_id}/{message_id} with {emojis}")
        except Exception as e:
            msg = str(e)
            if "REACTIONS_TOO_MANY" in msg or "REACTION_INVALID" in msg:
                try:
                    import random as _r
                    single_emoji = _r.choice(["👍", "❤", "🔥"])
                    single = [ReactionTypeEmoji(type="emoji", emoji=single_emoji)]
                    await self.bot.set_message_reaction(channel_id, message_id, single)
                    logger.info(f"Reacted to own post (1 fallback): {channel_id}/{message_id} with {single_emoji}")
                    return
                except Exception as e2:
                    logger.warning(f"React to own post fallback failed: {e2}")
            logger.warning(f"React to own post failed: {e}")

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
