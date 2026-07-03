"""Настя Group handler — active participation + web search + partners + site content."""
import asyncio, hashlib, logging, random, re, time
from typing import List
from aiogram import Router, F
from aiogram.types import Message
from aiogram.enums import ChatAction
from bot.config import config
from bot import database as db
from bot.context import (user_descriptor, chat_descriptor, is_directed_at_bot, strip_mention, recent_messages_to_text, build_group_context, build_user_profile, extract_and_store_facts)
from bot.mood import update_mood_from_message, current_mood_descriptor
from bot.reactions import maybe_react
from bot.safe_send import safe_reply, safe_send
from bot.web_search import verify_claim, research_topic, first_url, all_urls
from bot.media_handler import extract_caption
from bot.partners import partner_manager
from bot.persona import COMMENT_PROMPT, EVENT_PROMPT, DIRECT_PROMPT, TOPIC_PROMPT
from ai import client as ai_client

logger = logging.getLogger("nastya.groups")
group_router = Router()

_VERIFY_HINTS = ["новост", "правда ли", "что случилось", "говорят что", "по данным", "сегодня", "вчера", "слышал", "прочитал", "источник", "статья", "появился", "вышла", "анонс", "запустили", "анонсировал", "выпустил", "сколько стоит", "цена", "когда выйдет", "оказывается", "прошёл", "прошла", "состоялся", "открыли", "закрыли", "обновил", "обновление", "патч", "версия", "релиз", "тренд", "вирусный", "популярн", "обсуждают", "хайп"]
_EVENT_HINTS = ["новост", "событие", "случил", "произош", "прошёл", "прошла", "состоялся", "открыли", "закрыли", "запустили", "анонс", "вышла", "выпустил", "обновлен", "релиз", "появился", "анонсировал", "сегодня", "вчера", "только что", "прямо сейчас", "факт", "прикинь", "ого", "самый", "крупнейший", "первый в", "единственный", "открыла для себя", "узнала", "оказывает", "опрос дня", "опрос:", "как вы считаете", "что думаете"]

def _needs_verification(text):
    t = (text or "").lower()
    return len(t) >= 15 and any(h in t for h in _VERIFY_HINTS)

def _is_event_or_news(text):
    t = (text or "").lower()
    return len(t) >= 10 and any(h in t for h in _EVENT_HINTS)

def _is_politics_or_war(text):
    t = (text or "").lower()
    return any(w in t for w in ["путин", "кремль", "госдума", "санкци", "сво", "мобилиз", "война", "зеленск", "байден", "трамп", "выборы", "парламент", "оранжев", "наци", "террор", "обеднен", "обстрел"])

_recent_content_hashes: dict = {}
_DEDUP_TTL = 300

def _content_hash(text):
    clean = re.sub(r"^\[[^\]]+\]\s*", "", text or "")
    return hashlib.md5(clean[:100].strip().lower().encode()).hexdigest()

def _should_skip_duplicate(text, chat_id):
    now = time.time()
    global _recent_content_hashes
    _recent_content_hashes = {k: v for k, v in _recent_content_hashes.items() if now - v[1] < _DEDUP_TTL}
    h = _content_hash(text)
    if h in _recent_content_hashes:
        first_chat, _ = _recent_content_hashes[h]
        if first_chat != chat_id: return True
        return False
    _recent_content_hashes[h] = (chat_id, now)
    return False

_reply_chain_tracker: dict = {}
_MAX_BOT_REPLIES_PER_THREAD = 3
_THREAD_TTL = 1800

def _is_in_bot_loop(message):
    if not message.reply_to_message: return False
    chat_id = message.chat.id
    thread_key = message.reply_to_message.message_id
    now = time.time()
    tracker = _reply_chain_tracker.get(chat_id, {})
    tracker = {k: v for k, v in tracker.items() if now - v[1] < _THREAD_TTL}
    count, _ = tracker.get(thread_key, (0, now))
    return count >= _MAX_BOT_REPLIES_PER_THREAD

