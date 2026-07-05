"""Ася AI Client — routes all AI through OpenClaw Gateway + Pollinations direct."""
import asyncio, logging, os, random, time
from typing import List, Optional
import httpx
from bot.config import config

logger = logging.getLogger("nastya.ai")

_ENDPOINT = f"{config.OPENCLAW_URL}/v1/chat/completions"
_MODEL = "openclaw"
_POLLINATIONS_URL = "https://text.pollinations.ai/openai/chat/completions"
_POLLINATIONS_MODEL = "openai"

# Load up to 3 Pollinations API keys from environment (for rotation)
_POLLINATIONS_KEYS = []
for _i in range(1, 4):
    _k = os.getenv(f"POLLINATIONS_API_KEY_{_i}", "")
    if _k: _POLLINATIONS_KEYS.append(_k)
if not _POLLINATIONS_KEYS:
    # Fallback to single key
    _k = os.getenv("POLLINATIONS_API_KEY", "")
    if _k: _POLLINATIONS_KEYS.append(_k)
_POLLINATIONS_KEY_IDX = 0  # round-robin index

# Cloudflare Workers AI (Tier-2 fallback — more reliable, no rate limits)
_CF_MODEL = "@cf/meta/llama-4-scout-17b-16e-instruct"
_CF_ACCOUNTS = []
for _i in range(1, 3):
    _aid = os.getenv(f"CF_ACCOUNT_ID_{_i}", "")
    _tok = os.getenv(f"CF_API_TOKEN_{_i}", "")
    if _aid and _tok:
        _CF_ACCOUNTS.append((_aid, _tok))
_CF_ACCOUNT_IDX = 0

def _get_cf_account():
    """Get next Cloudflare account (round-robin). Returns (account_id, token) or None."""
    global _CF_ACCOUNT_IDX
    if not _CF_ACCOUNTS:
        return None
    acct = _CF_ACCOUNTS[_CF_ACCOUNT_IDX % len(_CF_ACCOUNTS)]
    _CF_ACCOUNT_IDX += 1
    return acct

def _strip_pollinations_ads(text):
    """Strip Pollinations ad suffix from response."""
    if not text:
        return text
    # Remove ad suffix
    for marker in ["---\n\n**Support Pollinations", "**Support Pollinations", "🌸 **Ad** 🌸"]:
        idx = text.find(marker)
        if idx > 0:
            text = text[:idx].rstrip()
    return text

def _get_pollinations_key():
    """Get next API key (round-robin). Returns empty string if none."""
    global _POLLINATIONS_KEY_IDX
    if not _POLLINATIONS_KEYS:
        return ""
    key = _POLLINATIONS_KEYS[_POLLINATIONS_KEY_IDX % len(_POLLINATIONS_KEYS)]
    _POLLINATIONS_KEY_IDX += 1
    return key

def _pollinations_headers():
    """Build Authorization header if API key available."""
    key = _get_pollinations_key()
    if key:
        return {"Authorization": f"Bearer {key}"}
    return {}

_client: Optional[httpx.AsyncClient] = None
_pollinations_sem: asyncio.Semaphore | None = None
_openclaw_sem: asyncio.Semaphore | None = None

def _get_pollinations_sem():
    global _pollinations_sem
    if _pollinations_sem is None: _pollinations_sem = asyncio.Semaphore(2)
    return _pollinations_sem

def _get_openclaw_sem():
    global _openclaw_sem
    if _openclaw_sem is None: _openclaw_sem = asyncio.Semaphore(5)
    return _openclaw_sem

_stats = {"requests": 0, "success": 0, "fail": 0, "openclaw_ok": 0, "pollinations_backup": 0, "static_fallback": 0, "last_error": ""}

async def initialize():
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0), limits=httpx.Limits(max_connections=50, max_keepalive_connections=20))
    logger.info(f"AI client → OpenClaw @ {_ENDPOINT} (providers: {config.providers_status()})")

async def close():
    global _client
    if _client: await _client.aclose(); _client = None

