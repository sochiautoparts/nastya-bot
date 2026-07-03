"""Настя Inline handler — @asnastya_bot <question> in ANY chat."""
import asyncio, logging
from aiogram import Router, F
from aiogram.types import InlineQuery, InlineQueryResultArticle, InputTextMessageContent
from aiogram.exceptions import TelegramRetryAfter
from bot.persona import PERSONA_PROMPT
from ai import client as ai_client

logger = logging.getLogger("nastya.inline")
inline_router = Router()

@inline_router.inline_query()
async def handle_inline_query(inline_query):
    query = (inline_query.query or "").strip()
    if len(query) < 2:
        results = [InlineQueryResultArticle(id="hint", title="Настя — задай вопрос", description="Напиши вопрос после @asnastya_bot — отвечу сразу", input_message_content=InputTextMessageContent(message_text="Напиши вопрос после @asnastya_bot 🙂"))]
        try: await inline_query.answer(results, cache_time=10)
        except: pass
        return
    try:
        answer = await asyncio.wait_for(ai_client.chat(query, system=PERSONA_PROMPT, fast=True, max_tokens=400, temperature=0.9, allow_static_fallback=True), timeout=20.0)
    except asyncio.TimeoutError: answer = "Настя задумалась надолго 🙈 Попробуй ещё раз."
    except: answer = "Что-то пошло не так. Попробуй переформулировать."
    if not answer: answer = "Не уловила мысль. Давай иначе?"
    answer = answer[:3900]
    results = [InlineQueryResultArticle(id="lyuba_answer", title=f"Настя: {query[:60]}", description=answer[:100] + ("..." if len(answer) > 100 else ""), input_message_content=InputTextMessageContent(message_text=f"❓ {query}\n\nНастя: {answer}"), thumbnail_url="https://emojiapi.dev/api/v1/girl.png")]
    try: await inline_query.answer(results, cache_time=5, is_personal=False)
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after)
        try: await inline_query.answer(results, cache_time=5, is_personal=False)
        except: pass
    except: pass
