"""Модуль бизнес-логики бота: обработка команд и сообщений."""
import asyncio

from fastapi import Request

import db
from global_state import (
    get_user_mode,
    set_user_mode,
    get_user_pending_delete,
    set_user_pending_delete,
    clear_user_pending_delete,
    enqueue_task,
    _use_redis,
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
    "/rag": (
        "rag",
        "Режим настройки базы знаний (RAG)." "\n"
        "Загрузка документов в базу знаний - загрузите документ pdf" "\n"
        "Просмотр наименований документов в базе - наберите ls" "\n"
        "Удаление документа из базы знаний:" "\n"
        " для удаления документа отправьте его название" 
    ),
}


async def handle_command(user_text: str, sender: dict) -> str | None:
    """
    Обработка команд бота - устанавливает режим пользователя
    и возвращает текст ответа для информирования пользователя 
    или None, если команда не распознана.
    """
    if not user_text.startswith("/"):
        return None

    command = user_text.split(maxsplit=1)[0].lower()

    user_name = sender.get("name", "Неизвестный пользователь")
    user_id = int(sender.get("user_id"))
    user_data = await db.get_user(user_id)
    # если пользователь гость то разрещен только один режим (для бота ССТ)
    if user_data["permission"] == 1:
        command = "/gigachat"  

    if command == "/billing":
        if user_data:
            balance = user_data["coins"] + user_data["giftcoins"]
            return f"Уважаемый: {user_name}!\n" f"Ваш баланс: {balance} ₽"
        return f"Пользователь: {user_name} в списках не значится)"
    
    if command == "/mode":
        return get_user_mode(user_id)
    
    if command in mode_map:
        mode, reply = mode_map[command]
        set_user_mode(user_id, mode)
        # Очищаем состояние подтверждения удаления
        clear_user_pending_delete(user_id)
        return reply
    
    return "Вы ввели неправильную команду"


async def handle_message(
        request: Request, user_text: str, sender: dict
) -> str | None:
    """Обработка сообщений пользователя."""
    user_id = int(sender.get("user_id"))
    user_mode = get_user_mode(user_id)
    # Если режим не установлен, по умолчанию gigachat
    if not user_mode:
        user_mode = "gigachat"
        set_user_mode(user_id, user_mode)
    
    logger.info(
        f"handle_message: user_id={user_id}, mode={user_mode}"
    )
    
    match user_mode:
        case "gigachat":
            lc_llm = request.app.state.giga_lc_client
            answer = await ask_rag(
                user_text=user_text, lc_llm=lc_llm, top_k=3
            )
            await db.add_billing(user_id, user_mode, user_text, 0, 2)
            return answer
            
        case "gigachatpro":
            answer = await request.app.state.giga_client.generate(
                user_text,
                model="GigaChat-Max",
            )
            await db.add_billing(user_id, user_mode, user_text, 0, 5)
            return answer
        case "file":
            return "Режим работы с файлами ещё не реализован."
        case "edit":
            return "Режим редактирования ещё не реализован."
        case "rag":
            user_text = user_text.strip()
            user_id = int(sender.get("user_id"))

            # Проверка состояния подтверждения удаления
            pending = get_user_pending_delete(user_id)
            if pending is not None:
                confirmations = {
                    "1", "да", "yes", "ok"
                }
                if user_text.lower() in confirmations:
                    file_to_del = get_user_pending_delete(user_id)
                    clear_user_pending_delete(user_id)
                    await db.add_billing(user_id, user_mode, user_text, 0, 1)
                    return await asyncio.to_thread(
                        delete_file_from_vector_db, file_to_del
                    )
                else:
                    clear_user_pending_delete(user_id)
                    return "Удаление отменено."
            
            if user_text.lower() == "ls":
                # выводим список документов в базе, если пользователь набрал ls
                docs_list = get_all_filenames_from_vector_db()
                await db.add_billing(user_id, user_mode, user_text, 0, 1)
                return docs_list
            
            # поиск файла по имени для возможного удаления
            result = get_all_filenames_from_vector_db(search_text=user_text)
            if result and not result.startswith("Файл с таким"):
                # Файл найден, запрашиваем подтверждение
                set_user_pending_delete(user_id, result)
                await db.add_billing(user_id, user_mode, user_text, 0, 1)
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
    user_id = int(sender.get("user_id"))
    if get_user_mode(user_id) == "edit":
        return "Режим редактирования ещё не реализован."
    return "Режим еще не работает"


async def handle_file(file_name: str, sender: dict) -> str | None:
    """Обработка файлов. Использует Redis очередь для больших файлов."""
    user_id = int(sender.get("user_id"))
    user_mode = get_user_mode(user_id)
    if user_mode == "rag":
        if _use_redis:
            try:
                task_id = enqueue_task("rag", {
                    "file_path": file_name,
                    "user_id": user_id,
                    "sender": sender,
                })
                logger.info(
                    f"RAG задача {task_id[:8]}... добавлена в очередь "
                    f"для пользователя {user_id}"
                )
                return (
                    "📥 Файл принят в обработку.\n"
                    "Это может занять несколько минут.\n"
                    "Вы получите уведомление по завершении."
                )
            except Exception as e:
                logger.error(f"Не удалось добавить задачу в очередь: {e}")
        result = await save_to_vector_db(
            file_path=file_name, sender=sender, model_name="Embeddings"
        )
        await db.add_billing(
            user_id, user_mode, "save_to_vector_db", 0, 5, notes=result
        )
        return result
    return "Режим еще не работает"

