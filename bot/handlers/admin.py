"""Nastya Admin Handler — admin commands for monitoring."""
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
    """Show available AI providers and their status."""
    if message.from_user.id not in ADMIN_IDS:
        return

    if not ai_router:
        return

    status = ai_router.get_status()
    lines = ["🤖 AI провайдеры Насти:\n"]

    for name, info in status.items():
        if name == "_stats":
            continue
        available = "✅" if info["available"] else "❌"
        healthy = "💚" if info["healthy"] else "💔"
        vision = " 👁" if info.get("vision") else ""
        fails = info["fail_count"]
        fail_str = f" (fails: {fails})" if fails > 0 else ""
        lines.append(f"  {available}{healthy} {name}{vision}{fail_str}")

    stats = status.get("_stats", {})
    lines.append(f"\n📊 Запросов: {stats.get('total_requests', 0)}")
    lines.append(f"🔄 Фоллбэков: {stats.get('total_fallbacks', 0)}")
    lines.append(f"✨ Последний рабочий: {stats.get('last_good_provider', 'нет')}")

    await message.answer("\n".join(lines))


@router.message(Command("reset"))
async def cmd_reset(message: Message, db=None, ai_router=None) -> None:
    """Reset circuit breaker for all providers."""
    if message.from_user.id not in ADMIN_IDS:
        return

    if not ai_router:
        return

    ai_router._fail_counts.clear()
    ai_router._last_fail.clear()
    await message.answer("🔄 Circuit breaker сброшен! Все провайдеры снова доступны.")
