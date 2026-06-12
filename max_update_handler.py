"""Обработка входящих обновлений и webhook от MAX."""

import asyncio
from global_state import (
    TRUSTED_WEBHOOK_IPS,
    RATE_LIMIT_PER_MINUTE,
    ALLOWED_EXTENSIONS,
    check_rate_limit,
    _use_redis,
    get_user_mode,
)
import bot_logic
import db
import lifespan
from utils import (
    logger,
    save_user_file,
)
from fastapi import Request, HTTPException
from starlette.background import BackgroundTasks
import os
from audio2text_salutespeech import transcribe_audio
import max_api


# ─── Rate limiting ───
# Используем Redis (check_rate_limit из global_state) для синхронизации
# между Gunicorn-воркерами


async def _process_audio_and_respond(
    request: Request,
    file_path: str,
    user_id: int,
    sender: dict
) -> None:
    """Распознавание аудио и передача текста в bot_logic."""
    try:
        recognized_text = await transcribe_audio(file_path)
        if not recognized_text:
            await max_api.send_message(
                user_id, "Не удалось распознать аудио."
            )
            return

        # Сначала показываем пользователю текст распознанного сообщения,
        # потом обрабатываем и отправляем ответ
        await max_api.send_message(user_id, recognized_text)

        reply_text = await bot_logic.handle_message(
            request, recognized_text, sender
        )
        # None означает ошибку, пустая строка "" означает успех без ответа
        if reply_text is None:
            logger.error(f"Пустой ответ для пользователя: {user_id}")
            await max_api.send_message(
                user_id, "Извините, не смог сформировать ответ."
            )
            return

        # Отправляем ответ только если он не пустой
        if reply_text:
            await max_api.send_message(user_id, reply_text, format="markdown")
    except Exception as e:
        logger.error(
            f"Ошибка обработки голосового сообщения: {e}", exc_info=True
        )
        await max_api.send_message(
            user_id, "Произошла ошибка при обработке голосового сообщения."
        )
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(
                "Временный файл голосового сообщения удалён: %s", file_path
            )


def _check_rate_limit(user_id: int) -> bool:
    """
    Проверяет лимит запросов для пользователя через Redis.
    Возвращает True, если запрос разрешён, False — если превышен.
    """
    return check_rate_limit(
        user_id,
        action="message",
        max_requests=RATE_LIMIT_PER_MINUTE,
        window_seconds=60
    )


async def _process_file_async(
        file_path: str,
        sender: dict,
        user_id: int
) -> None:
    """Асинхронная обработка файла без блокировки webhook."""
    try:
        result = await bot_logic.handle_file(file_path, sender)
        if result:
            await max_api.send_message(user_id, result, format="markdown")
    except Exception:
        import traceback
        error_text = traceback.format_exc()
        logger.error(f"Ошибка при обработке файла:\n{error_text}")
        await max_api.send_message(
            user_id, "Ошибка обработки файла. Обратитесь к администратору."
        )