def _track_bot_reply(message):
    if not message.reply_to_message: return
    chat_id = message.chat.id
    thread_key = message.reply_to_message.message_id
    now = time.time()
    tracker = _reply_chain_tracker.setdefault(chat_id, {})
    count, _ = tracker.get(thread_key, (0, now))
    tracker[thread_key] = (count + 1, now)

async def _log_group_message(message, content="", is_media=False, media_caption="", is_bot=False):
    u = message.from_user
    if not is_bot and u and (u.id == config.BOT_ID or u.is_bot): is_bot = True
    await db.add_group_message(message.chat.id, u.id if u else 0, (u.username or "") if u else "", (u.first_name or "") if u else "", content or (message.text or ""), is_media, media_caption, is_bot)

async def _should_respond(message):
    u = message.from_user
    if u and u.id == config.BOT_ID: return False
    directed = is_directed_at_bot(message)
    if directed:
        if _is_in_bot_loop(message): return False
        return True
    is_channel_forward = (u and u.id == 777000) or (message.sender_chat and message.sender_chat.type == "channel") or (message.forward_from_chat is not None)
    if is_channel_forward:
        if _should_skip_duplicate(message.text or "", message.chat.id): return False
        return True  # comment on ALL news/events
    if message.reply_to_message and message.reply_to_message.from_user:
        if message.reply_to_message.from_user.id != config.BOT_ID:
            text = (message.text or "").lower()
            if _is_event_or_news(text): return True
            return random.random() < 0.50
    if u and u.is_bot:
        if _is_in_bot_loop(message): return False
        return random.random() < 0.40
    text = (message.text or "").lower()
    if _is_event_or_news(text): return True
    return random.random() < config.GROUP_PROACTIVE_PROB

