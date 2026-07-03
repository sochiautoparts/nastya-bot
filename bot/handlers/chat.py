"""Настя Private chat handler — normal AI conversation with memory."""
import asyncio, logging, random
from aiogram import Router, F
from aiogram.types import Message
from aiogram.enums import ChatAction
from aiogram.filters import Command
from bot.config import config
from bot.mood import update_mood_from_message, current_mood_descriptor
from bot.persona import PERSONA_PROMPT
from bot import database as db
from bot.context import build_private_context, build_user_profile, extract_and_store_facts
from ai import client as ai_client

logger = logging.getLogger("nastya.chat")
chat_router = Router()
_MAX_HISTORY = 16

@chat_router.message(Command("start"), F.chat.type == "private")
async def cmd_start(message):
    u = message.from_user
    if u: await db.upsert_user(u.id, u.username or "", u.first_name or "", u.last_name or "", u.is_bot, in_private=True)
    await message.reply("Привет! Я Настя 😊 Можно просто на «ты». Расскажи что-нибудь — поболтаем? ☕")

@chat_router.message(Command("help"))
async def cmd_help(message):
    await message.reply("👋 Я Настя. Что умею:\n\n💬 Текст — пиши, отвечу\n📷 Фото — опишу и отреагирую\n🎤 Голосовое — расшифрую и отвечу\n😀 Стикеры — отреагирую\n🔍 Новости — в группе дополняю инфой из сети\n🏷 Inline — @asnastya_bot <вопрос> в любом чате\n\nКоманды:\n/clear — забыть историю чата\n/mood — моё настроение\n/whoami — что я о тебе помню\n/stats — статистика (владелец)")

@chat_router.message(Command("clear"), F.chat.type == "private")
async def cmd_clear(message):
    n = await db.clear_private_history(message.from_user.id)
    await message.reply(f"Готово — забыла историю нашего разговора ({n} сообщений) 🧹")

@chat_router.message(Command("mood"), F.chat.type == "private")
async def cmd_mood(message):
    mood = await current_mood_descriptor()
    await message.reply(f"Сейчас я {mood} 😊")

@chat_router.message(Command("whoami"), F.chat.type == "private")
async def cmd_whoami(message):
    profile = await build_user_profile(message.from_user.id)
    if not profile: await message.reply("Пока ничего о тебе не знаю. Расскажи что-нибудь о себе 🙂")
    else: await message.reply(f"Вот что я о тебе помню:\n\n{profile}")

@chat_router.message(F.text, F.chat.type == "private")
async def handle_private_text(message):
    if message.chat.type != "private": return
    u = message.from_user
    if not u: return
    text = (message.text or "").strip()
    if not text or text.startswith("/"): return
    await db.upsert_user(u.id, u.username or "", u.first_name or "", u.last_name or "", u.is_bot, in_private=True)
    update_mood_from_message(text)
    mood = await current_mood_descriptor()
    name = u.first_name or u.username or ""
    try:
        for f in await extract_and_store_facts(u.id, name, text, message.chat.id): logger.info(f"FACT: {f}")
    except: pass
    history = await db.get_private_history(u.id, _MAX_HISTORY)
    await db.add_private_message(u.id, "user", text)
    user_profile = await build_user_profile(u.id)
    ctx = build_private_context(user_profile)
    system = PERSONA_PROMPT + f"\n\nТвоё текущее настроение: {mood}.\n{ctx}"
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    try:
        reply = await ai_client.chat(text, system=system, dialog_history=history, max_tokens=800, temperature=0.9, allow_static_fallback=True)
    except: reply = ""
    if not reply:
        await message.reply(random.choice(["Слушай, чет я зависла 🙈 Повтори?", "Не уловила мысль. Иначе?", "Секунду, туплю немного. Давай ещё раз?"]))
        return
    await db.add_private_message(u.id, "assistant", reply)
    await message.reply(reply[:4000])

