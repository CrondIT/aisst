"""Обработчик режима RAG (управление векторной базой)."""
import asyncio

from fastapi import Request

import db
from global_state import (
    get_user_pending_delete,
    set_user_pending_delete,
    clear_user_pending_delete,
)
from rag_chain import (
    get_all_filenames_from_vector_db,
    delete_file_from_vector_db,
)
from handlers.base import ModeHandler


class RagHandler(ModeHandler):
    """Обработка режима rag — управление документами в векторной базе."""

    async def handle(
        self,
        request: Request,
        user_text: str,
        sender: dict,
    ) -> str | None:
        user_text = user_text.strip()
        user_id = int(sender.get("user_id"))
        user_mode = "rag"

        # Проверка состояния — есть ли файл на удаление
        pending = get_user_pending_delete(user_id)
        # если ожидаем удаление файла, то спрашиваем подтверждение
        if pending is not None:
            confirmations = {"1", "да", "yes", "ok"}
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

        # выводим список документов в базе, если пользователь набрал ls
        if user_text.lower() == "ls":
            docs_list = get_all_filenames_from_vector_db()
            await db.add_billing(user_id, user_mode, user_text, 0, 1)
            return docs_list

        # поиск файла по имени для возможного удаления
        # если пользователь что-то набрал, считаем что это часть имени файла
        # из векторной базы и пытаемся найти файл
        result = get_all_filenames_from_vector_db(search_text=user_text)
        if result and not result.startswith("Файл с таким"):
            # Файл найден, запрашиваем подтверждение
            set_user_pending_delete(user_id, result)
            await db.add_billing(user_id, user_mode, user_text, 0, 1)
            return (
                f"Найден файл: {result}\n"
                "Удалить? (Введите 1 / да / yes / ok)"
                "\n"
                "Для отмены введите 0 / нет / no / или любой символ) "
            )
        return result
