"""Nastya Donation Handler - Stars payments with ACTIVE Pay buttons."""
import logging
import random
from aiogram import Router, F
from aiogram.types import Message, PreCheckoutQuery

logger = logging.getLogger(__name__)
router = Router()


@router.pre_checkout_query()
async def process_pre_checkout(pre_checkout: PreCheckoutQuery, db=None, ai_router=None) -> None:
    """Always accept pre-checkout."""
    await pre_checkout.answer(ok=True)


@router.message(F.successful_payment)
async def process_payment(message: Message, db=None, ai_router=None) -> None:
    """Handle successful Stars payment - Nastya is happy!"""
    payment = message.successful_payment
    amount = payment.total_amount
    charge_id = payment.telegram_payment_charge_id

    if db:
        await db.record_donation(message.from_user.id, amount, charge_id)

    thanks = [
        f"Урааа! {amount} звёздочек! Настя счастлива! 🥰✨",
        f"Оооо, {amount} звёзд! Ты лучший! 💕⭐",
        f"Насте подарили {amount} звёзд! Настя обожает тебя! 💋⭐",
        f"Спасибо-спасибо! {amount} звёзд! Настя купит себе вкусняшку! 😍⭐",
        f"Вау, {amount} звёзд! Настя будет самой доброй... минуту! 💅✨",
    ]

    thank_text = random.choice(thanks)

    total_stars = 0
    if db:
        total_stars = await db.get_total_donated(message.from_user.id)

    if total_stars > amount:
        thank_text += f"\n\n💝 Всего подарено Насте: {total_stars} ⭐"

    await message.answer(thank_text)
