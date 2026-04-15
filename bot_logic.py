"""Модуль бизнес-логики бота: обработка команд и сообщений."""

import asyncio
import os
import tempfile
import httpx
from concurrent.futures import ThreadPoolExecutor
from fastapi import Request

import db
from global_state import (
    user_modes,
    GIGACHAT_API_KEY,
    GIGACHAT_SCOPE,
    GIGACHAT_CLIENT_ID,
    GIGACHAT_CLIENT_SECRET,
)
from utils import logger
from gigachat import GigaChat
import ai_models

_executor = ThreadPoolExecutor(max_workers=10)


async def handle_command(user_text: str, sender: dict) -> str | None:
    """
    Обработка команд бота.
    Возвращает текст ответа или None, если команда не распознана.
    """
    if not user_text.startswith("/"):
        return None

    command_parts = user_text.split(maxsplit=1)
    command = command_parts[0].lower()

    user_name = sender.get("name", "Неизвестный пользователь")
    user_id = sender.get("user_id")
    user_data = await db.get_user(user_id)

    if command == "/billing":
        if user_data:
            balance = user_data["coins"] + user_data["giftcoins"]
            return (
                f"Уважаемый: {user_name}!\n"
                f"Ваш баланс: {balance} ₽"
            )
        return f"Пользователь: {user_name} в списках не значится)"

    # Будущие команды:
    if command == "/chat":
        user_modes[user_id] = "chat"
        return "chat"
    if command == "/file":
        user_modes[user_id] = "file"
        return "file"
    if command == "/edit":
        user_modes[user_id] = "edit"
        return "edit"

    return None


async def handle_message(user_text: str, sender: dict) -> str | None:
    """Обработка сообщений пользователя."""
    user_id = sender.get("user_id")

    match user_modes[user_id]:
        case "chat":
            return await Request.app.giga_model.generate(user_text)
        case "file":
            pass
        case "edit":
            pass
        case None:
            pass
