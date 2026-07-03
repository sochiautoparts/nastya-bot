"""Люба Media Handler — photo + voice download for vision/whisper."""
import base64, logging
from aiogram import Bot
from aiogram.types import Message

logger = logging.getLogger("nastya.media")

def extract_caption(message):
    return (message.caption or "").strip()

async def download_photo_as_base64(bot, message, max_size=5_000_000):
    try:
        if not message.photo: return ""
        photo = message.photo[-1]
        if photo.file_size and photo.file_size > max_size: return ""
        downloaded = await bot.download(photo)
        if downloaded is None: return ""
        raw = downloaded.read() if hasattr(downloaded, "read") else bytes(downloaded)
        if not raw or len(raw) > max_size: return ""
        return f"data:image/jpeg;base64,{base64.b64encode(raw).decode('ascii')}"
    except Exception as e:
        logger.debug(f"photo download error: {e}")
        return ""

async def download_voice_as_base64(bot, message, max_size=20_000_000):
    try:
        if not message.voice: return ""
        voice = message.voice
        if voice.file_size and voice.file_size > max_size: return ""
        downloaded = await bot.download(voice)
        if downloaded is None: return ""
        raw = downloaded.read() if hasattr(downloaded, "read") else bytes(downloaded)
        if not raw or len(raw) > max_size: return ""
        return f"data:audio/ogg;base64,{base64.b64encode(raw).decode('ascii')}"
    except Exception as e:
        logger.debug(f"voice download error: {e}")
        return ""
