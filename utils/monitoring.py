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


async def check_cluster_health(base_url: str = "http://localhost:11434") -> dict:
    """Проверка здоровья кластера Ollama."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            async with client.get(f"{base_url}/api/tags") as resp:
                if resp.status_code == 200:
                    return {"status": "healthy", "models": resp.json().get("models", [])}
                return {"status": "degraded", "code": resp.status_code}
    except Exception as e:
        return {"status": "down", "error": str(e)}
