"""Nastya Bot — Database. SQLite with per-operation connections.

CRITICAL: Uses a NEW connection for each operation to avoid stale connections
on GitHub Actions where the runner can sleep between operations.
"""
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
    gender TEXT DEFAULT 'unknown',
    language_code TEXT DEFAULT 'ru',
    created_at REAL,
    total_messages INTEGER DEFAULT 0,
    last_active REAL,
    last_mood TEXT DEFAULT 'капризная',
    last_mood_change REAL DEFAULT 0
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
    ("любящая", "🥰", "Настя сегодня ласковая", 0.16),
    ("загадочная", "🔮", "Настя говорит загадками", 0.10),
    ("модная", "👗", "Настя одержима модой", 0.10),
    ("ремонтная", "🔨", "Настя одержима ремонтом", 0.08),
    ("спортивная", "🏃‍♀️", "Настя в спортивном настроении", 0.08),
    ("голодная", "🍽️", "Настя хочет есть", 0.08),
    ("философская", "🧘‍♀️", "Настя философствует", 0.08),
    ("драма", "🎭", "Настя в драме", 0.06),
    ("щедрая", "💝", "Настя добрая сегодня", 0.04),
]


class Database:
    """Database with per-operation connections — NEVER stale!"""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._initialized = False

    async def _get_conn(self) -> aiosqlite.Connection:
        """Get a FRESH connection for each operation."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self.db_path)
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        return conn

    async def init(self) -> None:
        """Initialize database schema."""
        if self._initialized:
            return

        conn = await self._get_conn()
        try:
            await conn.executescript(SCHEMA_SQL)

            # Insert moods
            async with conn.execute("SELECT COUNT(*) FROM nastya_moods") as cur:
                count = (await cur.fetchone())[0]
            if count == 0:
                for mood, emoji, desc, prob in MOODS:
                    await conn.execute(
                        "INSERT INTO nastya_moods (mood, emoji, description, probability) VALUES (?,?,?,?)",
                        (mood, emoji, desc, prob),
                    )
                await conn.commit()

            # Migrations — add columns if not exist
            for col_def in [
                "ALTER TABLE users ADD COLUMN gender TEXT DEFAULT 'unknown'",
                "ALTER TABLE users ADD COLUMN last_mood TEXT DEFAULT 'капризная'",
                "ALTER TABLE users ADD COLUMN last_mood_change REAL DEFAULT 0",
            ]:
                try:
                    await conn.execute(col_def)
                    await conn.commit()
                except Exception:
                    pass  # Column already exists

            self._initialized = True
            logger.info(f"Database initialized: {self.db_path}")
        finally:
            await conn.close()

    async def close(self) -> None:
        """Nothing to close — per-operation connections."""
        self._initialized = False

    # ── Users ────────────────────────────────────────────────

    async def get_or_create_user(self, user_id: int, username: str = "",
                                  first_name: str = "", language_code: str = "ru") -> Dict:
        conn = await self._get_conn()
        try:
            async with conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cur:
                row = await cur.fetchone()
                if row:
                    cols = [d[0] for d in cur.description]
                    return dict(zip(cols, row))

            now = time.time()
            await conn.execute(
                """INSERT OR IGNORE INTO users
                (user_id, username, first_name, gender, language_code, created_at, total_messages, last_active, last_mood, last_mood_change)
                VALUES (?, ?, ?, 'unknown', ?, ?, 0, ?, 'капризная', 0)""",
                (user_id, username, first_name, language_code, now, now),
            )
            await conn.commit()
            return {"user_id": user_id, "username": username, "first_name": first_name,
                    "gender": "unknown", "total_messages": 0, "last_mood": "капризная"}
        finally:
            await conn.close()

    async def set_gender(self, user_id: int, gender: str) -> None:
        conn = await self._get_conn()
        try:
            await conn.execute("UPDATE users SET gender = ? WHERE user_id = ?", (gender, user_id))
            await conn.commit()
        finally:
            await conn.close()

    async def get_gender(self, user_id: int) -> str:
        conn = await self._get_conn()
        try:
            async with conn.execute("SELECT gender FROM users WHERE user_id = ?", (user_id,)) as cur:
                row = await cur.fetchone()
                return row[0] if row else "unknown"
        finally:
            await conn.close()

    async def increment_messages(self, user_id: int) -> int:
        conn = await self._get_conn()
        try:
            now = time.time()
            await conn.execute(
                "UPDATE users SET total_messages = total_messages + 1, last_active = ? WHERE user_id = ?",
                (now, user_id),
            )
            await conn.commit()
            async with conn.execute("SELECT total_messages FROM users WHERE user_id = ?", (user_id,)) as cur:
                row = await cur.fetchone()
                return row[0] if row else 0
        finally:
            await conn.close()

    async def get_user_mood(self, user_id: int) -> str:
        """Get current mood for user, change it periodically."""
        conn = await self._get_conn()
        try:
            async with conn.execute(
                "SELECT last_mood, last_mood_change FROM users WHERE user_id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
                if not row:
                    return "капризная"
                mood, last_change = row
                # Change mood every 15-30 minutes
                if time.time() - (last_change or 0) > 900:
                    new_mood = await self._pick_random_mood(conn)
                    await conn.execute(
                        "UPDATE users SET last_mood = ?, last_mood_change = ? WHERE user_id = ?",
                        (new_mood, time.time(), user_id),
                    )
                    await conn.commit()
                    return new_mood
                return mood or "капризная"
        finally:
            await conn.close()

    async def _pick_random_mood(self, conn) -> str:
        import random
        moods = []
        probs = []
        async with conn.execute("SELECT mood, probability FROM nastya_moods") as cur:
            async for row in cur:
                moods.append(row[0])
                probs.append(row[1])
        if not moods:
            return "капризная"
        total = sum(probs)
        probs = [p / total for p in probs]
        return random.choices(moods, weights=probs, k=1)[0]

    async def set_user_mood(self, user_id: int, mood: str) -> None:
        conn = await self._get_conn()
        try:
            await conn.execute(
                "UPDATE users SET last_mood = ?, last_mood_change = ? WHERE user_id = ?",
                (mood, time.time(), user_id),
            )
            await conn.commit()
        finally:
            await conn.close()

    # ── Chat History ─────────────────────────────────────────

    async def add_message(self, user_id: int, role: str, content: str) -> None:
        conn = await self._get_conn()
        try:
            now = time.time()
            await conn.execute(
                "INSERT INTO chat_history (user_id, role, content, created_at) VALUES (?,?,?,?)",
                (user_id, role, content, now),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def get_history(self, user_id: int, limit: int = 50, max_age_hours: int = 720) -> List[Dict[str, str]]:
        """Get recent chat history. 30 days (720h) by default for context."""
        conn = await self._get_conn()
        try:
            cutoff = time.time() - (max_age_hours * 3600)
            messages = []
            async with conn.execute(
                "SELECT role, content FROM chat_history WHERE user_id = ? AND created_at > ? ORDER BY created_at DESC LIMIT ?",
                (user_id, cutoff, limit),
            ) as cur:
                async for row in cur:
                    messages.append({"role": row[0], "content": row[1]})
            messages.reverse()
            return messages
        finally:
            await conn.close()

    async def clear_history(self, user_id: int) -> None:
        conn = await self._get_conn()
        try:
            await conn.execute("DELETE FROM chat_history WHERE user_id = ?", (user_id,))
            await conn.commit()
        finally:
            await conn.close()

    # ── Donations ────────────────────────────────────────────

    async def record_donation(self, user_id: int, stars: int, charge_id: str) -> int:
        conn = await self._get_conn()
        try:
            now = time.time()
            cur = await conn.execute(
                "INSERT INTO donations (user_id, stars_amount, telegram_charge_id, created_at) VALUES (?,?,?,?)",
                (user_id, stars, charge_id, now),
            )
            await conn.commit()
            return cur.lastrowid
        finally:
            await conn.close()

    async def get_total_donated(self, user_id: int) -> int:
        conn = await self._get_conn()
        try:
            async with conn.execute(
                "SELECT COALESCE(SUM(stars_amount),0) FROM donations WHERE user_id = ?", (user_id,)
            ) as cur:
                return (await cur.fetchone())[0]
        finally:
            await conn.close()

    async def get_donation_count(self, user_id: int) -> int:
        conn = await self._get_conn()
        try:
            async with conn.execute("SELECT COUNT(*) FROM donations WHERE user_id = ?", (user_id,)) as cur:
                return (await cur.fetchone())[0]
        finally:
            await conn.close()

    # ── Stats ───────────────────────────────────────────────

    async def get_stats(self) -> Dict:
        conn = await self._get_conn()
        try:
            async with conn.execute("SELECT COUNT(*) FROM users") as cur:
                total_users = (await cur.fetchone())[0]
            async with conn.execute("SELECT COALESCE(SUM(stars_amount),0) FROM donations") as cur:
                total_stars = (await cur.fetchone())[0]
            async with conn.execute("SELECT COUNT(*) FROM donations") as cur:
                total_donations = (await cur.fetchone())[0]
            return {"total_users": total_users, "total_stars": total_stars, "total_donations": total_donations}
        finally:
            await conn.close()