async def _generate_group_response(message, text, directed):
    recent = await db.get_recent_group_messages(message.chat.id, limit=12)
    recent_text = recent_messages_to_text(recent, limit=8)
    memory_facts_rows = await db.get_group_memory(message.chat.id, limit=8)
    memory_facts = [r["fact"] for r in memory_facts_rows]
    author_profile = ""
    u = message.from_user
    if u and not u.is_bot and u.id != config.BOT_ID:
        try:
            await db.upsert_user(u.id, u.username or "", u.first_name or "", u.last_name or "", u.is_bot, in_group=True)
            author_profile = await build_user_profile(u.id)
        except: pass
    mood = await current_mood_descriptor()
    try: summaries = await db.get_chat_summaries(message.chat.id, limit=2)
    except: summaries = []
    extra_ctx = build_group_context(message, recent_text, memory_facts, author_profile, summaries)

    # Partner links
    try:
        await partner_manager.refresh_if_needed()
        links = partner_manager.get_all_partner_links_for_dialog(text, max_programs=2)
        if links:
            extra_ctx += "\n\nПартнёрские ссылки (вставь ОДНУ если к месту, естественно, не в каждом ответе):\n"
            for pl in links: extra_ctx += f"- {pl['name']} ({pl.get('label','')}): {pl['url']}\n"
    except: pass

    extra_ctx += "\n\nМожешь иногда (1 из 6 сообщений, если к месту) посоветовать каналы @sochiautoparts (авто-новости) или @bmw_mpower_club (BMW клуб), или магазин sochiautoparts.ru/shop."

    # Site content (products/posts)
    try:
        t_lower = (text or "").lower()
        is_shopping = any(k in t_lower for k in ["купить", "магазин", "цена", "стоим", "заказ", "товар", "запчаст", "детал", "артикул", "подобрать", "найти", "выбор", "рекоменд"])
        product_prob = 0.5 if is_shopping else 0.25
        post_prob = 0.25 if is_shopping else 0.12
        if random.random() < product_prob:
            from bot import site_content as sc
            prod = await sc.relevant_product(text) if text else await sc.random_product()
            if prod: extra_ctx += "\n\nТовар из магазина sochiautoparts.ru/shop (упомяни если к месту):\n" + sc.format_product_for_context(prod)
        if random.random() < post_prob:
            from bot import site_content as sc
            post = await sc.random_post()
            if post: extra_ctx += "\n\nСвежий пост на сайте (можешь поделиться): " + sc.format_post_for_context(post)
    except: pass

    # Web search
    is_event = _is_event_or_news(text)
    needs_verify = _needs_verification(text)
    web_context = ""
    web_urls = []
    if is_event:
        try:
            web_context = await asyncio.wait_for(research_topic(text[:400], max_queries=2), timeout=12.0)
            if web_context:
                extra_ctx += "\n\n" + web_context
                web_urls = all_urls(web_context)
        except: pass
    elif needs_verify and random.random() < config.WEB_VERIFY_PROB:
        try:
            web_context = await asyncio.wait_for(verify_claim(text[:400]), timeout=6.0)
            if web_context:
                extra_ctx += "\n\nРезультаты веб-поиска (используй для дополнения ответа, упомяни источник если уместно):\n" + web_context
                web_urls = [first_url(web_context)]
        except: pass

    dialog_history = []
    for m in recent:
        who = m.get("first_name") or m.get("username") or "кто-то"
        if m.get("user_id") == config.BOT_ID: role, content = "assistant", m.get("content", "")
        else:
            role, content = "user", f"{who}: {m.get('content', '')}"
            if m.get("is_media"): content = f"{who}: [фото{': ' + m.get('media_caption','') if m.get('media_caption') else ''}]"
        if content.strip(): dialog_history.append({"role": role, "content": content})

    if is_event: system = EVENT_PROMPT
    elif directed: system = DIRECT_PROMPT
    else: system = COMMENT_PROMPT
    if mood: system += f"\n\nТвоё текущее настроение: {mood}."

    prompt = strip_mention(text) if directed else text
    if not prompt: prompt = "(сообщение без текста — прокомментируй контекст чата, вступи в беседу)"

    max_tokens = 700 if is_event else 450
    fallback = directed
    use_fast = not directed
    try:
        out = await asyncio.wait_for(ai_client.chat(prompt, system=system, extra_context=extra_ctx, dialog_history=dialog_history, max_tokens=max_tokens, temperature=0.95, allow_static_fallback=fallback, fast=use_fast), timeout=40.0)
    except asyncio.TimeoutError: return ""
    out = (out or "").strip()
    if not out: return ""
    limit = (config.GROUP_MAX_CHARS + 300) if is_event else (config.GROUP_MAX_CHARS if directed else config.COMMENT_MAX_CHARS)
    out = out[:limit]
    if web_urls:
        missing = [u for u in web_urls[:2] if u not in out]
        if missing: out += "\n\nИсточник: " + " · ".join(missing)
    return out

@group_router.message(F.new_chat_members)
async def handle_new_members(message):
    if message.chat.type not in ("group", "supergroup"): return
    newcomers = message.new_chat_members or []
    if not any(m and m.id == config.BOT_ID for m in newcomers): return
    try:
        await message.reply("Настя на связи 😊 Буду активно участвовать в беседе, ставить реакции и дополнять новости из интернета. Кидайте темы! ☕")
        logger.info(f"BOT ADDED to chat {message.chat.id} ({message.chat.title})")
    except: pass

