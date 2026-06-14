"""Voice transcription - uses AI router for transcription.

Priority order:
1. Cloudflare Whisper (free, fast, reliable if credentials available)
2. Pollinations Whisper (free, no key)
3. Groq Whisper (if key available)

v2.2: Added Cloudflare Whisper as primary - faster and more reliable.
"""
import logging
import os
from typing import Optional
import httpx

logger = logging.getLogger(__name__)


async def transcribe_voice_ogg(ogg_bytes: bytes) -> Optional[str]:
    """Transcribe voice message using available providers.

    Tries providers in order:
    1. Cloudflare Whisper (free with CF credentials, fast)
    2. Pollinations Whisper (free, no key)
    3. Groq Whisper (if key available)
    """

    # Try Cloudflare Whisper first (fast + reliable if credentials available)
    # NOTE: Config uses CF_TOKEN_1/CF_ACCOUNT_ID_1 naming (matching bot.yml secrets)
    cf_token = os.environ.get("CF_TOKEN_1", "") or os.environ.get("CLOUDFLARE_API_TOKEN", "")
    cf_account = os.environ.get("CF_ACCOUNT_ID_1", "") or os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    if cf_token and cf_account and cf_token not in ("not_configured", ""):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                url = f"https://api.cloudflare.com/client/v4/accounts/{cf_account}/ai/run/@cf/openai/whisper"
                response = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {cf_token}",
                    },
                    files={"file": ("voice.ogg", ogg_bytes, "audio/ogg")},
                    data={"model": "whisper-1", "language": "ru"},
                )
                if response.status_code == 200:
                    data = response.json()
                    # Cloudflare may return result in different formats
                    if data.get("success", False):
                        result = data.get("result", {})
                        text = result.get("text", "").strip()
                    elif "text" in data:
                        text = data["text"].strip()
                    else:
                        text = str(data.get("result", "")).strip()
                    if text:
                        logger.info(f"Voice transcribed (Cloudflare): {text[:50]}...")
                        return text
        except Exception as e:
            logger.warning(f"Cloudflare Whisper error: {e}")

    # Try Pollinations Whisper endpoint (free, no key)
    for endpoint in [
        "https://text.pollinations.ai/openai/audio/transcriptions",
        "https://api.pollinations.ai/openai/audio/transcriptions",
    ]:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    endpoint,
                    files={"file": ("voice.ogg", ogg_bytes, "audio/ogg")},
                    data={"model": "whisper-1", "language": "ru", "response_format": "json"},
                )
                if response.status_code == 200:
                    data = response.json()
                    text = data.get("text", "").strip()
                    if text:
                        logger.info(f"Voice transcribed (Pollinations): {text[:50]}...")
                        return text
        except Exception as e:
            logger.warning(f"Pollinations Whisper error ({endpoint}): {e}")

    # Try Groq Whisper as fallback (if key is set)
    groq_key = os.environ.get("GROQ_API_KEY", "")
    if groq_key and groq_key not in ("not_configured", "NOT_CONFIGURED", ""):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {groq_key}"},
                    files={"file": ("voice.ogg", ogg_bytes, "audio/ogg")},
                    data={"model": "whisper-large-v3", "language": "ru", "response_format": "json"},
                )
                response.raise_for_status()
                data = response.json()
                text = data.get("text", "").strip()
                if text:
                    logger.info(f"Voice transcribed (Groq): {text[:50]}...")
                    return text
        except Exception as e:
            logger.warning(f"Groq Whisper error: {e}")

    logger.debug("No voice transcription available")
    return None
