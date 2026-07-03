"""Настя Admin handler — owner commands."""
import logging
from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command
from bot.config import config
from bot import database as db
from ai import client as ai_client

logger = logging.getLogger("nastya.admin")
admin_router = Router()

def _is_admin(message):
    uid = message.from_user.id if message.from_user else 0
    return uid == config.OWNER_ID or uid in config.ADMIN_IDS

@admin_router.message(Command("stats"))
async def cmd_stats(message):
    if not _is_admin(message): return
    s = ai_client.stats()
    await message.reply(f"📊 Статистика AI:\nЗапросов: {s.get('requests',0)}\nOpenClaw: {s.get('openclaw_ok',0)}\nPollinations: {s.get('pollinations_backup',0)}\nStatic: {s.get('static_fallback',0)}\nОшибок: {s.get('fail',0)}\nПоследняя: {s.get('last_error','—')[:80]}")

@admin_router.message(Command("providers"))
async def cmd_providers(message):
    if not _is_admin(message): return
    await message.reply(f"🔌 Провайдеры:\n{config.providers_status()}")

@admin_router.message(Command("models"))
async def cmd_models(message):
    if not _is_admin(message): return
    import httpx
    s = ai_client.stats()
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get("https://text.pollinations.ai/models")
        models = r.json() if r.status_code == 200 else []
    except: models = []
    lines = ["🤖 Модели Pollinations:"]
    for m in models: lines.append(f"  • {m.get('name','?')} — {m.get('description','')[:50]}")
    lines.append(f"\n📊 AI stats:\n  Запросов: {s.get('requests',0)}\n  OpenClaw: {s.get('openclaw_ok',0)}\n  Pollinations: {s.get('pollinations_backup',0)}\n  Ошибок: {s.get('fail',0)}")
    await message.reply("\n".join(lines))

@admin_router.message(Command("diag"))
async def cmd_diag(message):
    if not _is_admin(message): return
    c = message.chat
    u = message.from_user
    info = [f"🔧 Диагностика:", f"Бот: @{config.BOT_USERNAME} (id={config.BOT_ID})", f"Чат: id={c.id}, тип={c.type}, title={c.title or '—'}", f"Ты: {u.first_name} (id={u.id})", f"Провайдеры: {config.providers_status()}"]
    try:
        recent = await db.get_recent_group_messages(c.id, limit=5)
        info.append(f"\nЛог сообщений ({len(recent)}):")
        if not recent: info.append("  (пусто)")
        else:
            for m in recent[-5:]:
                who = m.get("first_name") or "?"
                if m.get("user_id") == config.BOT_ID: who = "Настя"
                info.append(f"  {who}: {(m.get('content') or '')[:50]}")
    except: pass
    try: await message.reply("\n".join(info))
    except: pass

@admin_router.message(Command("channel_on"))
async def cmd_channel_on(message):
    if not _is_admin(message): return
    parts = (message.text or "").split()
    if len(parts) < 2: await message.reply("Использование: /channel_on <chat_id>"); return
    try: chat_id = int(parts[1])
    except: await message.reply("chat_id должен быть числом"); return
    await db.set_channel_enabled(chat_id, True)
    await message.reply(f"✅ Реакции для канала {chat_id} включены")

@admin_router.message(Command("channel_off"))
async def cmd_channel_off(message):
    if not _is_admin(message): return
    parts = (message.text or "").split()
    if len(parts) < 2: await message.reply("Использование: /channel_off <chat_id>"); return
    try: chat_id = int(parts[1])
    except: await message.reply("chat_id должен быть числом"); return
    await db.set_channel_enabled(chat_id, False)
    await message.reply(f"🚫 Реакции для канала {chat_id} выключены")

@admin_router.message(Command("broadcast"))
async def cmd_broadcast(message):
    if not _is_admin(message): return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3: await message.reply("Использование: /broadcast <chat_id> <текст>"); return
    try: chat_id = int(parts[1])
    except: await message.reply("chat_id должен быть числом"); return
    try:
        await message.bot.send_message(chat_id, parts[2])
        await message.reply("✅ Отправлено")
    except Exception as e: await message.reply(f"❌ Ошибка: {e}")
