"""Voice transcription — HuggingFace Whisper (free tier) or skip."""
import logging
import tempfile
import os
from typing import Optional
import httpx

logger = logging.getLogger(__name__)

HF_API_KEY = os.environ.get("HUGGINGFACE_API_KEY", "")
if HF_API_KEY in ("not_configured", "NOT_CONFIGURED", ""):
    HF_API_KEY = ""


async def transcribe_voice_ogg(ogg_bytes: bytes) -> Optional[str]:
    """Transcribe voice message using HuggingFace Whisper API.
    Falls back gracefully if no key or API fails.
    """
    if not HF_API_KEY:
        logger.debug("No HuggingFace API key for voice transcription")
        return None

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            f.write(ogg_bytes)
            tmp_path = f.name

        async with httpx.AsyncClient(timeout=30.0) as client:
            with open(tmp_path, "rb") as audio_file:
                response = await client.post(
                    "https://api-inference.huggingface.co/models/openai/whisper-large-v3",
                    headers={"Authorization": f"Bearer {HF_API_KEY}"},
                    data=ogg_bytes,
                    files={"file": ("voice.ogg", audio_file, "audio/ogg")},
                    data={
                        "model": "whisper-large-v3",
                        "language": "ru",
                        "response_format": "json",
                    },
                )
                response.raise_for_status()
                data = response.json()
                text = data.get("text", "").strip()
                if text:
                    logger.info(f"Voice transcribed: {text[:50]}...")
                    return text
                return None

    except httpx.HTTPStatusError as e:
        logger.warning(f"Whisper API HTTP error: {e.response.status_code}")
        return None
    except httpx.TimeoutException:
        logger.warning("Whisper API timeout")
        return None
    except Exception as e:
        logger.warning(f"Voice transcription error: {e}")
        return None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
