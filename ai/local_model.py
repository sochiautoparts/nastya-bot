"""Local LLM provider — Qwen2.5-7B-Instruct (GGUF, Q5_K_M).

Полностью автономный провайдер: без сети, без лимитов, без ключей.
При LOCAL_MODEL_PRIMARY=1 используется КАК ОСНОВНОЙ генератор постов
канала; для остального — надёжный last-resort fallback.

Модель: Qwen/Qwen2.5-7B-Instruct-GGUF (Q5_K_M, ~5.4GB, 2 шарда)
- Загрузка: ~30-45с (кэшируется в actions/cache между запусками)
- Генерация: ~8-14 tok/s на 4 vCPU (GitHub ubuntu-latest)
- Пост 450-800 символов ≈ 300-450 токенов ≈ 30-60с генерации
- RAM: ~6-7GB (влезает в ubuntu-latest 16GB вместе с Node/OpenClaw)
- Контекст: 4096 токенов

КАЧЕСТВО поверх модели достигается связкой:
1. Строгие параметры сэмплирования для постов (см. POST_SAMPLING) —
   7B на высокой температуре начинает «плыть», поэтому для постов
   temperature снижена, добавлены top_k/min_p/stop-стопы.
2. Структурный промпт + one-shot пример формата (bot/post_quality.py).
3. Детерминированный quality gate в bot/post_quality.py, который
   отсекает слабые варианты и уходит в ретрай/облако.
"""
import asyncio, logging, os, time
from typing import Optional

logger = logging.getLogger("nastya.local")

_llm = None
_init_lock = asyncio.Lock()
_init_failed = False

# ─── Статистика локальной модели (видна в /stats админки) ───────────────────
_stats = {"gens": 0, "ok": 0, "fail": 0, "total_gen_s": 0.0, "total_tokens": 0, "last_error": ""}


def stats():
    s = dict(_stats)
    s["avg_tok_per_s"] = round(s["total_tokens"] / s["total_gen_s"], 1) if s["total_gen_s"] > 0 else 0.0
    s["loaded"] = _llm is not None
    return s


# ─── Загрузка модели (lazy + lock) ───────────────────────────────────────────
async def _get_llm():
    """Load local model lazily (first call). Returns Llama instance or None."""
    global _llm, _init_failed
    if _llm is not None:
        return _llm
    if _init_failed:
        return None  # Don't retry if init failed once
    async with _init_lock:
        if _llm is not None:
            return _llm
        try:
            from llama_cpp import Llama
            # Qwen2.5-7B-Instruct Q5_K_M (sharded into 2 files)
            # llama-cpp-python auto-loads shards from the first file
            model_path = os.getenv("LOCAL_MODEL_PATH", "data/qwen2.5-7b-instruct-q5_k_m-00001-of-00002.gguf")
            if not os.path.exists(model_path):
                logger.warning(f"Local model file not found: {model_path}")
                _init_failed = True
                return None
            threads = int(os.getenv("LOCAL_MODEL_THREADS", "4"))
            cpu = os.cpu_count() or threads
            threads = max(2, min(threads, cpu))
            logger.info(f"Loading local model (7B Q5_K_M, threads={threads}): {model_path}")
            t0 = time.time()
            _llm = Llama(
                model_path=model_path,
                n_ctx=4096,
                n_threads=threads,
                n_batch=256,      # быстрый prompt-processing на CPU
                n_gpu_layers=0,
                verbose=False,
            )
            logger.info(f"Local model (7B) loaded in {time.time()-t0:.1f}s")
            return _llm
        except ImportError:
            logger.warning("llama-cpp-python not installed — local model unavailable")
            _init_failed = True
            return None
        except Exception as e:
            logger.warning(f"Local model load failed: {e}")
            _init_failed = True
            return None


