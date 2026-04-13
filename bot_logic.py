"""Модуль бизнес-логики бота: обработка команд и сообщений."""

import db
from global_state import (
    user_modes,
)
# from utils import logger


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
    if command == "/start":
        return "start"
    if command == "/file":
        return "file"
    if command == "/edit":
        user_modes[user_id] = "edit"
        return "edit"

    return None
