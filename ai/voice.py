"""Voice transcription using Groq Whisper API (free tier) or fallback."""
import logging
import base64
import tempfile
import os
from typing import Optional
import httpx

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
if GROQ_API_KEY in ("not_configured", "NOT_CONFIGURED", ""):
    GROQ_API_KEY = ""


async def transcribe_voice_ogg(ogg_bytes: bytes) -> Optional[str]:
    """Transcribe voice message (OGG format) using Groq Whisper API."""
    if not GROQ_API_KEY:
        logger.warning("No Groq API key for voice transcription")
        return None

    # Save to temp file (Whisper API needs a file)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            f.write(ogg_bytes)
            tmp_path = f.name

        async with httpx.AsyncClient(timeout=60.0) as client:
            with open(tmp_path, "rb") as audio_file:
                response = await client.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={
                        "Authorization": f"Bearer {GROQ_API_KEY}",
                    },
                    files={
                        "file": ("voice.ogg", audio_file, "audio/ogg"),
                    },
                    data={
                        "model": "whisper-large-v3",
                        "language": "ru",
                        "response_format": "text",
                    },
                )
                response.raise_for_status()
                text = response.text.strip()
                if text:
                    logger.info(f"Voice transcribed: {text[:50]}...")
                    return text
                return None

    except httpx.HTTPStatusError as e:
        logger.error(f"Whisper API error: {e.response.status_code}")
        return None
    except Exception as e:
        logger.error(f"Voice transcription error: {e}")
        return None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