async def warmup():
    """Preload model in background at bot startup.

    Не блокирует старт: первый пост не будет ждать 30-45с на загрузку,
    а fallback-путь готов с первой минуты жизни процесса.
    """
    try:
        llm = await _get_llm()
        if llm is not None:
            logger.info("Local model warm-up: ready (7B in memory)")
        else:
            logger.info("Local model warm-up: unavailable (file missing / no llama-cpp)")
    except Exception as e:
        logger.debug(f"Local model warm-up skipped: {e}")


# ─── Параметры сэмплирования ────────────────────────────────────────────────
# Для постов — строже: 7B на высокой температуре придумывает факты и
# ломает структуру. Для чатов — живее.

CHAT_SAMPLING = dict(temperature=0.8, top_p=0.9, top_k=50, min_p=0.0, repeat_penalty=1.1)
POST_SAMPLING = dict(temperature=0.7, top_p=0.85, top_k=40, min_p=0.05, repeat_penalty=1.15)

# Стопы для постов: не даём модели «дописывать» за пределы формата
POST_STOPS = ["\nЗАДАНИЕ", "\nНовость", "\nПРИМЕР", "\n=====", "\n---", "ЗАГОЛОВОК:\n"]

MAX_TOKEN_CAP = {"chat": 500, "post": 750}


def _truncate_messages(messages, max_input_chars=12000):
    """Truncate to fit 4096-token context (reserve ~512 for generation)."""
    truncated = []
    total_chars = 0
    for msg in messages:
        content = msg.get("content", "")
        if total_chars + len(content) > max_input_chars:
            remaining = max_input_chars - total_chars
            if remaining > 100:
                content = content[:remaining] + "..."
                truncated.append({**msg, "content": content})
            break
        truncated.append(msg)
        total_chars += len(content)
    return truncated


async def call_local(messages, max_tokens=400, temperature=0.8, mode="chat"):
    """Call local Qwen2.5-7B model.

    Args:
        messages: List of {"role": "user"/"system"/"assistant", "content": "..."}
        max_tokens: Max tokens to generate (cap depends on mode)
        temperature: 0.0-1.0 (override; None → use mode default)
        mode: "post" (structured channel posts — strict sampling + stops)
              | "chat" (conversations — livelier sampling)

    Returns:
        Generated text or empty string on failure.
    """
    llm = await _get_llm()
    if llm is None:
        return ""

    sampling = dict(POST_SAMPLING if mode == "post" else CHAT_SAMPLING)
    if temperature is not None:
        sampling["temperature"] = max(0.1, min(float(temperature), 1.0))

    try:
        loop = asyncio.get_event_loop()
        truncated = _truncate_messages(messages)

        def _generate():
            return llm.create_chat_completion(
                messages=truncated,
                max_tokens=min(max_tokens, MAX_TOKEN_CAP.get(mode, 500)),
                top_p=sampling["top_p"],
                top_k=sampling["top_k"],
                min_p=sampling["min_p"] or 0.01,
                repeat_penalty=sampling["repeat_penalty"],
                stop=POST_STOPS if mode == "post" else None,
            )

        t0 = time.time()
        response = await loop.run_in_executor(None, _generate)
        gen_s = time.time() - t0
        content = (response["choices"][0]["message"]["content"] or "").strip()
        n_tokens = (response.get("usage") or {}).get("completion_tokens", 0) or 0

        _stats["gens"] += 1
        _stats["total_gen_s"] += gen_s
        _stats["total_tokens"] += n_tokens
        if content and len(content) > 10:
            _stats["ok"] += 1
            tps = n_tokens / gen_s if gen_s > 0 else 0
            logger.info(f"Local 7B ({mode}): {n_tokens} tok in {gen_s:.1f}s ({tps:.1f} tok/s), {len(content)} chars")
            return content
        _stats["fail"] += 1
        return ""
    except Exception as e:
        _stats["gens"] += 1
        _stats["fail"] += 1
        _stats["last_error"] = f"{type(e).__name__}: {e}"
        logger.warning(f"Local model generation error: {e}")
        return ""


def is_available():
    """Check if local model is loaded and ready."""
    return _llm is not None
