"""Модуль бизнес-логики бота: обработка команд и сообщений."""

from fastapi import Request

import db
from global_state import (
    user_modes,
)
from utils import logger
from load_from_file import (
    save_to_vector_db,
)

from rag_chain import ask_rag  # ← единственный импорт для RAG


async def handle_command(user_text: str, sender: dict) -> str | None:
    """
    Обработка команд бота.
    Возвращает текст ответа или None, если команда не распознана.
    """
    if not user_text.startswith("/"):
        return None

    command = user_text.split(maxsplit=1)[0].lower()

    user_name = sender.get("name", "Неизвестный пользователь")
    user_id = sender.get("user_id")
    user_data = await db.get_user(user_id)
    if user_data["permission"] == 1:
        command = "/gigachat"  # если пользователь гость то только один режим

    if command == "/billing":
        if user_data:
            balance = user_data["coins"] + user_data["giftcoins"]
            return f"Уважаемый: {user_name}!\n" f"Ваш баланс: {balance} ₽"
        return f"Пользователь: {user_name} в списках не значится)"

    mode_map = {
        "/gigachat": (
            "gigachat",
            "Режим: чат с ИИ по документам колледжа"
        ),
        "/gigachatpro": (
            "gigachatpro",
            "Режим: GigaChat Pro"
        ),
        "/file": (
            "file",
            "Режим: анализ файлов"
        ),
        "/edit": (
            "edit",
            "Режим: редактирование"
        ),
        "/guestrag": (
            "guestrag",
            "Режим: загрузка документов в базу знаний"
        ),
    }

    if command in mode_map:
        mode, reply = mode_map[command]
        user_modes[user_id] = mode
        return reply

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
            # app.state.giga_lc_client — LangChain GigaChat, совместим с LCEL
            lc_llm = request.app.state.giga_lc_client
            return await ask_rag(user_text=user_text, lc_llm=lc_llm, top_k=3)
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
            return (
                "Вы в режиме загрузки документов. "
                "Отправьте PDF-файл для добавления в базу знаний."
            )
        case None:
            return "Используйте /gigachat для начала общения с ИИ."


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


async def transcribe_audio(audio_data: bytes, ext: str) -> str | None:
    """Заглушка для транскрибации аудио."""
    logger.warning("transcribe_audio вызван, но не реализован")
    return None
