"""Nastya Admin Handler — admin commands for monitoring + news + channel."""
import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message
from bot.config import OWNER_ID, ADMIN_IDS, CHANNEL_ID

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

    channel_info = ""
    if CHANNEL_ID:
        channel_info = f"\n📺 Постов в канале: {stats.get('total_channel_posts', 0)}"

    text = (
        f"📊 Статистика Насти 2.0:\n\n"
        f"👥 Пользователей: {stats['total_users']}\n"
        f"⭐ Звёзд получено: {stats['total_stars']}\n"
        f"💝 Донатов: {stats['total_donations']}\n"
        f"📰 Новостей в базе: {stats.get('total_news', 0)}"
        f"{channel_info}\n\n"
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
    lines.append(f"💾 Кеш-хитов: {stats.get('cache_hits', 0)}")
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


@router.message(Command("fetchnews"))
async def cmd_fetch_news(message: Message, db=None, ai_router=None) -> None:
    """Manually trigger news fetch."""
    if message.from_user.id not in ADMIN_IDS:
        return

    if not db or not ai_router:
        return

    await message.answer("📰 Настя идёт читать новости... 🔍")

    try:
        from news import run_news_cycle
        commented = await run_news_cycle(db, ai_router)
        await message.answer(f"📰 Готово! Новых с комментариями: {commented}")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("postchannel"))
async def cmd_post_channel(message: Message, db=None, ai_router=None) -> None:
    """Manually trigger channel post."""
    if message.from_user.id not in ADMIN_IDS:
        return

    if not CHANNEL_ID:
        await message.answer("❌ CHANNEL_ID не настроен!")
        return

    if not db or not ai_router:
        return

    await message.answer("📺 Настя постит в канал... 💅")

    try:
        from channel import run_channel_cycle
        posted = await run_channel_cycle(message.bot, db, ai_router)
        await message.answer(f"📺 Готово! Постов: {posted}")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("testnews"))
async def cmd_test_news(message: Message, db=None, ai_router=None) -> None:
    """Test news commentary generation."""
    if message.from_user.id not in ADMIN_IDS:
        return

    if not ai_router:
        return

    test_title = "В Москве открыли новый торговый центр"
    await message.answer(f"🧪 Генерю реакцию на: {test_title}")

    try:
        from news import generate_nastya_comment
        comment = await generate_nastya_comment(ai_router, test_title)
        await message.answer(f"💬 Реакция Насти: {comment}")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
