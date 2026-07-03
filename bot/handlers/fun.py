"""Настя Fun Handler — /fact command."""
import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message
from bot.nastya import get_random_fact

logger = logging.getLogger("nastya.fun")
fun_router = Router()


@fun_router.message(Command("fact"))
async def cmd_fact(message: Message):
    """Random fun fact about Nastyas."""
    await message.answer(get_random_fact())
