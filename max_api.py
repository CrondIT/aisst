"""Модуль для взаимодействия с MAX API."""

import asyncio
import httpx
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
from fastapi import Request
from starlette.background import BackgroundTasks
import os


async def _process_file_async(
        file_path: str,
        sender: dict,
        user_id: int
) -> None:
    """Асинхронная обработка файла без блокировки webhook."""
    try:
        result = await bot_logic.handle_file(file_path, sender)
        if result:
            await send_message(user_id, result)
    except Exception:
        import traceback
        error_text = traceback.format_exc()
        logger.error(f"Ошибка при обработке файла:\n{error_text}")
        await send_message(
            user_id, "Ошибка обработки файла. Обратитесь к администратору."
        )


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


async def send_message(user_id: int, text: str) -> int | None:
    """Отправка сообщения через API MAX."""
    url = f"{MAX_BASE_URL}/messages"
    headers = {
        "Authorization": MAX_API_TOKEN,
        "Content-Type": "application/json"
    }
    params = {"user_id": user_id}
    payload = {"text": text}

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                url, headers=headers, params=params, json=payload
            )
            if response.status_code != 200:
                logger.error(
                    f"Ошибка отправки: "
                    f"{response.status_code} — {response.text}"
                )
            return response.status_code
        except Exception as e:
            logger.error(f"Исключение при отправке: {e}")
            return None


BUTTONS = [
    {"text": "Чат с ИИ", "command": "gigachat"},
    {"text": "Анализ файлов", "command": "file"},
    {"text": "Изображения", "command": "edit"},
    {"text": "ИИ Агент", "command": "guestrag"},
    {"text": "Настройки", "command": "settings"},
    {"text": "Оплата", "command": "billing"},
]


async def send_inline_message(
        user_id: int, text: str, buttons: list[dict] = None
) -> int | None:
    """Отправка сообщения с инлайн кнопками через API MAX."""
    if buttons is None:
        buttons = BUTTONS
    url = f"{MAX_BASE_URL}/messages"
    headers = {
        "Authorization": MAX_API_TOKEN,
        "Content-Type": "application/json"
    }
    params = {"user_id": user_id}

    rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    keyboard_buttons = [
        [
            {
                "type": "callback",
                "text": btn["text"],
                "payload": btn["command"]
            }
            for btn in row
        ]
        for row in rows
    ]

    payload = {
        "text": text,
        "attachments": [{
            "type": "inline_keyboard",
            "payload": {
                "buttons": keyboard_buttons
            }
        }]
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                url, headers=headers, params=params, json=payload
            )
            if response.status_code != 200:
                logger.error(
                    f"Ошибка отправки: "
                    f"{response.status_code} — {response.text}"
                )
            return response.status_code
        except Exception as e:
            logger.error(f"Исключение при отправке: {e}")
            return None


def verify_webhook_secret(
        payload_body: bytes,
        secret_header: str | None
) -> bool:
    """Проверка подлинности webhook по secret. WEBHOOK_SECRET обязателен."""
    if not WEBHOOK_SECRET:
        logger.error("WEBHOOK_SECRET не задан в .env — webhook отключён")
        return False
    if not secret_header:
        return False
    # MAX API отправляет секрет в plain text, а не хеш
    return secret_header == WEBHOOK_SECRET


async def subscribe_webhook() -> None:
    """Создание webhook-подписки через POST /subscriptions."""
    if not MAX_API_TOKEN:
        logger.critical("MAX_API_TOKEN не задан в .env!")
        raise RuntimeError("MAX_API_TOKEN is required")
    if not MAX_BASE_URL:
        logger.critical("MAX_BASE_URL не задан в .env!")
        raise RuntimeError("MAX_BASE_URL is required")

    url = f"{MAX_BASE_URL}/subscriptions"
    headers = {
        "Authorization": MAX_API_TOKEN,
        "Content-Type": "application/json"
    }
    payload = {
        "url": WEBHOOK_URL,
        "update_types": ["message_created", "message_callback"],
    }
    if WEBHOOK_SECRET:
        payload["secret"] = WEBHOOK_SECRET

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code == 200:
                logger.info(f"Webhook подписка создана: {WEBHOOK_URL}")
            else:
                logger.error(
                    f"Ошибка создания webhook: {response.status_code}"
                    f" — {response.text}"
                )
        except Exception as e:
            logger.error(f"Исключение при создании webhook: {e}")


async def get_subscriptions() -> dict:
    """Просмотр текущих подписок."""
    url = f"{MAX_BASE_URL}/subscriptions"
    headers = {"Authorization": MAX_API_TOKEN}
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        return response.json()


