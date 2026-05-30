"""Nastya Donation Handler — Stars payments, support the dev only."""
import logging
from aiogram import Router, F
from aiogram.types import Message, PreCheckoutQuery

logger = logging.getLogger(__name__)
router = Router()


@router.pre_checkout_query()
async def process_pre_checkout(pre_checkout: PreCheckoutQuery, db=None, ai_router=None) -> None:
    await pre_checkout.answer(ok=True)


@router.message(F.successful_payment)
async def process_payment(message: Message, db=None, ai_router=None) -> None:
    payment = message.successful_payment
    amount = payment.total_amount
    charge_id = payment.telegram_payment_charge_id

    if db:
        await db.record_donation(message.from_user.id, amount, charge_id)

    thanks = {
        100: "Ой, кофе! Спасибо! Настя теперь добрее... ненадолго",
        500: "Помада! Настя довольна! Спасибо!",
        1000: "Маникюр! О боже, ты лучший! Настя тебя обожает!",
        3000: "Платье?! Серьёзно?! Настя в шоке от щедрости!",
        5000: "СУМОЧКА! Настя тебя не забудет! Ну... надолго не забудет",
        10000: "БИЛЕТ НА МОРЕ?! Настя реально в шоке! Ты невероятный!",
        100000: "НАСТЯ КОРОЛЕВА!!! Это лучшее что случалось! Ты легенда!",
    }

    thank_text = "Спасибо! Настя рада!"
    for amt in sorted(thanks.keys()):
        if amount >= amt:
            thank_text = thanks[amt]

    total_stars = 0
    if db:
        total_stars = await db.get_total_donated(message.from_user.id)

    await message.answer(
        f"{thank_text}\n\n"
        f"⭐ Получено: {amount}\n"
        f"💝 Всего подарено Насте: {total_stars}"
    )
