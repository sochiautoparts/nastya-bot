"""Настя Database — SQLite async (aiosqlite), WAL mode."""
import logging, time
from typing import List, Optional
import aiosqlite
from bot.config import config

logger = logging.getLogger("nastya.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS posted_news (
    news_id TEXT PRIMARY KEY,
    title TEXT,
    posted_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS channels (chat_id INTEGER PRIMARY KEY, username TEXT DEFAULT '', title TEXT DEFAULT '', enabled INTEGER DEFAULT 1, seen INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS group_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, username TEXT DEFAULT '', first_name TEXT DEFAULT '', content TEXT DEFAULT '', is_media INTEGER DEFAULT 0, media_caption TEXT DEFAULT '', is_bot INTEGER DEFAULT 0, ts INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_gm_chat_ts ON group_messages(chat_id, id DESC);
CREATE TABLE IF NOT EXISTS group_memory (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, fact TEXT NOT NULL, ts INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_gmem_chat ON group_memory(chat_id, id DESC);
CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT DEFAULT '', first_name TEXT DEFAULT '', last_name TEXT DEFAULT '', is_bot INTEGER DEFAULT 0, first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL, msg_count INTEGER DEFAULT 0, private_msgs INTEGER DEFAULT 0, group_msgs INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS user_facts (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, fact TEXT NOT NULL, source_chat INTEGER NOT NULL, ts INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_uf_user ON user_facts(user_id, id DESC);
CREATE TABLE IF NOT EXISTS private_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, ts INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_pm_user_ts ON private_messages(user_id, id DESC);
CREATE TABLE IF NOT EXISTS reactions_dedup (message_id INTEGER NOT NULL, chat_id INTEGER NOT NULL, ts INTEGER NOT NULL, PRIMARY KEY (chat_id, message_id));
CREATE TABLE IF NOT EXISTS donations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    stars_amount INTEGER,
    telegram_charge_id TEXT,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_donations_user ON donations(user_id);

CREATE TABLE IF NOT EXISTS chat_summaries (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, summary TEXT NOT NULL, topics TEXT DEFAULT '', ts INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_cs_chat ON chat_summaries(chat_id, id DESC);
CREATE TABLE IF NOT EXISTS moods (id INTEGER PRIMARY KEY DEFAULT 1, mood TEXT DEFAULT 'спокойная', energy REAL DEFAULT 0.5, ts INTEGER NOT NULL);
"""

_db: Optional[aiosqlite.Connection] = None

async def init_db():
    global _db
    import os
    os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
    _db = await aiosqlite.connect(config.DB_PATH)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL;")
    await _db.execute("PRAGMA synchronous=NORMAL;")
    await _db.executescript(_SCHEMA)
    await _db.commit()
    logger.info(f"DB ready at {config.DB_PATH}")

async def close_db():
    global _db
    if _db: await _db.close(); _db = None

def _conn():
    if _db is None: raise RuntimeError("DB not initialised")
    return _db

# Channels
async def upsert_channel(chat_id, username="", title=""):
    await _conn().execute("INSERT INTO channels(chat_id, username, title, enabled, seen) VALUES(?, ?, ?, 1, ?) ON CONFLICT(chat_id) DO UPDATE SET username=excluded.username, title=excluded.title, seen=excluded.seen", (chat_id, username, title, int(time.time())))
    await _conn().commit()

async def is_channel_enabled(chat_id):
    cur = await _conn().execute("SELECT enabled FROM channels WHERE chat_id=?", (chat_id,))
    row = await cur.fetchone()
    return row is None or row["enabled"] == 1

async def set_channel_enabled(chat_id, enabled):
    await _conn().execute("INSERT INTO channels(chat_id, enabled, seen) VALUES(?, ?, ?) ON CONFLICT(chat_id) DO UPDATE SET enabled=excluded.enabled", (chat_id, 1 if enabled else 0, int(time.time())))
    await _conn().commit()

# Group messages
async def add_group_message(chat_id, user_id, username, first_name, content, is_media=False, media_caption="", is_bot=False):
    await _conn().execute("INSERT INTO group_messages(chat_id, user_id, username, first_name, content, is_media, media_caption, is_bot, ts) VALUES(?,?,?,?,?,?,?,?,?)", (chat_id, user_id, username, first_name, content, int(is_media), media_caption, int(is_bot), int(time.time())))
    await _conn().commit()
    await _conn().execute("DELETE FROM group_messages WHERE chat_id=? AND id NOT IN (SELECT id FROM group_messages WHERE chat_id=? ORDER BY id DESC LIMIT ?)", (chat_id, chat_id, config.GROUP_MEMORY_SIZE * 2))
    await _conn().commit()

async def get_recent_group_messages(chat_id, limit=12):
    cur = await _conn().execute("SELECT * FROM group_messages WHERE chat_id=? ORDER BY id DESC LIMIT ?", (chat_id, limit))
    rows = await cur.fetchall()
    return [dict(r) for r in reversed(rows)]

async def get_active_group_chats(within_hours=24, limit=20):
    cutoff = int(time.time()) - within_hours * 3600
    cur = await _conn().execute("SELECT DISTINCT chat_id FROM group_messages WHERE ts > ? AND chat_id < 0 LIMIT ?", (cutoff, limit))
    return [r["chat_id"] for r in await cur.fetchall()]

async def last_bot_message_time(chat_id):
    cur = await _conn().execute("SELECT ts FROM group_messages WHERE chat_id=? AND user_id=? ORDER BY id DESC LIMIT 1", (chat_id, config.BOT_ID))
    row = await cur.fetchone()
    return float(row["ts"]) if row else 0.0

async def last_message_time(chat_id):
    cur = await _conn().execute("SELECT ts FROM group_messages WHERE chat_id=? ORDER BY id DESC LIMIT 1", (chat_id,))
    row = await cur.fetchone()
    return float(row["ts"]) if row else 0.0

# Group memory
async def add_group_memory(chat_id, user_id, fact):
    await _conn().execute("INSERT INTO group_memory(chat_id, user_id, fact, ts) VALUES(?,?,?,?)", (chat_id, user_id, fact, int(time.time())))
    await _conn().commit()

async def get_group_memory(chat_id, user_id=None, limit=8):
    if user_id is not None:
        cur = await _conn().execute("SELECT * FROM group_memory WHERE chat_id=? AND user_id=? ORDER BY id DESC LIMIT ?", (chat_id, user_id, limit))
    else:
        cur = await _conn().execute("SELECT * FROM group_memory WHERE chat_id=? ORDER BY id DESC LIMIT ?", (chat_id, limit))
    return [dict(r) for r in await cur.fetchall()]

# Users
async def upsert_user(user_id, username="", first_name="", last_name="", is_bot=False, in_private=False, in_group=False):
    now = int(time.time())
    await _conn().execute("INSERT INTO users(user_id, username, first_name, last_name, is_bot, first_seen, last_seen, msg_count, private_msgs, group_msgs) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name, last_name=excluded.last_name, last_seen=excluded.last_seen, msg_count=users.msg_count+1, private_msgs=users.private_msgs+?, group_msgs=users.group_msgs+?", (user_id, username, first_name, last_name, int(is_bot), now, now, 1, int(in_private), int(in_group), int(in_private), int(in_group)))
    await _conn().commit()

async def get_user(user_id):
    cur = await _conn().execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = await cur.fetchone()
    return dict(row) if row else None

async def add_user_fact(user_id, fact, source_chat=0):
    await _conn().execute("INSERT INTO user_facts(user_id, fact, source_chat, ts) VALUES(?,?,?,?)", (user_id, fact, source_chat, int(time.time())))
    await _conn().commit()

async def get_user_facts(user_id, limit=12):
    cur = await _conn().execute("SELECT fact FROM user_facts WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit))
    return [dict(r) for r in await cur.fetchall()]

async def has_user_fact(user_id, fact):
    cur = await _conn().execute("SELECT 1 FROM user_facts WHERE user_id=? AND LOWER(fact)=LOWER(?) LIMIT 1", (user_id, fact))
    return await cur.fetchone() is not None

async def clear_user_facts(user_id):
    cur = await _conn().execute("DELETE FROM user_facts WHERE user_id=?", (user_id,)); await _conn().commit(); return cur.rowcount or 0

# Private messages
async def add_private_message(user_id, role, content):
    await _conn().execute("INSERT INTO private_messages(user_id, role, content, ts) VALUES(?,?,?,?)", (user_id, role, content, int(time.time())))
    await _conn().commit()
    await _conn().execute("DELETE FROM private_messages WHERE user_id=? AND id NOT IN (SELECT id FROM private_messages WHERE user_id=? ORDER BY id DESC LIMIT 80)", (user_id, user_id))
    await _conn().commit()

async def get_private_history(user_id, limit=16):
    cur = await _conn().execute("SELECT role, content FROM private_messages WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit))
    return [{"role": r["role"], "content": r["content"]} for r in reversed(await cur.fetchall())]

async def clear_private_history(user_id):
    cur = await _conn().execute("DELETE FROM private_messages WHERE user_id=?", (user_id,)); await _conn().commit(); return cur.rowcount or 0

# Reaction dedup
async def already_reacted(chat_id, message_id):
    cur = await _conn().execute("SELECT 1 FROM reactions_dedup WHERE chat_id=? AND message_id=?", (chat_id, message_id))
    return await cur.fetchone() is not None

async def mark_reacted(chat_id, message_id):
    await _conn().execute("INSERT OR IGNORE INTO reactions_dedup(chat_id, message_id, ts) VALUES(?,?,?)", (chat_id, message_id, int(time.time())))
    await _conn().commit()

# Chat summaries
async def add_chat_summary(chat_id, summary, topics=""):
    await _conn().execute("INSERT INTO chat_summaries(chat_id, summary, topics, ts) VALUES(?,?,?,?)", (chat_id, summary, topics, int(time.time())))
    await _conn().commit()
    await _conn().execute("DELETE FROM chat_summaries WHERE chat_id=? AND id NOT IN (SELECT id FROM chat_summaries WHERE chat_id=? ORDER BY id DESC LIMIT 3)", (chat_id, chat_id))
    await _conn().commit()

async def get_chat_summaries(chat_id, limit=2):
    cur = await _conn().execute("SELECT summary, topics, ts FROM chat_summaries WHERE chat_id=? ORDER BY id DESC LIMIT ?", (chat_id, limit))
    return [dict(r) for r in await cur.fetchall()]

# Mood
async def get_mood():
    cur = await _conn().execute("SELECT mood, energy FROM moods WHERE id=1")
    row = await cur.fetchone()
    if row: return dict(row)
    await _conn().execute("INSERT OR IGNORE INTO moods(id, mood, energy, ts) VALUES(1, 'спокойная', 0.5, ?)", (int(time.time()),))
    await _conn().commit()
    return {"mood": "спокойная", "energy": 0.5}

async def set_mood(mood, energy):
    await _conn().execute("INSERT INTO moods(id, mood, energy, ts) VALUES(1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET mood=excluded.mood, energy=excluded.energy, ts=excluded.ts", (mood, energy, int(time.time())))
    await _conn().commit()

# Cleanup
async def record_donation(user_id, stars, charge_id):
    await _conn().execute("INSERT INTO donations (user_id, stars_amount, telegram_charge_id, created_at) VALUES (?,?,?,?)", (user_id, stars, charge_id, int(time.time())))
    await _conn().commit()

async def get_total_donated(user_id):
    cur = await _conn().execute("SELECT COALESCE(SUM(stars_amount),0) FROM donations WHERE user_id=?", (user_id,))
    row = await cur.fetchone()
    return int(row[0]) if row else 0



async def is_news_posted(news_id: str) -> bool:
    cur = await _conn().execute("SELECT 1 FROM posted_news WHERE news_id=?", (news_id,))
    return await cur.fetchone() is not None

async def mark_news_posted(news_id: str, title: str = "") -> None:
    await _conn().execute("INSERT OR IGNORE INTO posted_news(news_id, title, posted_at) VALUES(?,?,?)", (news_id, title[:200], int(time.time())))
    await _conn().commit()
    # Also save to file for backup
    try:
        import json, os
        filepath = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "posted_news.json")
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        data = {}
        if os.path.exists(filepath):
            with open(filepath, "r") as f:
                data = json.load(f)
        data[news_id] = {"title": title[:200], "ts": int(time.time())}
        if len(data) > 1000:
            sorted_items = sorted(data.items(), key=lambda x: x[1]["ts"])
            data = dict(sorted_items[-1000:])
        with open(filepath, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except: pass

async def load_posted_news_from_file():
    try:
        import json, os
        filepath = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "posted_news.json")
        if os.path.exists(filepath):
            with open(filepath, "r") as f:
                data = json.load(f)
            count = 0
            for news_id, info in data.items():
                await _conn().execute("INSERT OR IGNORE INTO posted_news(news_id, title, posted_at) VALUES(?,?,?)", (news_id, info.get("title", ""), info.get("ts", 0)))
                count += 1
            await _conn().commit()
            if count > 0:
                import logging
                logging.getLogger("nastya.db").info(f"Loaded {count} posted_news from file backup")
    except: pass

async def run_periodic_cleanup():
    import asyncio
    while True:
        await asyncio.sleep(600)
        try:
            cutoff = int(time.time()) - 3600
            await _conn().execute("DELETE FROM reactions_dedup WHERE ts < ?", (cutoff,))
            await _conn().commit()
        except Exception as e:
            logger.debug(f"cleanup error: {e}")
