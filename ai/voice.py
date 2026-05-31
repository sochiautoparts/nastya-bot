"""Voice transcription — uses AI router for transcription.
Pollinations Whisper as primary (free), Groq as fallback if key available.
"""
import logging
from typing import Optional
import httpx

logger = logging.getLogger(__name__)


async def transcribe_voice_ogg(ogg_bytes: bytes) -> Optional[str]:
    """Transcribe voice message using free providers."""

    # Try Pollinations Whisper endpoint (free, no key)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://text.pollinations.ai/openai/audio/transcriptions",
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
        logger.warning(f"Pollinations Whisper error: {e}")

    # Try Groq Whisper as fallback (if key is set)
    import os
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