@group_router.message(F.photo)
async def handle_group_photo(message):
    if message.chat.type not in ("group", "supergroup"): return
    if message.from_user is None: return
    u = message.from_user
    if u.id == config.BOT_ID: return
    caption = extract_caption(message)
    await _log_group_message(message, content=caption, is_media=True, media_caption=caption)
    update_mood_from_message(caption)
    if _is_politics_or_war(caption): return
    directed = is_directed_at_bot(message)
    if message.media_group_id:
        if not directed:
            if caption and random.random() < 0.15: asyncio.create_task(maybe_react(message.bot, message.chat.id, message.message_id, caption, prob=1.0))
            return
    if not directed:
        if caption and random.random() < 0.25: asyncio.create_task(maybe_react(message.bot, message.chat.id, message.message_id, caption, prob=1.0))
        elif not caption and random.random() < 0.10: asyncio.create_task(maybe_react(message.bot, message.chat.id, message.message_id, "", prob=1.0))
        return
    photo_prompt = caption or "(тебе прислали фото — коротко отреагируй живо)"
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    out = ""
    try:
        from bot.media_handler import download_photo_as_base64
        data_uri = await download_photo_as_base64(message.bot, message)
        if data_uri:
            vision_prompt = f"Тебе прислали фото в группе. Опиши кратко что видишь (1-2 предложения), потом живо отреагируй как Настя. {'Подпись к фото: ' + caption if caption else 'Без подписи.'}"
            out = await asyncio.wait_for(ai_client.vision(vision_prompt, data_uri, system=DIRECT_PROMPT, max_tokens=300), timeout=30.0)
    except: pass
    if not out:
        try: out = await _generate_group_response(message, photo_prompt, directed)
        except: return
    if not out: return
    await safe_reply(message.bot, message, out, always_reply=True, priority=directed)
    await _log_group_message(message, content=out, is_media=False, is_bot=True)
    _track_bot_reply(message)

@group_router.message(F.voice)
async def handle_group_voice(message):
    if message.chat.type not in ("group", "supergroup"): return
    if message.from_user is None: return
    u = message.from_user
    if u.id == config.BOT_ID: return
    directed = is_directed_at_bot(message)
    await _log_group_message(message, content="[голосовое]", is_media=True, media_caption="")
    if not directed:
        asyncio.create_task(maybe_react(message.bot, message.chat.id, message.message_id, "", prob=0.3, force=True))
        return
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    transcribed = ""
    try:
        from bot.media_handler import download_voice_as_base64
        data_uri = await download_voice_as_base64(message.bot, message)
        if data_uri: transcribed = await asyncio.wait_for(ai_client.transcribe_audio(data_uri), timeout=30.0)
    except: pass
    if not transcribed:
        await safe_reply(message.bot, message, "Не разобрала голосовое 🙈 Повтори текстом?", always_reply=True, priority=True)
        return
    await _log_group_message(message, content=transcribed, is_media=False, is_bot=False)
    try: out = await _generate_group_response(message, transcribed, directed)
    except: return
    if not out: out = "Услышала, но чет зависла 🙈"
    await safe_reply(message.bot, message, out, always_reply=True, priority=directed)
    await _log_group_message(message, content=out, is_media=False, is_bot=True)
    _track_bot_reply(message)

@group_router.message(F.sticker)
async def handle_group_sticker(message):
    if message.chat.type not in ("group", "supergroup"): return
    if message.from_user is None: return
    u = message.from_user
    if u.id == config.BOT_ID: return
    directed = is_directed_at_bot(message)
    sticker_emoji = (message.sticker.emoji or "🙂") if message.sticker else "🙂"
    await _log_group_message(message, content=f"[стикер {sticker_emoji}]", is_media=True, media_caption=sticker_emoji)
    asyncio.create_task(maybe_react(message.bot, message.chat.id, message.message_id, sticker_emoji, prob=0.7, force=True))
    if directed:
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
        prompt = f"Тебе прислали стикер с эмодзи {sticker_emoji}. Коротко отреагируй живо (1 предложение)."
        try: out = await _generate_group_response(message, prompt, directed)
        except: return
        if out:
            await safe_reply(message.bot, message, out, always_reply=True, priority=directed)
            await _log_group_message(message, content=out, is_media=False, is_bot=True)
            _track_bot_reply(message)

