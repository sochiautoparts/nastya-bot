"""Nastya Admin Handler 🎀"""
import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message
from bot.config import OWNER_ID, ADMIN_IDS

logger = logging.getLogger(__name__)
router = Router()


@router.message(Command("stats"))
async def cmd_stats(message: Message, db=None, ai_router=None) -> None:
    """Bot statistics."""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Настя не обязана отчитываться! 😤")
        return

    if not db:
        return

    stats = await db.get_stats()
    text = (
        f"📊 Статистика Насти:\n\n"
        f"👥 Пользователей: {stats['total_users']}\n"
        f"⭐ Звёзд получено: {stats['total_stars']}\n"
        f"💝 Донатов: {stats['total_donations']}\n\n"
        f"🎀 Настя работает!"
    )
    await message.answer(text)


@router.message(Command("providers"))
async def cmd_providers(message: Message, db=None, ai_router=None) -> None:
    """Show available AI providers."""
    if message.from_user.id not in ADMIN_IDS:
        return

    if not ai_router:
        return

    providers = list(ai_router.providers.keys())
    chain = ai_router._chain
    text = (
        f"🤖 AI провайдеры Насти:\n\n"
        f"Доступные: {', '.join(providers)}\n"
        f"Цепочка: {' → '.join(chain)}"
    )
    await message.answer(text)