async def _wait_for_gateway(timeout=90.0):
    if _client is None: await initialize()
    deadline = asyncio.get_event_loop().time() + timeout
    url = f"{config.OPENCLAW_URL}/v1/models"
    while asyncio.get_event_loop().time() < deadline:
        try:
            r = await _client.get(url, timeout=5.0)
            if r.status_code == 200: return True
        except: pass
        await asyncio.sleep(2.0)
    return False

async def _call_openclaw(messages, max_tokens, temperature, timeout=25.0):
    if _client is None: await initialize()
    payload = {"model": _MODEL, "messages": messages, "temperature": temperature, "max_tokens": max_tokens, "stream": False}
    sem = _get_openclaw_sem()
    for attempt in range(2):
        try:
            async with sem:
                r = await _client.post(_ENDPOINT, json=payload, timeout=timeout)
            if r.status_code == 200:
                data = r.json()
                choices = data.get("choices") or []
                if choices:
                    content = (choices[0].get("message", {}).get("content", "") or "").strip()
                    if content: return content
                return ""
            if r.status_code in (502, 503, 504) and attempt == 0:
                await _wait_for_gateway(30.0); continue
            _stats["last_error"] = f"OpenClaw HTTP {r.status_code}: {r.text[:200]}"
            return ""
        except (httpx.ReadTimeout, httpx.ConnectTimeout):
            _stats["last_error"] = "OpenClaw timeout"
            return ""
        except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as e:
            if attempt == 0: await _wait_for_gateway(30.0); continue
            return ""
        except Exception as e:
            _stats["last_error"] = f"{type(e).__name__}: {e}"
            return ""
    return ""

async def _call_pollinations_direct(messages, max_tokens, timeout=30.0, retries=2):
    if _client is None: await initialize()
    payload = {"model": _POLLINATIONS_MODEL, "messages": messages, "temperature": 0.9, "max_tokens": max_tokens, "stream": False, "referrer": "nastya-bot", "reasoning_effort": "low"}
    sem = _get_pollinations_sem()
    for attempt in range(retries + 1):
        try:
            t_start = time.time()
            headers = _pollinations_headers()
            headers["Referer"] = "nastya-bot"
            async with sem:
                r = await _client.post(_POLLINATIONS_URL, json=payload, timeout=timeout, headers=headers)
            elapsed = time.time() - t_start
            if r.status_code == 200:
                data = r.json()
                choices = data.get("choices") or []
                if choices:
                    msg = choices[0].get("message", {}) or {}
                    content = (msg.get("content", "") or "").strip()
                    if content:
                        return _strip_pollinations_ads(content)
                    reasoning = (msg.get("reasoning", "") or "").strip()
                    if reasoning:
                        parts = reasoning.split(".")
                        return ".".join(parts[-3:]).strip()[:500]
            # If got response but empty content in <5s, retry (Pollinations quick refusal)
            if elapsed < 5.0 and attempt < retries:
                await asyncio.sleep(2.0)
                continue
            return ""
        except:
            if attempt < retries:
                await asyncio.sleep(2.0)
                continue
            return ""
    return ""

async def _call_pollinations_get(prompt, timeout=12.0):
    if _client is None: await initialize()
    from urllib.parse import quote
    url = f"https://text.pollinations.ai/{quote(prompt)}"
    sem = _get_pollinations_sem()
    try:
        headers = {"Accept": "text/plain"}
        headers.update(_pollinations_headers())
        async with sem:
            r = await _client.get(url, timeout=timeout, headers=headers)
        if r.status_code == 200:
            text = r.text.strip()
            if text and len(text) > 2: return text[:2000]
        return ""
    except: return ""

_STATIC_FALLBACKS = {
    "greeting": ["Привет! Я Ася 😊", "Привет-привет! ☕", "Хей! Как настроение?"],
    "howareyou": ["Да нормально, спасибо ☕", "Всё ок, а у тебя?", "Живу-здравствую 😊"],
    "default": ["Хм, давай по-другому", "Интересно, расскажи подробнее?", "Поняла тебя. И что дальше?", "Окей, я с тобой. Продолжай."],
}