@group_router.message(F.animation)
async def handle_group_animation(message):
    if message.chat.type not in ("group", "supergroup"): return
    if message.from_user is None: return
    u = message.from_user
    if u.id == config.BOT_ID: return
    directed = is_directed_at_bot(message)
    caption = extract_caption(message)
    await _log_group_message(message, content=f"[гифка{': '+caption if caption else ''}]", is_media=True, media_caption=caption)
    asyncio.create_task(maybe_react(message.bot, message.chat.id, message.message_id, caption or "", prob=0.5, force=True))
    if directed:
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
        prompt = caption or "(тебе прислали гифку — коротко отреагируй живо)"
        try: out = await _generate_group_response(message, prompt, directed)
        except: return
        if out:
            await safe_reply(message.bot, message, out, always_reply=True, priority=directed)
            await _log_group_message(message, content=out, is_media=False, is_bot=True)
            _track_bot_reply(message)

@group_router.message(F.text)
async def handle_group_text(message):
    if message.chat.type not in ("group", "supergroup"): return
    if message.from_user is None: return
    u = message.from_user
    if u.id == config.BOT_ID: return
    text = (message.text or "").strip()
    if not text: return
    directed_early = is_directed_at_bot(message)
    await _log_group_message(message, content=text, is_media=False, is_bot=False)
    update_mood_from_message(text)
    asyncio.create_task(maybe_react(message.bot, message.chat.id, message.message_id, text))
    if text.startswith("/") and not directed_early: return
    if _is_politics_or_war(text) and not directed_early: return
    if not await _should_respond(message): return
    directed = directed_early
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    try: out = await _generate_group_response(message, text, directed)
    except: return
    if not out:
        asyncio.create_task(maybe_react(message.bot, message.chat.id, message.message_id, text, prob=1.0, force=True))
        return
    await safe_reply(message.bot, message, out, always_reply=True, priority=directed)
    await _log_group_message(message, content=out, is_media=False, is_bot=True)
    _track_bot_reply(message)
    try: await _extract_and_store_memory(message, text)
    except: pass

@group_router.message(F.video | F.video_note | F.document)
async def handle_group_other_media(message):
    if message.chat.type not in ("group", "supergroup"): return
    if message.from_user is None: return
    u = message.from_user
    if u.id == config.BOT_ID: return
    directed = is_directed_at_bot(message)
    caption = extract_caption(message)
    media_label = "[кружочек]" if message.video_note else "[видео]" if message.video else "[документ]"
    await _log_group_message(message, content=f"{media_label}{': '+caption if caption else ''}", is_media=True, media_caption=caption)
    asyncio.create_task(maybe_react(message.bot, message.chat.id, message.message_id, caption or "", prob=0.4, force=True))
    if directed:
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
        if message.video_note: prompt = caption or "(тебе прислали кружочек — коротко отреагируй живо)"
        elif message.video: prompt = caption or "(тебе прислали видео — коротко отреагируй живо)"
        else: prompt = caption or "(тебе прислали файл — коротко отреагируй живо)"
        try: out = await _generate_group_response(message, prompt, directed)
        except: return
        if out:
            await safe_reply(message.bot, message, out, always_reply=True, priority=directed)
            await _log_group_message(message, content=out, is_media=False, is_bot=True)
            _track_bot_reply(message)

@group_router.message()
async def handle_group_catchall(message):
    if message.chat.type not in ("group", "supergroup"): return
    if message.from_user is None: return
    u = message.from_user
    if u.id == config.BOT_ID: return
    dice_emoji = ""
    if message.dice: dice_emoji = message.dice.emoji or "🎲"
    asyncio.create_task(maybe_react(message.bot, message.chat.id, message.message_id, dice_emoji, prob=0.5, force=True))

async def _extract_and_store_memory(message, text):
    if not text or not message.from_user: return
    u = message.from_user
    if u.id == 777000 or u.is_bot or u.id == config.BOT_ID: return
    if (message.sender_chat and message.sender_chat.type == "channel") or message.forward_from_chat is not None: return
    name = u.first_name or u.username or ""
    chat_id = message.chat.id
    new_facts = await extract_and_store_facts(u.id, name, text, source_chat=chat_id)
    for fact in new_facts:
        await db.add_group_memory(chat_id, u.id, fact)
        logger.info(f"MEMORY: {fact}")
