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
    """Проверка здоровья AI провайдеров - Pollinations + Local."""
    if not ai_router:
        return {"status": "down", "error": "No ai_router"}

    results = {}
    overall = "healthy"

    # Check Pollinations (PRIMARY)
    if ai_router._pollinations:
        try:
            available = ai_router._pollinations.is_available()
            if available:
                results["pollinations"] = {
                    "status": "healthy",
                    "available": True,
                }
                try:
                    stats = ai_router._pollinations.get_model_stats()
                    results["pollinations"]["model_stats"] = stats
                except Exception:
                    pass
            else:
                results["pollinations"] = {"status": "degraded", "available": False}
                overall = "degraded"
        except Exception as e:
            results["pollinations"] = {"status": "down", "error": str(e)}
            overall = "degraded"
    else:
        results["pollinations"] = {"status": "down", "error": "Not initialized"}

    # Check Local model (FALLBACK)
    if ai_router._local:
        try:
            available = ai_router._local.is_available()
            if available:
                results["local"] = {
                    "status": "healthy",
                    "available": True,
                }
                try:
                    stats = ai_router._local.get_stats()
                    results["local"]["stats"] = stats
                except Exception:
                    pass
            else:
                results["local"] = {"status": "degraded", "available": False}
        except Exception as e:
            results["local"] = {"status": "down", "error": str(e)}
    else:
        results["local"] = {"status": "disabled", "available": False}

    results["overall"] = overall
    return results
