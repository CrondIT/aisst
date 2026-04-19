"""Модуль бизнес-логики бота: обработка команд и сообщений."""


from fastapi import Request

import db
from global_state import (
    user_modes,
)
from utils import logger
from load_from_file import save_to_vector_db


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
    if command == "/gigachat":
        user_modes[user_id] = "gigachat"
        return "gigachat"
    if command == "/gigachatpro":
        user_modes[user_id] = "gigachatpro"
        return "gigachatpro"
    if command == "/file":
        user_modes[user_id] = "file"
        return "file"
    if command == "/edit":
        user_modes[user_id] = "edit"
        return "edit"
    if command == "/guestrag":
        user_modes[user_id] = "guestrag"
        return (
            "Режим редактирование векторной базы данные для ИИ агента гостя"
        )

    return None


async def handle_message(
        request: Request,
        user_text: str,
        sender: dict
) -> str | None:
    """Обработка сообщений пользователя."""
    user_id = sender.get("user_id")
    if not user_modes[user_id]:
        user_modes[user_id] = "gigachat"
    logger.info(
        f"handle_message: user_id={user_id}, mode={user_modes[user_id]}"
    )

    match user_modes[user_id]:
        case "gigachat":
            return await request.app.state.giga_client.generate(
                user_text,
                max_tokens=1000,
            )
        case "gigachatpro":
            return await request.app.state.giga_client.generate(
                user_text,
                model="GigaChat-Pro",
            )
        case "file":
            return "Режим работы с файлами ещё не реализован."
        case "edit":
            return "Режим редактирования ещё не реализован."
        case "guestrag":
            pass
        case None:
            # по умолчанию - режим gigachat
            return "Используйте /chat для начала общения с ИИ."


async def handle_image(
        request: Request,
        image_path: str,
        sender: dict
) -> str | None:
    """Обработка изображений."""
    if user_modes[sender["user_id"]] == "edit":
        return "Режим редактирования ещё не реализован."
    return "Режим еще не работает"


async def handle_file(
        request: Request,
        file_name: str,
        sender: dict
) -> str | None:
    """Обработка файлов."""
    if user_modes[sender["user_id"]] == "guestrag":
        # сохраняем файл на диске
        # сохраняем файл в векторную базу
        return await save_to_vector_db(file_name)
    return "Режим еще не работает"
