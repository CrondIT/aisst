"""Обработка входящих обновлений и webhook от MAX."""

import asyncio
import time
from collections import defaultdict
from global_state import (
    MAX_API_TOKEN,
    MAX_BASE_URL,
    WEBHOOK_URL,
    WEBHOOK_SECRET,
    TRUSTED_WEBHOOK_IPS,
    RATE_LIMIT_PER_MINUTE,
    ALLOWED_EXTENSIONS,
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
_rate_limit_store: dict[int, list[float]] = defaultdict(list)


def _check_rate_limit(user_id: int) -> bool:
    """
    Проверяет лимит запросов для пользователя.
    Возвращает True, если запрос разрешён, False — если превышен.
    """
    now = time.monotonic()
    window = 60.0  # 1 минута
    timestamps = _rate_limit_store[user_id]

    # Удаляем устаревшие записи
    timestamps[:] = [t for t in timestamps if now - t < window]

    if len(timestamps) >= RATE_LIMIT_PER_MINUTE:
        return False

    timestamps.append(now)
    return True


async def _process_file_async(
        file_path: str,
        sender: dict,
        user_id: int
) -> None:
    """Асинхронная обработка файла без блокировки webhook."""
    try:
        result = await bot_logic.handle_file(file_path, sender)
        if result:
            await max_api.send_message(user_id, result)
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
        user_id = sender.get("user_id")
        callback_data = callback_obj.get("payload", "")
        logger.info(f"Callback от {sender.get('name')}: {callback_data}")
        if callback_data and user_id:
            command_response = await bot_logic.handle_command(
                callback_data, sender
            )
            if command_response is not None:
                await max_api.send_message(user_id, command_response)
        return

    # 3. Фильтрация по типу
    if update_type != "message_created":
        return

    message = update.get("message", {})
    message_created_at = message.get("created_at")
    if (
        message_created_at
        and lifespan.SERVER_START_TIME
        and message_created_at < lifespan.SERVER_START_TIME.isoformat()
    ):
        logger.info(f"Пропущено старое сообщение: {message.get('message_id')}")
        return

    # 4. Извлечение данных сообщения
    sender = message.get("sender", {})
    body = message.get("body", {})
    user_id = sender.get("user_id")
    user_text = body.get("text", "")

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
    permission = user_data["permission"]
    print(nickname, permission)

    # 8. Обработка вложений
    attachments = body.get("attachments", [])
    for att in attachments:
        attr_url = att.get("payload", {}).get("url")
        # Аудио
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
                    await max_api.send_message(user_id, "Ошибка загрузки аудиофайла.")
                    return

                async def _process_audio_wrapper():
                    try:
                        recognized_text = await transcribe_audio(file_path)
                        await max_api.send_message(user_id, recognized_text)
                        return recognized_text
                    except Exception as e:
                        logger.error(f"Ошибка распознавания аудио: {e}")
                        await max_api.send_message(
                            user_id, "Не удалось распознать аудио."
                        )

                asyncio.create_task(_process_audio_wrapper())
        # Файлы
        if att.get("type") == "file" and attr_url:
            filename = att.get("filename")
            if not filename:
                return
            ext = filename.split('.')[-1].lower()
            name = os.path.splitext(filename)[0]
            if ext not in ALLOWED_EXTENSIONS.get("guestrag"):
                return
            file_path = await save_user_file(
                attr_url, user_id, ext, "rag", name
            )
            if not file_path:
                logger.error(f"Не удалось загрузить файл: {filename}")
                return await max_api.send_message(
                    user_id,
                    f"Не удалось загрузить файл: {filename}"
                )
            await max_api.send_message(user_id, "Файл получен. Начинаю обработку...")
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
        command_parts = user_text.split(maxsplit=1)
        command = command_parts[0].lower()
        if command == "/start" and permission != 1:
            text = "Большой блок информационного текста"
            await max_api.send_inline_message(user_id, text)
            return
        else:
            command_response = await bot_logic.handle_command(
                user_text, sender
            )
        if command_response is not None:
            await max_api.send_message(user_id, command_response)
            return

    try:
        reply_text = await bot_logic.handle_message(request, user_text, sender)

        if not reply_text:
            logger.error(
                f"Пустой ответ для пользователя: {user_id} "
                f"для запроса: {user_text}"
            )
            await max_api.send_message(
                user_id, "Извините, не смог сформировать ответ."
            )
            return

        if len(reply_text) > 4000:
            reply_text = reply_text[:3997] + "..."

        await max_api.send_message(user_id, reply_text)
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
    logger.info(f"Headers: {dict(request.headers)}")

    body = await request.body()
    logger.info(f"Body: {body.decode('utf-8', errors='replace')}")

    # Проверка secret
    secret_header = request.headers.get("X-Max-Bot-Api-Secret")
    logger.info(f"X-Max-Bot-Api-Secret header: '{secret_header}'")
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
