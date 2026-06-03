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
    lines = ["🤖 AI Провайдеры Насти:\n"]

    for name, info in status.items():
        if name == "_stats":
            continue
        if isinstance(info, dict):
            available = "✅" if info.get("available") or info.get("model_loaded") else "❌"
            model_path = info.get('model_path', '?')
            model_name = model_path.split('/')[-1] if model_path != '?' else '?'
            lines.append(f"  {available} {name}")
            if name == "llama_cpp":
                lines.append(f"    Model: {model_name}")
                lines.append(f"    Loaded: {info.get('model_loaded', False)} | n_ctx: {info.get('n_ctx', 0)} | n_threads: {info.get('n_threads', 0)}")
                lines.append(f"    Requests: {info.get('request_count', 0)} | Avg time: {info.get('avg_gen_time', 0):.1f}s | Errors: {info.get('error_count', 0)}")
            elif name == "pollinations":
                on_cooldown = "🔒" if info.get('on_cooldown') else "🔓"
                lines.append(f"    Available: {available} {on_cooldown}")

    stats = status.get("_stats", {})
    lines.append(f"\n📊 Запросов: {stats.get('total_requests', 0)}")
    lines.append(f"🔄 Фоллбэков: {stats.get('total_fallbacks', 0)}")
    lines.append(f"💾 Кеш-хитов: {stats.get('cache_hits', 0)}")

    await message.answer("\n".join(lines))


@router.message(Command("reset"))
async def cmd_reset(message: Message, db=None, ai_router=None) -> None:
    """Reset circuit breaker for all providers."""
    if message.from_user.id not in ADMIN_IDS:
        return

    if not ai_router:
        return

    # Перезагрузка провайдеров
    reloaded = []
    if ai_router._pollinations:
        try:
            await ai_router._pollinations.close()
            await ai_router._pollinations.init()
            reloaded.append("Pollinations")
        except Exception as e:
            logger.error(f"Pollinations reload error: {e}")
    if ai_router._local:
        try:
            await ai_router._local.close()
            await ai_router._local.init()
            reloaded.append("Local")
        except Exception as e:
            logger.error(f"Local model reload error: {e}")
    if reloaded:
        await message.answer(f"🔄 Провайдеры перезагружены: {', '.join(reloaded)}!")
    else:
        await message.answer("❌ Провайдеры не найдены!")


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
        commented = await run_news_cycle(db)  # v35: ai_router not needed, templates only!
        await message.answer(f"📰 Готово! Новых с комментариями: {commented}")
    except Exception as e:
        logger.error(f"Fetch news error: {e}")
        await message.answer("❌ Настя не смогла загрузить новости... Попробуй позже!")


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
        logger.error(f"Channel post error: {e}")
        await message.answer("❌ Настя не смогла запостить... Попробуй позже!")


@router.message(Command("testnews"))
async def cmd_test_news(message: Message, db=None, ai_router=None) -> None:
    """Test news commentary generation — template-based, no AI!"""
    if message.from_user.id not in ADMIN_IDS:
        return

    test_title = "В Москве открыли новый торговый центр"
    test_category = "general"
    await message.answer(f"🧪 Генерю реакцию на: {test_title}")

    try:
        from news import generate_template_commentary
        comment = generate_template_commentary(test_title, test_category)
        await message.answer(f"💬 Реакция Насти (template): {comment}")
    except Exception as e:
        logger.error(f"Test news error: {e}")
        await message.answer("❌ Настя пока не может реагировать на новости...")
