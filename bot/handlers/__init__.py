"""Nastya Bot Handlers 🎀"""
from aiogram import Router
from bot.handlers.chat import router as chat_router
from bot.handlers.fun import router as fun_router
from bot.handlers.payment import router as payment_router
from bot.handlers.admin import router as admin_router

all_routers = [chat_router, fun_router, payment_router, admin_router]
