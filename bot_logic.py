"""Модуль бизнес-логики бота: обработка команд и сообщений."""
import asyncio

from fastapi import Request

import db
from global_state import (
    user_modes,
    user_pending_delete,
)
from utils import logger
from load_from_file import (
    save_to_vector_db,
    get_all_filenames_from_vector_db,
    delete_file_from_vector_db,
)

from rag_chain import ask_rag  # ← единственный импорт для RAG

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
    "/aiagent": (
        "aiagent",
        "Режим: AI агент"
        "Загрузка документов в базу знаний - загрузите документ pdf" "\n"
        "Просмотр наименований документов в базе - наберите ls" "\n"
        "Удаление документа из базы знаний:" "\n"
        " для удаления документа отправьте его название" 
    ),
}


async def handle_command(user_text: str, sender: dict) -> str | None:
    """
    Обработка команд бота только устанавливает user_modes[user_id]
    и возвращает текст ответа для информирования пользователя 
    или None, если команда не распознана.
    """
    if not user_text.startswith("/"):
        return None

    command = user_text.split(maxsplit=1)[0].lower()

    user_name = sender.get("name", "Неизвестный пользователь")
    user_id = sender.get("user_id")
    user_data = await db.get_user(user_id)
    # если пользователь гость то разрещен только один режим (для бота ССТ)
    if user_data["permission"] == 1:
        command = "/gigachat"  

    if command == "/billing":
        if user_data:
            balance = user_data["coins"] + user_data["giftcoins"]
            return f"Уважаемый: {user_name}!\n" f"Ваш баланс: {balance} ₽"
        return f"Пользователь: {user_name} в списках не значится)"

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
        case "aiagent":
            user_text = user_text.strip()
            user_id = sender.get("user_id")

            # Проверка состояния подтверждения удаления
            if user_id in user_pending_delete:
                confirmations = {
                    "1", "да", "yes", "ok"
                }
                if user_text.lower() in confirmations:
                    file_to_del = user_pending_delete.pop(user_id)
                    return await asyncio.to_thread(
                        delete_file_from_vector_db, file_to_del
                    )
                else:
                    user_pending_delete.pop(user_id, None)
                    return "Удаление отменено."

            if user_text.lower() == "ls":
                # выводим список документов в базе, если пользователь набрал ls
                docs_list = get_all_filenames_from_vector_db()
                return docs_list

            # поиск файла по имени для возможного удаления
            result = get_all_filenames_from_vector_db(search_text=user_text)
            if result and not result.startswith("Файл с таким"):
                # Файл найден, запрашиваем подтверждение
                user_pending_delete[user_id] = result
                return (
                    f"Найден файл: {result}\n"
                    "Удалить? (Введите 1 / да / yes / ok)" "\n"
                    "Для отмены введите 0 / нет / no / или любой символ) "
                )
            return result

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
    if user_modes.get(sender["user_id"]) == "aiagent":
        return await save_to_vector_db(
            file_path=file_name, sender=sender, model_name="Embeddings"
        )
    return "Режим еще не работает"

