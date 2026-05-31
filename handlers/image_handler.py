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
    TEMP_DIR,
    MAX_REF_IMAGES,
)
from utils import logger
from handlers.base import ModeHandler


class ImageHandler(ModeHandler):
    """
    Обработка режима image 
    генерация и редактирование изображений через Gemini.
    """

    async def handle(
        self,
        request: Request,
        user_text: str,
        sender: dict,
    ) -> str | None:
        user_id = int(sender.get("user_id"))
        gemini_client = getattr(request.app.state, "gemini_client", None)
        if gemini_client is None:
            return "Gemini клиент не настроен."

        user_text = user_text.strip()
        if not user_text:
            return "Опишите изображение, которое хотите создать или изменить."

        operation_type = "редактирование"
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
        else:
            operation_type = "редактирование"

        if not image_paths:
            operation_type = "генерация"

        # Ограничиваем количество изображений
        if len(image_paths) > MAX_REF_IMAGES:
            image_paths = image_paths[:MAX_REF_IMAGES]

        logger.info(
            f"image_handler: user_id={user_id}, "
            f"операция={operation_type}, "
            f"изображений={len(image_paths)}"
        )

        await max_api.send_message(
            user_id,
            f"🎨 {operation_type.capitalize()} изображения начата...\n"
            f"Запрос: {user_text[:200]}"
        )

        try:
            image_bytes, text_response = await gemini_client.generate_image(
                image_paths=image_paths,
                prompt=user_text,
            )
        except Exception as e:
            error_msg = str(e)
            if "timeout" in error_msg.lower():
                logger.warning(f"Gemini timeout для user_id={user_id}")
                return (
                    "⏰ Время ожидания истекло. "
                    "Попробуйте снова с более простым запросом."
                )
            logger.error(f"Ошибка generate_image: {e}", exc_info=True)
            return f"⚠️ Ошибка: {error_msg[:300]}"

        if text_response is not None:
            await max_api.send_message(user_id, text_response)
            self._cleanup_old_files(image_paths, edit_data=None)
            set_user_edit_queue(user_id, [])
            return None

        if image_bytes is not None:
            os.makedirs(TEMP_DIR, exist_ok=True)
            new_file_path = os.path.join(TEMP_DIR, f"img_{user_id}.jpg")
            old_edit_data = get_user_edit_data(user_id)

            try:
                prefix = (
                    "Сгенерировано по запросу: "
                    if not image_paths
                    else "Отредактировано по запросу: "
                )
                caption = prefix + user_text[:500]

                result = await max_api.send_generated_image(
                    user_id=user_id,
                    image_bytes=image_bytes,
                    caption=caption,
                )

                if result != 200:
                    logger.error(
                        f"Ошибка отправки изображения: status={result}"
                    )
                    await max_api.send_message(
                        user_id,
                        "⚠️ Изображение создано, но не удалось отправить.",
                    )
            except Exception as e:
                await max_api.send_message(
                    user_id,
                    f"⚠️ Ошибка при отправке изображения: {str(e)}",
                )
                logger.error(f"Ошибка отправки изображения: {e}")
            else:
                # Сохраняем путь к новому изображению
                with open(new_file_path, "wb") as f:
                    f.write(image_bytes)
                last_edited = old_edit_data.get("last_image")
                set_user_edit_data(user_id, {"last_image": new_file_path})

                # Удаляем старый файл
                if last_edited and os.path.exists(last_edited):
                    try:
                        os.remove(last_edited)
                    except OSError:
                        pass

            # Очищаем очередь и временные файлы исходных изображений
            self._cleanup_old_files(image_paths, edit_data=old_edit_data)
            set_user_edit_queue(user_id, [])
            clear_user_pending_delete(user_id)

            await db.add_billing(user_id, "image", user_text, 0, 10)
            return None

        return "Не удалось получить результат от модели."

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
