"""Voice transcription — uses Groq Whisper (free tier) or HuggingFace."""
import logging
import os
from typing import Optional
import httpx

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
if GROQ_API_KEY in ("not_configured", "NOT_CONFIGURED", ""):
    GROQ_API_KEY = ""

HF_API_KEY = os.environ.get("HUGGINGFACE_API_KEY", "")
if HF_API_KEY in ("not_configured", "NOT_CONFIGURED", ""):
    HF_API_KEY = ""


async def transcribe_voice_ogg(ogg_bytes: bytes) -> Optional[str]:
    """Transcribe voice message. Try Groq first (fast), then HuggingFace."""

    # Try Groq Whisper first — fast and free tier available
    if GROQ_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
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

    # Fallback: HuggingFace Whisper
    if HF_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api-inference.huggingface.co/models/openai/whisper-large-v3",
                    headers={"Authorization": f"Bearer {HF_API_KEY}"},
                    data=ogg_bytes,
                )
                response.raise_for_status()
                data = response.json()
                text = data.get("text", "").strip()
                if text:
                    logger.info(f"Voice transcribed (HF): {text[:50]}...")
                    return text
        except Exception as e:
            logger.warning(f"HuggingFace Whisper error: {e}")

    logger.debug("No voice transcription available")
    return None
