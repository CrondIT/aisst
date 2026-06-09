"""Обработчик режима image (генерация и редактирование изображений)."""
import os

from fastapi import Request

import db
import max_api
from global_state import (
    get_user_edit_data,
    set_user_edit_data,
    get_user_edit_queue,
    set_user_edit_queue,
    clear_user_pending_delete,
    enqueue_task,
    get_queue_size,
    TEMP_DIR,
    MAX_REF_IMAGES,
    MAX_CONCURRENT_IMAGES,
)
from utils import logger
from handlers.base import ModeHandler


class ImageHandler(ModeHandler):
    """
    Обработка режима image.
    Генерация и редактирование изображений через LLM-клиент (OpenAI, Gemini и т.д.).
    Параметризован: client_attr — имя атрибута на app.state,
    model_name — модель для генерации, error_msg — текст при отсутствии клиента.
    """

    def __init__(
        self,
        client_attr: str = "gemini_client",
        model_name: str = "gemini-3.1-flash-image-preview",
        error_msg: str = "Клиент изображений не настроен.",
    ):
        self.client_attr = client_attr
        self.model_name = model_name
        self.error_msg = error_msg

    async def handle(
        self,
        request: Request,
        user_text: str,
        sender: dict,
    ) -> str | None:
        user_id = int(sender.get("user_id"))
        
        # Проверяем наличие клиента (для валидации конфигурации)
        client = getattr(request.app.state, self.client_attr, None)
        if client is None:
            return self.error_msg

        user_text = user_text.strip()
        if not user_text:
            return "Опишите изображение, которое хотите создать или изменить."

        operation_type = "генерация"
        queue_type = "image_gen"
        image_paths = []

        # Собираем изображения из очереди
        edit_queue = get_user_edit_queue(user_id)
        if edit_queue:
            valid_paths = [
                p for p in edit_queue if p is not None and os.path.exists(p)
            ]
            image_paths.extend(valid_paths)

        # Если нет изображений из очереди — берём последнее отредактированное
        if not image_paths:
            edit_data = get_user_edit_data(user_id)
            last_edited = edit_data.get("last_image")
            if last_edited and os.path.exists(last_edited):
                image_paths.append(last_edited)

        # Определяем тип операции
        if image_paths:
            operation_type = "редактирование"
            queue_type = "image_edit"

        # Ограничиваем количество изображений
        if len(image_paths) > MAX_REF_IMAGES:
            image_paths = image_paths[:MAX_REF_IMAGES]

        logger.info(
            f"image_handler: user_id={user_id}, "
            f"клиент={self.client_attr}, модель={self.model_name}, "
            f"операция={operation_type}, "
            f"входных_изображений={len(image_paths)}"
        )

        # Формируем данные задачи для воркера
        task_data = {
            "user_id": user_id,
            "prompt": user_text,
            "model": self.model_name,
            "size": "1024x1024",
            "image_paths": image_paths,
            "operation": operation_type,
            "client_attr": self.client_attr,
        }

        # Ставим задачу в очередь Redis
        try:
            task_id = enqueue_task(queue_type, task_data, priority="normal")
            logger.info(f"Задача {task_id[:8]}... поставлена в очередь {queue_type}")
        except Exception as e:
            logger.error(f"Ошибка постановки задачи в очередь: {e}", exc_info=True)
            return f"⚠️ Ошибка: не удалось поставить задачу в очередь. {str(e)[:100]}"

        # Определяем позицию в очереди и примерное время ожидания
        queue_size = get_queue_size("image_gen") + get_queue_size("image_edit")
        est_seconds = max(30, round((queue_size * 45) / MAX_CONCURRENT_IMAGES))
        if est_seconds >= 120:
            est_str = f"~{est_seconds // 60} мин."
        else:
            est_str = f"~{est_seconds} сек."

        # Отправляем пользователю подтверждение
        await max_api.send_message(
            user_id,
            f"🎨 {operation_type.capitalize()} изображения запущена...\n"
            f"Запрос: {user_text[:200]}\n"
            f"📍 Позиция в очереди: {queue_size}\n"
            f"⏳ Ожидаемое время: {est_str}"
        )

        # Очистка очереди изображений — воркер использует сохранённые пути
        # но не очищаем сразу, т.к. воркер ещё не обработал
        # Очистка будет в воркере после успешной обработки
        
        # Возвращаем пустую строку — задача поставлена в очередь, ответ уже отправлен
        return ""

    @staticmethod
    def _cleanup_old_files(
        image_paths: list[str],
        edit_data: dict | None,
    ) -> None:
        """Удаляет временные файлы исходных изображений."""
        for path in image_paths:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