async def process_update(
        update: dict,
        request: Request,
        background_tasks: BackgroundTasks = None
) -> None:
    """Обработка одного обновления."""

    # 1. Определение типа обновления
    update_type = update.get("update_type")

    # 2. Обработка callback-кнопок
    if update_type == "message_callback":
        callback_obj = update.get("callback", {})
        sender = callback_obj.get("user", {})
        user_id = int(sender.get("user_id"))
        callback_data = callback_obj.get("payload", "")
        logger.info(f"Callback от {sender.get('name')}: {callback_data}")
        if callback_data and user_id:
            result = await bot_logic.handle_command(
                callback_data, sender, request.app.state
            )
            if result is not None:
                if result.buttons:
                    await max_api.send_inline_message(
                        user_id, result.text, result.buttons, format=result.format
                    )
                else:
                    await max_api.send_message(
                        user_id, result.text, format=result.format
                    )
        return

    # 3. Фильтрация по типу
    if update_type != "message_created":
        return

    message = update.get("message", {})
    # Timestamp может быть в message.timestamp (миллисекунды) 
    # или message.created_at (ISO)
    message_timestamp_ms = message.get("timestamp")
    message_created_at = message.get("created_at")
    
    if lifespan.SERVER_START_TIME:
        server_start_ms = int(lifespan.SERVER_START_TIME.timestamp() * 1000)
        
        # Проверяем timestamp в миллисекундах
        if message_timestamp_ms and message_timestamp_ms < server_start_ms:
            logger.info(
                f"Пропущено старое сообщение (timestamp): "
                f"mid={message.get('body', {}).get('mid')}"
            )
            return
        
        # Проверяем created_at в ISO формате
        if (
            message_created_at
            and message_created_at < lifespan.SERVER_START_TIME.isoformat()
        ):
            logger.info(
                f"Пропущено старое сообщение (created_at): "
                f"mid={message.get('body', {}).get('mid')}"
            )
            return

    # 4. Извлечение данных сообщения
    sender = message.get("sender", {})
    body = message.get("body", {})
    user_id = int(sender.get("user_id"))
    user_text = body.get("text", "")
    
    # 4.1 Дедупликация сообщений по mid
    mid = body.get("mid")
    if mid:
        from global_state import is_message_processed
        if is_message_processed(mid):
            logger.info(f"Пропущено дублирующееся сообщение: {mid}")
            return
    else:
        logger.warning("Сообщение без mid")

    # 5. Фильтр ботов
    if sender.get("is_bot"):
        return

    # 6. Rate limit
    if not _check_rate_limit(user_id):
        logger.warning(f"Rate limit превышен для user_id={user_id}")
        await max_api.send_message(
            user_id, "Превышен лимит запросов. Попробуйте через минуту."
        )
        return

    # 7. Регистрация пользователя
    nickname = sender.get("name", f"user_{user_id}")
    if await db.create_user(user_id, nickname):
        logger.info(f"Создан пользователь: {nickname} (id={user_id})")
    user_data = await db.get_user(user_id)

    # 8. Обработка вложений
    attachments = body.get("attachments", [])
    for att in attachments:
        attr_url = att.get("payload", {}).get("url")
        # 8.1 Аудио
        if att.get("type") == "audio":
            voice_url = attr_url
            if voice_url and not user_text:
                await max_api.send_message(
                    user_id,
                    "Начинаю распознавание голосового сообщения ... "
                )
                filename = att.get("filename")
                if not filename:
                    filename = "voice.ogg"

                ext = filename.split('.')[-1].lower()
                if not ext:
                    ext = "ogg"
                    filename = f"{filename}.{ext}"

                name = os.path.splitext(filename)[0]
                file_path = await save_user_file(
                    attr_url, user_id, ext, "voice", name
                )
                if not file_path:
                    await max_api.send_message(
                        user_id, "Ошибка загрузки аудиофайла."
                    )
                    return
                asyncio.create_task(
                    _process_audio_and_respond(
                        request, file_path, user_id, sender
                    )
                )

        # 8.2 Изображения
        if att.get("type") == "image" and attr_url:
            filename = att.get("filename", "image.png")
            ext = filename.split('.')[-1].lower()
            if ext not in ("png", "jpg", "jpeg", "gif", "webp", "bmp"):
                ext = "png"
            name = os.path.splitext(filename)[0]
            file_path = await save_user_file(
                attr_url, user_id, ext, "image", name
            )
            if not file_path:
                logger.error(f"Не удалось загрузить изображение: {filename}")
                await max_api.send_message(
                    user_id, f"Не удалось загрузить изображение: {filename}"
                )
                continue
            reply_text = await bot_logic.handle_image(
                request, file_path, sender
            )
            if reply_text:
                await max_api.send_message(
                    user_id, reply_text, format="markdown"
                )
            continue

        # 8.3 Файлы
        if att.get("type") == "file" and attr_url:
            filename = att.get("filename")
            if not filename:
                return
            ext = filename.split('.')[-1].lower()
            name = os.path.splitext(filename)[0]
            if not any(
                ext in ext_set for ext_set in ALLOWED_EXTENSIONS.values()
            ):
                return
            file_path = await save_user_file(
                attr_url, user_id, ext, "file", name
            )
            if not file_path:
                logger.error(f"Не удалось загрузить файл: {filename}")
                return await max_api.send_message(
                    user_id,
                    f"Не удалось загрузить файл: {filename}"
                )
            await max_api.send_message(
                user_id, "Файл получен. Начинаю обработку..."
            )
            if background_tasks:
                background_tasks.add_task(
                    _process_file_async, file_path, sender, user_id
                )
            else:
                asyncio.create_task(
                    _process_file_async(file_path, sender, user_id)
                )
            return

    logger.info(f"Сообщение от {sender.get('name')}: {user_text}")

    if not user_id or not user_text:
        logger.warning(f"Пропущено: user_id={user_id}, text={user_text}")
        return

    # 9. Обработка команд
    if user_text.startswith("/"):
        result = await bot_logic.handle_command(
            user_text, sender, request.app.state
        )
        if result is not None:
            if result.buttons:
                await max_api.send_inline_message(
                    user_id, result.text, result.buttons, format=result.format
                )
            else:
                await max_api.send_message(
                    user_id, result.text, format=result.format
                )
            return

    # 10. LLM-режимы → очередь (если USE_REDIS=True)
    user_mode = get_user_mode(user_id)
    if _use_redis and user_mode in bot_logic.LLM_QUEUE_MODES:
        await bot_logic.enqueue_llm_request(user_text, sender, user_mode)
        await max_api.send_message(
            user_id, "⏳ Запрос обрабатывается..."
        )
        return

    try:
        reply_text = await bot_logic.handle_message(
            request, user_text, sender
        )

        # None означает ошибку, пустая строка "" означает успех без ответа
        if reply_text is None:
            logger.error(
                f"Пустой ответ для пользователя: {user_id} "
                f"для запроса: {user_text}"
            )
            await max_api.send_message(
                user_id, "Извините, не смог сформировать ответ."
            )
            return

        # Отправляем ответ только если он не пустой
        if reply_text:
            await max_api.send_message(user_id, reply_text, format="markdown")
    except Exception as e:
        logger.error(f"Ошибка при обработке сообщения: {e}", exc_info=True)
        await max_api.send_message(user_id, f"Произошла ошибка: {str(e)}")


async def handle_webhook(request: Request) -> tuple[bool, dict | None]:
    """
    Обработка входящего webhook-запроса.
    Возвращает (успех, данные) или вызывает HTTPException.
    """
    # ─── IP-фильтрация ───
    if TRUSTED_WEBHOOK_IPS:
        client_ip = request.client.host if request.client else ""
        if client_ip not in TRUSTED_WEBHOOK_IPS:
            logger.warning(f"Webhook отклонён: недоверенный IP {client_ip}")
            raise HTTPException(status_code=403, detail="IP not trusted")

    logger.info("=== Входящий webhook запрос ===")
    logger.info(f"Method: {request.method}, URL: {request.url}")

    body = await request.body()

    # Проверка secret
    secret_header = request.headers.get("X-Max-Bot-Api-Secret")
    if not max_api.verify_webhook_secret(body, secret_header):
        logger.warning("Неверный X-Max-Bot-Api-Secret — запрос отклонён")
        raise HTTPException(status_code=403, detail="Invalid secret")

    try:
        data = await request.json()
    except Exception:
        logger.error("Не удалось распарсить JSON webhook")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    logger.info(f"Webhook payload: {data.get('update_type', 'unknown')}")
    return True, data