async def delete_subscription(subscription_id: int = None) -> dict:
    """Удаление webhook-подписки."""
    url = f"{MAX_BASE_URL}/subscriptions"
    headers = {"Authorization": MAX_API_TOKEN}
    params = {"subscription_id": subscription_id} if subscription_id else {}
    async with httpx.AsyncClient() as client:
        response = await client.delete(url, headers=headers, params=params)
        return response.json()


async def process_update(
        update: dict,
        request: Request,
        background_tasks: BackgroundTasks = None
) -> None:
    """Обработка одного обновления."""
    update_type = update.get("update_type")

    if update_type == "message_callback":
        callback_obj = update.get("callback", {})
        sender = callback_obj.get("user", {})
        user_id = sender.get("user_id")
        callback_data = callback_obj.get("payload", "")
        logger.info(f"Callback от {sender.get('name')}: {callback_data}")
        if callback_data and user_id:
            command_response = await bot_logic.handle_command(
                "/" + callback_data, sender
            )
            if command_response is not None:
                await send_message(user_id, command_response)
        return

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
    sender = message.get("sender", {})
    body = message.get("body", {})

    user_id = sender.get("user_id")
    user_text = body.get("text", "")

    if sender.get("is_bot"):
        return  # возврат если сообщение от бота

    if not _check_rate_limit(user_id):  # вовзврат если слишком много запросов
        logger.warning(f"Rate limit превышен для user_id={user_id}")
        await send_message(
            user_id, "Превышен лимит запросов. Попробуйте через минуту."
        )
        return
    # создаем пользователя если его нет в базе
    # (и базу с таблицами), все проверки уже есть в db
    nickname = sender.get("name", f"user_{user_id}")
    if await db.create_user(user_id, nickname):
        logger.info(f"Создан пользователь: {nickname} (id={user_id})")
    user_data = await db.get_user(user_id)
    permission = user_data["permission"]
    print(nickname, permission)
    # ─── Обработка вложений ───
    # если есть вложения
    # то проверяем их на соответсвие типа выбранному режиму user_modes
    # и сохраняем их в папке temp, если тип подходит под режим
    # и отправляем на обработку в bot_logic
    attachments = body.get("attachments", [])
    attr_url = None
    for att in attachments:
        attr_url = att.get("payload", {}).get("url")
        if att.get("type") == "audio":
            voice_url = attr_url
            if voice_url and not user_text:
                logger.info(
                    f"Голосовое сообщение от {sender.get('name')} "
                    f"(user_id={user_id})"
                )
                await send_message(user_id, "Не распознаю голосовое ...")
                return
            break
        if att.get("type") == "image" and attr_url:
            # image_path = await save_user_image(attr_url, user_id)
            pass
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
                return await send_message(
                    user_id,
                    f"Не удалось загрузить файл: {filename}"
                )
            await send_message(user_id, "Файл получен. Начинаю обработку...")
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

    if not user_id or not user_text:  # возврат если нет id или текста
        logger.warning(f"Пропущено: user_id={user_id}, text={user_text}")
        return

    # если команда то отправляем ее на обработку в bot_logic
    # за исключением /start, там выводим кнопки
    if user_text.startswith("/"):
        command_parts = user_text.split(maxsplit=1)
        command = command_parts[0].lower()
        if command == "/start" and permission != 1:
            text = "Большой блок информационного текста"
            await send_inline_message(user_id, text)
            return
        else:
            command_response = await bot_logic.handle_command(
                user_text, sender
            )
        if command_response is not None:  # если есть ответ о выводим его
            await send_message(user_id, command_response)
            return

    try:
        reply_text = await bot_logic.handle_message(request, user_text, sender)

        if not reply_text:
            logger.error(
                f"Пустой ответ для пользователя: {user_id} "
                f"для запроса: {user_text}"
            )
            await send_message(
                user_id, "Извините, не смог сформировать ответ."
            )
            return

        if len(reply_text) > 4000:
            reply_text = reply_text[:3997] + "..."

        await send_message(user_id, reply_text)
    except Exception as e:
        logger.error(f"Ошибка при обработке сообщения: {e}", exc_info=True)
        await send_message(user_id, f"Произошла ошибка: {str(e)}")


async def handle_webhook(request) -> tuple[bool, dict | None]:
    """
    Обработка входящего webhook-запроса.
    Возвращает (успех, данные) или вызывает HTTPException.
    """
    from fastapi import HTTPException

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
    if not verify_webhook_secret(body, secret_header):
        logger.warning("Неверный X-Max-Bot-Api-Secret — запрос отклонён")
        raise HTTPException(status_code=403, detail="Invalid secret")

    try:
        data = await request.json()
    except Exception:
        logger.error("Не удалось распарсить JSON webhook")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    logger.info(f"Webhook payload: {data.get('update_type', 'unknown')}")
    return True, data
