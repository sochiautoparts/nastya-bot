"""Inter-Bot Communication System v1.0.

Настя ↔ Ася — межботовое взаимодействие.

Это заглушка для будущей коммуникации между Настей и Асей.
Ася — другой бот в экосистеме, и иногда им нужно общаться:

Примеры использования:
- Настя может переслать Асе сложный запрос, который та лучше обработает
- Ася может попросить Настю написать пост в канал
- Обмен новостями и контентом между ботами
- Координация при обработке пользователей

Возможные реализации:
1. Shared JSON файл (самый простой вариант)
2. Специальный Telegram чат между ботами
3. Redis/pubsub для real-time обмена
4. HTTP API между сервисами

Пока что — заглушки, которые можно заменить на реальную реализацию.
"""

import logging
import json
import time
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("nastya.interbot")

# Path to shared messages file (can be replaced with Redis/API)
_INTERBOT_MESSAGES_FILE = "data/interbot_messages.json"

# Message structure:
# {
#     "from": "nastya" | "asya",
#     "to": "nastya" | "asya",
#     "text": str,
#     "timestamp": float,
#     "read": bool,
# }


async def send_to_asya(message: str) -> bool:
    """Send a message to Ася bot.
    
    Stub implementation — writes to a shared JSON file.
    Can be replaced with Telegram bot API, Redis, or HTTP API.
    
    Args:
        message: Text message to send to Ася
        
    Returns:
        True if message was sent successfully, False otherwise
    """
    try:
        msg = {
            "from": "nastya",
            "to": "asya",
            "text": message,
            "timestamp": time.time(),
            "read": False,
        }
        
        # Write to shared file
        messages = _load_messages()
        messages.append(msg)
        _save_messages(messages)
        
        logger.info(f"Inter-bot message sent to Ася: {message[:100]}...")
        return True
    except Exception as e:
        logger.error(f"Failed to send message to Ася: {e}")
        return False


async def check_messages() -> List[str]:
    """Check for unread messages from Ася.
    
    Stub implementation — reads from a shared JSON file.
    Can be replaced with Telegram bot API, Redis, or HTTP API.
    
    Returns:
        List of unread message texts from Ася
    """
    try:
        messages = _load_messages()
        unread = []
        
        for msg in messages:
            if (msg.get("to") == "nastya" 
                and msg.get("from") == "asya" 
                and not msg.get("read", False)):
                unread.append(msg.get("text", ""))
                msg["read"] = True
        
        if unread:
            _save_messages(messages)
            logger.info(f"Inter-bot: {len(unread)} new messages from Ася")
        
        return unread
    except Exception as e:
        logger.error(f"Failed to check messages from Ася: {e}")
        return []


def _load_messages() -> list:
    """Load messages from shared JSON file."""
    try:
        path = Path(_INTERBOT_MESSAGES_FILE)
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.debug(f"Error loading interbot messages: {e}")
    return []


def _save_messages(messages: list) -> None:
    """Save messages to shared JSON file."""
    try:
        path = Path(_INTERBOT_MESSAGES_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Keep only last 100 messages to avoid file bloat
        recent = messages[-100:]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(recent, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error saving interbot messages: {e}")
