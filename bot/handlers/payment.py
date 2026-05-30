"""Nastya Donation Handler 🎀 — Stars payments, no subscriptions, support the dev."""
import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, PreCheckoutQuery, LabeledPrice, InlineKeyboardMarkup, InlineKeyboardButton
from bot.config import DONATION_AMOUNTS, DONATION_LABELS, OWNER_ID

logger = logging.getLogger(__name__)
router = Router()


@router.message(Command("donate"))
async def cmd_donate(message: Message, db=None, ai_router=None) -> None:
    """Show donation options — support Nastya and the developer!"""
    buttons = []
    row = []
    for amount in DONATION_AMOUNTS:
        label = DONATION_LABELS.get(amount, f"Поддержать {amount} ⭐")
        buttons.append([InlineKeyboardButton(
            text=f"{label} — {amount} ⭐",
            callback_data=f"donate_{amount}",
        )])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    # Check user's donation history
    donated_text = ""
    if db:
        total = await db.get_total_donated(message.from_user.id)
        count = await db.get_donation_count(message.from_user.id)
        if total > 0:
            donated_text = f"\n\n💝 Ты уже подарил Насте {total} ⭐ ({count} раз)! Настя благодарна!"

    text = (
        "🎀 Поддержи Настю и разработчика!\n\n"
        "Настя старается быть капризной и весёлой, "
        "а разработчик кормит её серверами и API. "
        "Любая звёздочка — это радость для Насти! 💫\n"
        f"{donated_text}"
    )
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("donate_"))
async def callback_donate(callback, db=None, ai_router=None) -> None:
    """Handle donation amount selection."""
    amount = int(callback.data.replace("donate_", ""))
    label = DONATION_LABELS.get(amount, "Поддержка Насти")

    await callback.message.answer_invoice(
        title=label,
        description=f"Поддержка Насти и разработчика — {amount} Telegram Stars",
        payload=f"donate_{amount}",
        currency="XTR",
        prices=[LabeledPrice(label=label, amount=amount)],
        provider_token="",
    )
    await callback.answer()


@router.pre_checkout_query()
async def process_pre_checkout(pre_checkout: PreCheckoutQuery, db=None, ai_router=None) -> None:
    """Approve pre-checkout."""
    await pre_checkout.answer(ok=True)


@router.message(F.successful_payment)
async def process_payment(message: Message, db=None, ai_router=None) -> None:
    """Handle successful payment — Nastya thanks you!"""
    payment = message.successful_payment
    amount = payment.total_amount
    charge_id = payment.telegram_payment_charge_id

    if db:
        await db.record_donation(message.from_user.id, amount, charge_id)

    # Nastya's thank you messages based on amount
    thanks = {
        100: "Ой, кофе! Спасибо! ☕ Настя теперь добрее... ненадолго 😤",
        500: "Помада! Настя довольна! 💄 Спасибо, ты милый!",
        1000: "Маникюр! О боже, ты лучший! 💅 Настя тебя обожает!",
        3000: "Платье?! Серьёзно?! 😍 Настя в шоке от щедрости!",
        5000: "СУМОЧКА! 🌟 Настя тебя не забудет! Ну... надолго не забудет 😄",
        10000: "БИЛЕТ НА МОРЕ?! ✈️ Настя реально в шоке! Ты невероятный!",
        100000: "НАСТЯ КОРОЛЕВА!!! 👑💎✨ Это лучшее что случалось с Настей! Ты легенда!",
    }

    # Find the closest thank you message
    thank_text = "Спасибо! Настя рада! 💝"
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