def _static_fallback(prompt):
    t = (prompt or "").lower()
    if any(w in t for w in ["привет", "здаров", "хай", "ку", "доброе"]): return random.choice(_STATIC_FALLBACKS["greeting"])
    if any(w in t for w in ["как дела", "как ты", "как жизнь", "что нового"]): return random.choice(_STATIC_FALLBACKS["howareyou"])
    return random.choice(_STATIC_FALLBACKS["default"])

async def chat(prompt, system="", extra_context="", dialog_history=None, max_tokens=600, temperature=0.9, allow_static_fallback=True, fast=False, prefer_pollinations=False):
    global _stats
    _stats["requests"] += 1
    t0 = time.time()
    if _client is None: await initialize()
    messages = []
    if system: messages.append({"role": "system", "content": system})
    if dialog_history: messages.extend(dialog_history)
    user_content = f"{extra_context}\n\n---\n\n{prompt}" if extra_context else prompt
    messages.append({"role": "user", "content": user_content})

    if fast:
        use_get = (not extra_context) and (not dialog_history) and len(prompt) < 400
        if use_get:
            short_persona = "Ты Настя, девушка из Москвы. Женский род всегда. Отвечай живо, кратко (2-4 предложения). По-русски. Без выдуманных фактов. Не начинай с имени."
            embedded = f"{short_persona}\n\nВопрос: {prompt}\n\nОтвет:"
            out = await _call_pollinations_get(embedded, 12.0)
            if out:
                _stats["success"] += 1; _stats["pollinations_backup"] += 1
                logger.info(f"AI fast=pollinations-GET ({time.time()-t0:.1f}s) len={len(out)}")
                return _strip_name_prefix(out)
        out = await _call_pollinations_direct(messages, max_tokens, 45.0)
        if out:
            _stats["success"] += 1; _stats["pollinations_backup"] += 1
            logger.info(f"AI fast=pollinations-POST ({time.time()-t0:.1f}s) len={len(out)}")
            return _strip_name_prefix(out)
        out = await _call_openclaw(messages, max_tokens, temperature, 15.0)
        if out:
            _stats["success"] += 1; _stats["openclaw_ok"] += 1
            return _strip_name_prefix(out)
    else:
        # If prefer_pollinations, try Pollinations POST first (more reliable for long prompts)
        if prefer_pollinations:
            out = await _call_pollinations_direct(messages, max_tokens, 45.0)
            logger.info(f"AI Pollinations-POST: {len(out) if out else 0} chars ({time.time()-t0:.1f}s)")
            if out:
                _stats["success"] += 1; _stats["pollinations_backup"] += 1
                return _strip_name_prefix(out)
        out = await _call_openclaw(messages, max_tokens, temperature, 25.0)
        logger.info(f"AI OpenClaw: {len(out) if out else 0} chars ({time.time()-t0:.1f}s)")
        if out:
            _stats["success"] += 1; _stats["openclaw_ok"] += 1
            return _strip_name_prefix(out)
        if not prefer_pollinations:
            out = await _call_pollinations_direct(messages, max_tokens, 45.0)
            logger.info(f"AI Pollinations-POST: {len(out) if out else 0} chars ({time.time()-t0:.1f}s)")
            if out:
                _stats["success"] += 1; _stats["pollinations_backup"] += 1
                return _strip_name_prefix(out)
        # GET fallback: build a combined prompt and use GET (more reliable for long prompts)
        combined = ""
        if system: combined += system + "\n\n"
        combined += prompt
        if len(combined) > 3500:
            combined = combined[:3500]
        out = await _call_pollinations_get(combined, 60.0)
        logger.info(f"AI Pollinations-GET: {len(out) if out else 0} chars ({time.time()-t0:.1f}s)")
        if out:
            out = _strip_pollinations_ads(out)
            _stats["success"] += 1; _stats["pollinations_backup"] += 1
            logger.info(f"AI fallback=pollinations-GET ({time.time()-t0:.1f}s) len={len(out)}")
            return _strip_name_prefix(out)
        # Tier-2: Cloudflare Workers AI (more reliable, no rate limits)
        out = await _call_cloudflare(messages, max_tokens, 30.0)
        logger.info(f"AI Cloudflare: {len(out) if out else 0} chars ({time.time()-t0:.1f}s)")
        if out:
            _stats["success"] += 1
            logger.info(f"AI fallback=cloudflare ({time.time()-t0:.1f}s) len={len(out)}")
            return _strip_name_prefix(out)

    _stats["fail"] += 1
    _stats["last_error"] = "all providers returned empty"
    if allow_static_fallback:
        fb = _static_fallback(prompt)
        _stats["static_fallback"] += 1
        return fb
    return ""

