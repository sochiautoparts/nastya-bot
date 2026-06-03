"""Nastya Fun Handler — minimal, mostly just /fact for entertainment."""
import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message
from bot.nastya import get_random_fact

logger = logging.getLogger(__name__)
router = Router()


@router.message(F.text == "/fact")
async def cmd_fact(message: Message, db=None, ai_router=None) -> None:
    """Random fun fact about Nastyas."""
    fact = get_random_fact()
    await message.answer(fact)
