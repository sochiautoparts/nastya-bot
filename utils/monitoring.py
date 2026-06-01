"""Мониторинг и декораторы для Nastya Bot Production Cluster."""
import time
import logging
import functools

logger = logging.getLogger(__name__)


def monitor(func):
    """Декоратор для замера времени выполнения."""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        start = time.time()
        try:
            result = await func(*args, **kwargs)
            duration = time.time() - start
            logger.info(f"{func.__name__} completed in {duration:.2f}s")
            if duration > 10:
                logger.warning(f"SLOW: {func.__name__} took {duration:.2f}s")
            return result
        except Exception as e:
            duration = time.time() - start
            logger.error(f"{func.__name__} failed after {duration:.2f}s: {e}")
            raise
    return wrapper


async def check_model_health(ai_router) -> dict:
    """Проверка здоровья модели llama-cpp-python."""
    if not ai_router or not ai_router.provider:
        return {"status": "down", "error": "No provider"}
    try:
        available = ai_router.provider.is_available()
        if available:
            stats = ai_router.provider.get_stats()
            return {
                "status": "healthy",
                "model_loaded": True,
                "avg_gen_time": stats.get("avg_gen_time", 0),
                "request_count": stats.get("request_count", 0),
            }
        return {"status": "degraded", "model_loaded": False}
    except Exception as e:
        return {"status": "down", "error": str(e)}