async def _call_cloudflare(messages, max_tokens, timeout=30.0):
    """Call Cloudflare Workers AI (Tier-2 fallback). Returns content or empty string."""
    if _client is None: await initialize()
    acct = _get_cf_account()
    if not acct:
        return ""
    account_id, token = acct
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/chat/completions"
    payload = {
        "model": _CF_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.85,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        r = await _client.post(url, json=payload, timeout=timeout, headers=headers)
        if r.status_code == 200:
            data = r.json()
            # Cloudflare returns either OpenAI format or {result: {response: ...}}
            choices = data.get("choices") or []
            if choices:
                content = (choices[0].get("message", {}).get("content", "") or "").strip()
                if content:
                    return content
            result = data.get("result", {})
            if isinstance(result, dict):
                content = (result.get("response", "") or "").strip()
                if content:
                    return content
        return ""
    except Exception as e:
        _stats["last_error"] = f"Cloudflare: {type(e).__name__}: {e}"
        return ""


def _strip_name_prefix(text):
    if not text: return text
    import re
    stripped = re.sub(r'^\s*Ася\s*[:,\-—]\s*', '', text, flags=re.IGNORECASE)
    stripped = re.sub(r'^\s*Ответ\s*[:,\-—]\s*', '', stripped, flags=re.IGNORECASE)
    return stripped

async def comment(prompt, extra_context="", mood="", dialog_history=None):
    from bot.persona import COMMENT_PROMPT
    system = COMMENT_PROMPT + (f"\n\nТвоё текущее настроение: {mood}." if mood else "")
    return await chat(prompt, system=system, extra_context=extra_context, dialog_history=dialog_history, max_tokens=400, temperature=0.95, allow_static_fallback=False)

async def vision(prompt, image_data_uri, system="", max_tokens=300):
    global _stats
    _stats["requests"] += 1
    t0 = time.time()
    if _client is None: await initialize()
    messages = []
    if system: messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": image_data_uri}}]})
    payload = {"model": _MODEL, "messages": messages, "max_tokens": max_tokens, "temperature": 0.7, "stream": False}
    try:
        r = await _client.post(_ENDPOINT, json=payload, timeout=30.0)
        if r.status_code == 200:
            data = r.json()
            choices = data.get("choices") or []
            if choices:
                content = (choices[0].get("message", {}).get("content", "") or "").strip()
                if content:
                    _stats["success"] += 1; _stats["openclaw_ok"] += 1
                    return content
        _stats["fail"] += 1
    except: _stats["fail"] += 1
    return ""

async def transcribe_audio(audio_data_uri, timeout=30.0):
    global _stats
    _stats["requests"] += 1
    groq_key = config.GROQ_API_KEY
    if not groq_key:
        _stats["fail"] += 1; return ""
    try:
        import base64 as _b64
        raw_b64 = audio_data_uri.split(",", 1)[1] if "," in audio_data_uri else audio_data_uri
        audio_bytes = _b64.b64decode(raw_b64)
        files = {"file": ("voice.ogg", audio_bytes, "audio/ogg"), "model": (None, "whisper-large-v3"), "language": (None, "ru")}
        r = await _client.post("https://api.groq.com/openai/v1/audio/transcriptions", headers={"Authorization": f"Bearer {groq_key}"}, files=files, timeout=timeout)
        if r.status_code == 200:
            text = (r.json().get("text", "") or "").strip()
            if text:
                _stats["success"] += 1
                return text
        _stats["fail"] += 1
    except: _stats["fail"] += 1
    return ""

def stats(): return dict(_stats)
