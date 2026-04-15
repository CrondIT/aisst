"""Модуль для взаимодействия с MAX API."""

import httpx
import asyncio
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse
from global_state import (
    MAX_API_TOKEN,
    MAX_BASE_URL,
    WEBHOOK_URL,
    WEBHOOK_SECRET,
    TRUSTED_WEBHOOK_IPS,
    RATE_LIMIT_PER_MINUTE,
)
import bot_logic
import db
from utils import logger

# ThreadPoolExecutor для запуска sync GigaChat в отдельном потоке
executor = ThreadPoolExecutor(max_workers=10)

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
    url = f"{MAX_BASE_URL}/subscriptions"
    headers = {
        "Authorization": MAX_API_TOKEN,
        "Content-Type": "application/json"
    }
    payload = {
        "url": WEBHOOK_URL,
        "update_types": ["message_created"],
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


async def process_update(update: dict, giga_client) -> None:
    """Обработка одного обновления."""
    if update.get("update_type") != "message_created":
        return

    message = update.get("message", {})
    sender = message.get("sender", {})
    body = message.get("body", {})

    user_id = sender.get("user_id")
    user_text = body.get("text", "")

    # Игнорируем сообщения от самого бота
    if sender.get("is_bot"):
        return

    # Проверяем, не голосовое ли это сообщение (audio attachment)
    attachments = body.get("attachments", [])
    voice_url = None
    voice_ext = ".ogg"
    for att in attachments:
        if att.get("type") == "audio":
            payload = att.get("payload", {})
            voice_url = payload.get("url")
            # Попробуем определить расширение из URL
            if voice_url:
                parsed = urlparse(voice_url)
                path = parsed.path
                if "." in path:
                    voice_ext = "." + path.rsplit(".", 1)[1].lower()
            break

    if voice_url and not user_text:
        logger.info(
            f"Голосовое сообщение от {sender.get('name')} (user_id={user_id})"
        )
        await send_message(user_id, "Не распознаю голосовое сообщение...")

        return

    if not user_id or not user_text:
        logger.warning(f"Пропущено: user_id={user_id}, text={user_text}")
        return

    # ─── Rate limiting ───
    if not _check_rate_limit(user_id):
        logger.warning(f"Rate limit превышен для user_id={user_id}")
        await send_message(
            user_id, "Превышен лимит запросов. Попробуйте через минуту."
        )
        return

    logger.info(f"Сообщение от {sender.get('name')}: {user_text}")

    # ─── Создание пользователя в БД, если не существует ───
    # в db.create_user уже встроена проверка существования пользователя
    nickname = sender.get("name", f"user_{user_id}")
    if await db.create_user(user_id, nickname):
        logger.info(f"Создан пользователь: {nickname} (id={user_id})")

    # Проверяем, не команда ли это
    command_response = await bot_logic.handle_command(user_text, sender)
    if command_response is not None:
        await send_message(user_id, command_response)
        return

    try:
        # Вызываем GigaChat в отдельном потоке (он синхронный)
        answer = await asyncio.get_running_loop().run_in_executor(
            executor,
            lambda: giga_client.chat(user_text)
        )

        if not answer or not answer.choices:
            logger.error(
                f"GigaChat вернул пустой ответ для запроса: {user_text}"
            )
            await send_message(
                user_id, "Извините, не смог сформировать ответ."
            )
            return

        reply_text = answer.choices[0].message.content

        # Обрезаем если ответ > 4000 символов (лимит MAX API)
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
