"""Настя Proactive — topic starter + conversation summaries."""
import asyncio, logging, random, time
from aiogram import Bot
from bot import database as db
from bot.config import config
from bot.mood import current_mood_descriptor
from bot.context import recent_messages_to_text
from bot.persona import TOPIC_PROMPT
from bot.safe_send import safe_send
from ai import client as ai_client

logger = logging.getLogger("nastya.proactive")

SILENCE_THRESHOLD = 2 * 3600
CHECK_INTERVAL = 10 * 60
MIN_TOPIC_INTERVAL = 3 * 3600
ACTIVE_GROUP_INJECTION_PROB = 0.08
ACTIVE_MIN_INTERVAL = 45 * 60

TOPIC_STARTERS = ["народ, что думаете про", "кто-нибудь следил за", "а вы как считаете насчёт", "кстати, слышали что", "интересно ваше мнение —", "недавно думала про", "народ, а кто-нибудь", "блин, только что вспомнила —", "кстати о чём говорили,", "а что нового у всех? давно не виделись"]
GENERAL_TOPICS = ["новые гаджеты и технологии", "какие фильмы/сериалы стоит глянуть", "путешествия и куда бы хотелось поехать", "любимая еда и рецепты", "как проходит неделя", "что нового в мире авто", "интересные факты", "хобби и увлечения", "какой кофе/чай любите", "что читаете сейчас", "планы на выходные", "любимые места в городе", "новости AI и нейросетей", "спорт — кто за кем следит"]

_bot_ref = None
def set_bot(bot): global _bot_ref; _bot_ref = bot

async def _check_and_start_topic(chat_id):
    if _bot_ref is None: return
    try:
        recent = await db.get_recent_group_messages(chat_id, limit=6)
        if not recent: return
        now = time.time()
        last_msg_ts = await db.last_message_time(chat_id)
        silence = now - last_msg_ts if last_msg_ts else now
        last_bot_ts = await db.last_bot_message_time(chat_id)
        since_bot = now - last_bot_ts if last_bot_ts else 999999
        is_silent = silence >= SILENCE_THRESHOLD
        is_active_inject = not is_silent and random.random() < ACTIVE_GROUP_INJECTION_PROB and since_bot >= ACTIVE_MIN_INTERVAL
        if is_silent:
            if since_bot < MIN_TOPIC_INTERVAL: return
        elif is_active_inject: pass
        else: return
        recent_text = recent_messages_to_text(recent, limit=4)
        mood = await current_mood_descriptor()
        dialog_history = []
        for m in recent:
            who = m.get("first_name") or m.get("username") or "кто-то"
            if m.get("user_id") == config.BOT_ID: role, content = "assistant", m.get("content", "")
            else: role, content = "user", f"{who}: {m.get('content', '')}"
            if content.strip(): dialog_history.append({"role": role, "content": content})
        topic = random.choice(GENERAL_TOPICS)
        starter = random.choice(TOPIC_STARTERS)
        if is_silent: prompt = f"В группе давно тишина ({silence/3600:.0f}ч). Начни беседу — поделись мыслью/новостью/вопросом. Тема: {topic}. Оборот: «{starter}». 1-2 предложения."
        else: prompt = f"В группе активная беседа. Вступи со СВОЕЙ мыслью/вопросом. Тема: {topic}. Оборот: «{starter}». 1-2 предложения. Не повторяй других."
        extra_ctx = f"Ты в группе. Настроение: {mood}.\nНедавний контекст:\n{recent_text}\nБудь естественной."
        system = TOPIC_PROMPT + f"\n\nТвоё текущее настроение: {mood}."
        try: text = await asyncio.wait_for(ai_client.chat(prompt, system=system, extra_context=extra_ctx, dialog_history=dialog_history, max_tokens=300, temperature=0.95, allow_static_fallback=False), timeout=40.0)
        except asyncio.TimeoutError: return
        if not text: return
        text = text.strip()[:config.GROUP_MAX_CHARS]
        if not text: return
        sent = await safe_send(_bot_ref, chat_id, text, priority=False)
        if sent:
            await db.add_group_message(chat_id=chat_id, user_id=config.BOT_ID, username=config.BOT_USERNAME.lstrip("@"), first_name="Настя", content=text, is_media=False, is_bot=True)
    except Exception as e: logger.debug(f"start_topic error: {e}")

async def _summarize_chat(chat_id):
    try:
        recent = await db.get_recent_group_messages(chat_id, limit=20)
        if len(recent) < 8: return
        recent_text = recent_messages_to_text(recent, limit=15)
        prompt = f"Кратко суммаризуй что обсуждали в чате (2-3 предложения, по-русски). Выдели главные темы.\n\nСообщения:\n{recent_text[:1500]}"
        out = await asyncio.wait_for(ai_client.chat(prompt, system="Ты суммаризатор. Кратко по-русски.", fast=True, max_tokens=150, allow_static_fallback=False), timeout=20.0)
        if out and len(out) > 20: await db.add_chat_summary(chat_id, out.strip()[:500])
    except: pass

SUMMARY_INTERVAL = 30 * 60

async def summary_loop():
    logger.info("Summary loop started")
    while True:
        try:
            await asyncio.sleep(SUMMARY_INTERVAL)
            groups = await db.get_active_group_chats(within_hours=6, limit=10)
            for chat_id in groups: await _summarize_chat(chat_id); await asyncio.sleep(3)
        except asyncio.CancelledError: break
        except: await asyncio.sleep(120)

async def proactive_loop():
    logger.info("Proactive loop started")
    while True:
        try:
            await asyncio.sleep(CHECK_INTERVAL)
            groups = await db.get_active_group_chats(within_hours=24, limit=20)
            for chat_id in groups: await _check_and_start_topic(chat_id); await asyncio.sleep(2)
        except asyncio.CancelledError: break
        except: await asyncio.sleep(60)
