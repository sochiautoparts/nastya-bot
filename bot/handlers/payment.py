"""Настя Stars Payment Handler — Telegram Stars donations."""
import logging, random
from aiogram import Router, F
from aiogram.types import Message, PreCheckoutQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from bot import database as db

logger = logging.getLogger("nastya.payment")
payment_router = Router()

STARS_REQUESTS = [
    "Насте очень хочется новый маникюр! Жми /donate 🎀💅",
    "Настя хочет кофе с овсяным молоком! Подари звёздочек ⭐",
    "Насте нужен новый чехол на айфон! /donate 💅✨",
    "Настя мечтает о спа-дне! Звёздочки приветствуются 🧖‍♀️⭐",
    "Насте срочно нужны духи! /donate 💋",
]

STARS_AMOUNTS = [50, 100, 250, 500]

def _build_stars_keyboard(default_amount=100):
    """Build inline keyboard with Stars donation amounts."""
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    row = []
    for amount in STARS_AMOUNTS:
        row.append(InlineKeyboardButton(text=f"{amount} ⭐", callback_data=f"donate_{amount}"))
    builder.row(*row)
    builder.row(InlineKeyboardButton(text="💋 Потом, Настя!", callback_data="donate_later"))
    return builder.as_markup()


@payment_router.pre_checkout_query()
async def process_pre_checkout(pre_checkout: PreCheckoutQuery):
    """Always accept pre-checkout."""
    await pre_checkout.answer(ok=True)


@payment_router.message(F.successful_payment)
async def process_payment(message: Message):
    """Handle successful Stars payment — Настя is happy!"""
    payment = message.successful_payment
    amount = payment.total_amount
    charge_id = payment.telegram_payment_charge_id

    try:
        await db.record_donation(message.from_user.id, amount, charge_id)
    except Exception as e:
        logger.debug(f"record_donation error: {e}")

    thanks = [
        f"Урааа! {amount} звёздочек! Настя счастлива! 🥰✨",
        f"Оооо, {amount} звёзд! Ты лучший! 💕⭐",
        f"Насте подарили {amount} звёзд! Настя обожает тебя! 💋⭐",
        f"Спасибо-спасибо! {amount} звёзд! Настя купит себе вкусняшку! 😍⭐",
        f"Вау, {amount} звёзд! Настя будет самой доброй... минуту! 💅✨",
    ]
    thank_text = random.choice(thanks)
    try:
        total_stars = await db.get_total_donated(message.from_user.id)
        if total_stars > amount:
            thank_text += f"\n\n💝 Всего подарено Насте: {total_stars} ⭐"
    except: pass
    await message.answer(thank_text)


@payment_router.message(Command("donate"))
async def cmd_donate(message: Message):
    """Show Stars donation keyboard."""
    await message.answer(
        "🎀 Настя принимает звёздочки!\n\n"
        "Выбери сколько подарить Насте:",
        reply_markup=_build_stars_keyboard(),
    )


@payment_router.callback_query(F.data.startswith("donate_"))
async def process_donate_callback(callback):
    """Handle donate button callback."""
    data = callback.data
    if data == "donate_later":
        await callback.message.edit_text("Ну ладно, потом значит! Настя не обижается 💅")
        await callback.answer()
        return

    amount = int(data.replace("donate_", ""))
    try:
        await callback.message.answer_invoice(
            title=f"Звёздочки для Насти ⭐",
            description=f"Подарить Насте {amount} звёздочек 🎀",
            payload=f"nastya_donation_{amount}",
            currency="XTR",
            prices=[{"label": f"{amount} звёзд", "amount": amount}],
            provider_token="",
        )
        logger.info(f"Stars invoice sent: {amount} XTR to user {callback.from_user.id}")
    except Exception as e:
        logger.error(f"Failed to send Stars invoice: {e}")
        await callback.message.answer("Ой, что-то с платёжкой! Попробуй позже 💔")
    await callback.answer()
