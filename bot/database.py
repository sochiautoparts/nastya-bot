"""Nastya Bot — Database. SQLite with WAL mode."""
import aiosqlite
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from bot.config import DB_PATH

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    language_code TEXT DEFAULT 'ru',
    created_at REAL,
    total_messages INTEGER DEFAULT 0,
    last_active REAL
);

CREATE TABLE IF NOT EXISTS chat_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    role TEXT,
    content TEXT,
    created_at REAL
);

CREATE TABLE IF NOT EXISTS donations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    stars_amount INTEGER,
    telegram_charge_id TEXT,
    created_at REAL
);

CREATE TABLE IF NOT EXISTS nastya_moods (
    mood TEXT PRIMARY KEY,
    emoji TEXT,
    description TEXT,
    probability REAL DEFAULT 0.1
);

CREATE INDEX IF NOT EXISTS idx_chat_history_user ON chat_history(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_donations_user ON donations(user_id);
"""

MOODS = [
    ("капризная", "😤", "Настя в капризном настроении", 0.22),
    ("любящая", "🥰", "Настя сегодня ласковая", 0.18),
    ("загадочная", "🔮", "Настя говорит загадками", 0.12),
    ("модная", "👗", "Настя одержима модой", 0.12),
    ("ремонтная", "🔨", "Настя одержима ремонтом и дизайном", 0.10),
    ("философская", "🧘‍♀️", "Настя философствует", 0.10),
    ("драма", "🎭", "Настя в драме", 0.10),
    ("щедрая", "💝", "Настя добрая сегодня", 0.06),
]


class Database:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def init(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.executescript(SCHEMA_SQL)

        # Insert moods if empty
        async with self._db.execute("SELECT COUNT(*) FROM nastya_moods") as cur:
            count = (await cur.fetchone())[0]
        if count == 0:
            for mood, emoji, desc, prob in MOODS:
                await self._db.execute(
                    "INSERT INTO nastya_moods (mood, emoji, description, probability) VALUES (?,?,?,?)",
                    (mood, emoji, desc, prob),
                )
            await self._db.commit()
        logger.info(f"Database initialized: {self.db_path}")

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    # ── Users ────────────────────────────────────────────────

    async def get_or_create_user(self, user_id: int, username: str = "", first_name: str = "", language_code: str = "ru") -> Dict:
        async with self._db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            if row:
                cols = [d[0] for d in cur.description]
                return dict(zip(cols, row))

        now = time.time()
        await self._db.execute(
            """INSERT OR IGNORE INTO users (user_id, username, first_name, language_code, created_at, total_messages, last_active)
            VALUES (?, ?, ?, ?, ?, 0, ?)""",
            (user_id, username, first_name, language_code, now, now),
        )
        await self._db.commit()
        return {"user_id": user_id, "username": username, "first_name": first_name, "total_messages": 0}

    async def increment_messages(self, user_id: int) -> int:
        now = time.time()
        await self._db.execute(
            "UPDATE users SET total_messages = total_messages + 1, last_active = ? WHERE user_id = ?",
            (now, user_id),
        )
        await self._db.commit()
        async with self._db.execute("SELECT total_messages FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

    # ── Chat History ─────────────────────────────────────────

    async def add_message(self, user_id: int, role: str, content: str) -> None:
        now = time.time()
        await self._db.execute(
            "INSERT INTO chat_history (user_id, role, content, created_at) VALUES (?,?,?,?)",
            (user_id, role, content, now),
        )
        await self._db.commit()

    async def get_history(self, user_id: int, limit: int = 30) -> List[Dict[str, str]]:
        messages = []
        async with self._db.execute(
            "SELECT role, content FROM chat_history WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ) as cur:
            async for row in cur:
                messages.append({"role": row[0], "content": row[1]})
        messages.reverse()
        return messages

    async def clear_history(self, user_id: int) -> None:
        await self._db.execute("DELETE FROM chat_history WHERE user_id = ?", (user_id,))
        await self._db.commit()

    # ── Donations ────────────────────────────────────────────

    async def record_donation(self, user_id: int, stars: int, charge_id: str) -> int:
        now = time.time()
        cur = await self._db.execute(
            "INSERT INTO donations (user_id, stars_amount, telegram_charge_id, created_at) VALUES (?,?,?,?)",
            (user_id, stars, charge_id, now),
        )
        await self._db.commit()
        return cur.lastrowid

    async def get_total_donated(self, user_id: int) -> int:
        async with self._db.execute("SELECT COALESCE(SUM(stars_amount),0) FROM donations WHERE user_id = ?", (user_id,)) as cur:
            return (await cur.fetchone())[0]

    async def get_donation_count(self, user_id: int) -> int:
        async with self._db.execute("SELECT COUNT(*) FROM donations WHERE user_id = ?", (user_id,)) as cur:
            return (await cur.fetchone())[0]

    # ── Moods ────────────────────────────────────────────────

    async def get_random_mood(self) -> Dict:
        import random
        moods = []
        async with self._db.execute("SELECT mood, emoji, description FROM nastya_moods") as cur:
            async for row in cur:
                moods.append({"mood": row[0], "emoji": row[1], "description": row[2]})
        if not moods:
            return {"mood": "капризная", "emoji": "😤", "description": "Настя в капризном настроении"}
        return random.choice(moods)

    # ── Stats ────────────────────────────────────────────────

    async def get_stats(self) -> Dict:
        async with self._db.execute("SELECT COUNT(*) FROM users") as cur:
            total_users = (await cur.fetchone())[0]
        async with self._db.execute("SELECT COALESCE(SUM(stars_amount),0) FROM donations") as cur:
            total_stars = (await cur.fetchone())[0]
        async with self._db.execute("SELECT COUNT(*) FROM donations") as cur:
            total_donations = (await cur.fetchone())[0]
        return {"total_users": total_users, "total_stars": total_stars, "total_donations": total_donations}
