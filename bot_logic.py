"""Модуль бизнес-логики бота: обработка команд и сообщений."""

from fastapi import Request

import db
from global_state import (
    user_modes,
    GUEST_RAG_DIR,
)
from utils import logger
from load_from_file import (
    save_to_vector_db,
    check_vector_db,
)
from rag_embeddings import (
    get_giga_embeddings,
    search_vector_db,
    format_sources,
)


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
            return f"Уважаемый: {user_name}!\n" f"Ваш баланс: {balance} ₽"
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
        return "Режим редактирование векторной базы данные для ИИ агента гостя"

    return None


async def handle_message(
        request: Request, user_text: str, sender: dict
) -> str | None:
    """Обработка сообщений пользователя."""
    user_id = sender.get("user_id")
    if user_modes.get(user_id) is None:
        user_modes[user_id] = "gigachat"
    logger.info(
        f"handle_message: user_id={user_id}, mode={user_modes[user_id]}"
    )

    match user_modes[user_id]:
        case "gigachat":
            logger.info("-------------------in case---------------")
            client = request.app.state.giga_client
            logger.info("-------------------client---------------")
            embeddings = get_giga_embeddings(model_name="Embeddings")
            logger.info("-------------------embeddings---------------")
            vector_db = check_vector_db(
                persist_dir=GUEST_RAG_DIR,
                embeddings=embeddings
            )
            logger.info("-------------------vector_db---------------")
            result = search_vector_db(user_text, vector_db, top_k=3)
            logger.info("-------------------result---------------")
            prompt = (
                f"Используй этот контекст для ответа: {result.context}\n\n"
                f"Вопрос: {user_text}"
            )
            logger.info("-------------------prompt---------------")
            response = client.generate(prompt, model="GigaChat-Pro")
            logger.info("-------------------response---------------")
            answer = response.choices[0].message.content
            logger.info("-------------------answer---------------")
            sources = format_sources(result.sources)
            return f"{answer}\n\n{sources}"
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
            return "Используйте /chat для начала общения с ИИ."


async def handle_image(
        request: Request, image_path: str, sender: dict
) -> str | None:
    """Обработка изображений."""
    if user_modes.get(sender["user_id"]) == "edit":
        return "Режим редактирования ещё не реализован."
    return "Режим еще не работает"


async def handle_file(file_name: str, sender: dict) -> str | None:
    """Обработка файлов."""
    if user_modes.get(sender["user_id"]) == "guestrag":
        return await save_to_vector_db(
            file_path=file_name, sender=sender, model_name="Embeddings"
        )
    return "Режим еще не работает"
