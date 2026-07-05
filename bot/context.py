"""Настя Context — assembles who/where/recent/memory/summaries for AI prompts."""
import re, time
from typing import List
from aiogram.types import Message
from bot.config import config

# Known bot siblings — bots recognize each other and don't keep "introducing" themselves
KNOWN_BOTS = {
    "asiaexp_bot": "Ася — автоэксперт, @sochiautoparts",
    "asluba_bot": "Люба — копирайтер из Сочи",
    "asnastya_bot": "Настя — блогер, астрология",
    "asmasha_bot": "Маша — BMW M-эксперт, @bmw_mpower_club",
    "asdasha_bot": "Даша — дизайнер мебели, Абакан",
    "aimega_bot": "Василий — парень из Сочи",
}


def is_known_bot(username: str) -> bool:
    if not username:
        return False
    return username.lstrip("@").lower() in KNOWN_BOTS


def get_bot_description(username: str) -> str:
    if not username:
        return ""
    return KNOWN_BOTS.get(username.lstrip("@").lower(), "")
from bot import database as db

def user_descriptor(message):
    u = message.from_user
    if not u: return "кто-то"
    name = u.first_name or u.username or "кто-то"
    return f"{name} (бот)" if u.is_bot else name

def chat_descriptor(message):
    c = message.chat
    return c.title or c.username or ("личка" if c.type == "private" else "чат")

def is_directed_at_bot(message):
    text = (message.text or "").lower()
    handle = config.BOT_HANDLE.lower()
    if not handle: return False
    if f"@{handle}" in text: return True
    if message.reply_to_message and message.reply_to_message.from_user:
        if message.reply_to_message.from_user.id == config.BOT_ID: return True
    if text.startswith(handle): return True
    return False

def strip_mention(text):
    if not text: return ""
    handle = config.BOT_HANDLE
    out = re.sub(rf"(?i)\s*@{re.escape(handle)}\b", "", text)
    out = re.sub(rf"(?i)^{re.escape(handle)}[,\s:]*", "", out)
    return out.strip()

def recent_messages_to_text(recent, limit=8):
    lines = []
    for m in recent[-limit:]:
        who = m.get("first_name") or m.get("username") or "кто-то"
        if m.get("user_id") == config.BOT_ID: who = "Настя"
        content = m.get("content") or ""
        if m.get("is_media"):
            cap = m.get("media_caption") or ""
            content = f"[фото{': ' + cap if cap else ''}]"
        if content.strip(): lines.append(f"{who}: {content}")
    return "\n".join(lines)

async def build_user_profile(user_id):
    user = await db.get_user(user_id)
    if not user: return ""
    parts = []
    name = user.get("first_name") or user.get("username") or f"пользователь {user_id}"
    if user.get("username"): name += f" (@{user['username']})"
    parts.append(name)
    total = user.get("msg_count", 0) or 0
    if total > 0:
        if total < 3: parts.append("(общались мало)")
        elif total < 20: parts.append(f"(виделись {total} раз)")
        else: parts.append(f"(давние знакомые, ~{total} сообщений)")
    facts_rows = await db.get_user_facts(user_id, limit=10)
    if facts_rows:
        parts.append("что знаю о нём:\n- " + "\n- ".join(r["fact"] for r in facts_rows))
    return "\n".join(parts) if len(parts) > 1 else ""

def build_group_context(message, recent_text, memory_facts, author_profile="", summaries=None):
    who = user_descriptor(message)
    where = chat_descriptor(message)
    now = _now_moscow()
    parts = [f"Контекст: чат «{where}», сейчас {now}."]
    if author_profile: parts.append(f"Кто пишет:\n{author_profile}")
    else: parts.append(f"Пишет: {who}.")
    if summaries:
        parts.append("О чём ранее говорили в чате:\n" + "\n".join(f"  • {s.get('summary','')}" for s in summaries))
    if recent_text: parts.append("Недавняя беседа:\n" + recent_text)
    if memory_facts: parts.append("Что помнишь об участниках чата:\n- " + "\n- ".join(memory_facts))
    return "\n\n".join(parts)

def build_private_context(user_profile):
    now = _now_moscow()
    parts = [f"Сейчас {now}."]
    if user_profile: parts.append(f"С кем общаешься:\n{user_profile}")
    return "\n\n".join(parts)

def _now_moscow():
    t = time.gmtime()
    h = (t.tm_hour + 3) % 24
    tod = "ночь" if 0 <= h < 6 else "утро" if h < 12 else "день" if h < 18 else "вечер"
    days = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
    return f"{h:02d}:{t.tm_min:02d}, {tod}, {days[t.tm_wday]}"

_FACT_PATTERNS = [
    ("я живу в ", "живёт в"), ("я из ", "родом из"), ("я работаю в ", "работает в"),
    ("я работаю ", "работает"), ("я учусь ", "учится"), ("я учусь в ", "учится в"),
    ("у меня собака", "есть собака"), ("у меня кот", "есть кот"), ("у меня кошка", "есть кошка"),
    ("у меня ребенок", "есть ребенок"), ("у меня дети", "есть дети"),
    ("я люблю ", "любит"), ("мне нравится ", "нравится"), ("я обожаю ", "обожает"),
    ("я ненавижу ", "не любит"), ("я фрилансер", "фрилансер"), ("я программист", "программист"),
    ("я дизайнер", "дизайнер"), ("я маркетолог", "маркетолог"), ("я езжу на ", "ездит на"),
    ("я был в ", "был в"), ("я была в ", "была в"),
]

async def extract_and_store_facts(user_id, name, text, source_chat=0):
    if not text or not name: return []
    t = text.lower().strip()
    stored = []
    for pattern, label in _FACT_PATTERNS:
        if pattern in t:
            idx = t.index(pattern) + len(pattern)
            rest = text[idx:idx + 80].split(".")[0].split("!")[0].split("?")[0].strip()
            if rest and 2 < len(rest) < 80:
                fact = f"{name} {label} {rest}".strip()
                if not await db.has_user_fact(user_id, fact):
                    await db.add_user_fact(user_id, fact, source_chat)
                    stored.append(fact)
                break
    return stored