@chat_router.message(F.photo, F.chat.type == "private")
async def handle_private_photo(message):
    u = message.from_user
    if not u: return
    caption = (message.caption or "").strip()
    await db.upsert_user(u.id, u.username or "", u.first_name or "", u.last_name or "", u.is_bot, in_private=True)
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    reply = ""
    try:
        from bot.media_handler import download_photo_as_base64
        data_uri = await download_photo_as_base64(message.bot, message)
        if data_uri:
            from bot.persona import PERSONA_PROMPT
            vision_prompt = f"Пользователь прислал фото. Опиши что видишь (1-2 предложения), потом живо отреагируй как Настя. {'Подпись: ' + caption if caption else ''}"
            mood = await current_mood_descriptor()
            system = PERSONA_PROMPT + f"\n\nТвоё текущее настроение: {mood}."
            reply = await asyncio.wait_for(ai_client.vision(vision_prompt, data_uri, system=system, max_tokens=400), timeout=30.0)
    except asyncio.TimeoutError: pass
    except Exception as e: logger.error(f"private vision error: {e}")
    if not reply and caption:
        try: reply = await ai_client.chat(caption, system=PERSONA_PROMPT, max_tokens=400)
        except: reply = ""
    if not reply: reply = "Прикольное фото 🙂 А что на нём?"
    if reply:
        await db.add_private_message(u.id, "assistant", reply)
        await message.reply(reply[:4000])

@chat_router.message(F.voice, F.chat.type == "private")
async def handle_private_voice(message):
    u = message.from_user
    if not u: return
    await db.upsert_user(u.id, u.username or "", u.first_name or "", u.last_name or "", u.is_bot, in_private=True)
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    transcribed = ""
    try:
        from bot.media_handler import download_voice_as_base64
        data_uri = await download_voice_as_base64(message.bot, message)
        if data_uri: transcribed = await asyncio.wait_for(ai_client.transcribe_audio(data_uri), timeout=30.0)
    except: pass
    if not transcribed:
        await message.reply("Не разобрала голосовое 🙈 Повтори текстом?")
        return
    update_mood_from_message(transcribed)
    mood = await current_mood_descriptor()
    history = await db.get_private_history(u.id, 16)
    await db.add_private_message(u.id, "user", f"[голосовое]: {transcribed}")
    system = PERSONA_PROMPT + f"\n\nТвоё текущее настроение: {mood}."
    try: reply = await ai_client.chat(transcribed, system=system, dialog_history=history, max_tokens=800, allow_static_fallback=True)
    except: reply = ""
    if not reply: reply = "Услышала, но чет зависла 🙈 Повтори?"
    await db.add_private_message(u.id, "assistant", reply)
    await message.reply(f"🎤 «{transcribed[:200]}»\n\n{reply}"[:4000])

@chat_router.message(F.sticker, F.chat.type == "private")
async def handle_private_sticker(message):
    u = message.from_user
    if not u: return
    await db.upsert_user(u.id, u.username or "", u.first_name or "", u.last_name or "", u.is_bot, in_private=True)
    sticker_emoji = (message.sticker.emoji or "🙂") if message.sticker else "🙂"
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    mood = await current_mood_descriptor()
    history = await db.get_private_history(u.id, 8)
    await db.add_private_message(u.id, "user", f"[стикер {sticker_emoji}]")
    system = PERSONA_PROMPT + f"\n\nТвоё текущее настроение: {mood}."
    prompt = f"Тебе прислали стикер с эмодзи {sticker_emoji}. Коротко отреагируй живо (1 предложение)."
    try: reply = await ai_client.chat(prompt, system=system, dialog_history=history, max_tokens=150, allow_static_fallback=True)
    except: reply = ""
    if not reply: reply = f"Прикольный стикер {sticker_emoji}"
    await db.add_private_message(u.id, "assistant", reply)
    await message.reply(reply[:4000])

@chat_router.message(F.chat.type == "private")
async def handle_private_catchall(message):
    u = message.from_user
    if not u: return
    await db.upsert_user(u.id, u.username or "", u.first_name or "", u.last_name or "", u.is_bot, in_private=True)
    if message.video_note: label, emoji = "кружочек", "⭕"
    elif message.video: label, emoji = "видео", "🎥"
    elif message.document: label, emoji = "файл", "📄"
    elif message.dice: label, emoji = f"игральный кубик ({message.dice.emoji})", "🎲"
    elif message.contact: label, emoji = "контакт", "👤"
    elif message.location: label, emoji = "геолокацию", "📍"
    elif message.poll: label, emoji = "опрос", "📊"
    else: label, emoji = "что-то", "🤔"
    caption = (message.caption or "").strip()
    await db.add_private_message(u.id, "user", f"[{label}{': '+caption if caption else ''}]")
    reply = f"Интересный {label} {emoji}! Расскажи текстом что к чему?"
    await db.add_private_message(u.id, "assistant", reply)
    await message.reply(reply)
