"""Модуль для взаимодействия с MAX API (отправка, подписки)."""

import httpx
from global_state import (
    MAX_API_TOKEN,
    MAX_BASE_URL,
    WEBHOOK_URL,
    WEBHOOK_SECRET,
)
from utils import logger, split_long_message
import asyncio


async def send_message(user_id: int, text: str) -> int | None:
    """Отправка сообщения через API MAX. 
       Автоматически разбивает текст на части, если он длиннее 4000 символов.
    """
    url = f"{MAX_BASE_URL}/messages"
    headers = {
        "Authorization": MAX_API_TOKEN,
        "Content-Type": "application/json"
    }
    params = {"user_id": user_id}
    
    # Разбиваем сообщение на части
    parts = split_long_message(text, MESSAGE_LIMIT=4000)
    
    async with httpx.AsyncClient() as client:
        last_status = None
        for i, part in enumerate(parts):
            payload = {"text": part}
            try:
                response = await client.post(
                    url, headers=headers, params=params, json=payload
                )
                last_status = response.status_code
                if response.status_code != 200:
                    logger.error(
                        f"Ошибка отправки части {i+1}/{len(parts)}: "
                        f"{response.status_code} — {response.text}"
                    )
            except Exception as e:
                logger.error(
                    f"Исключение при отправке части {i+1}/{len(parts)}: {e}"
                )
                last_status = None
            
            # Пауза между отправками для избежания rate limit
            if i < len(parts) - 1:
                await asyncio.sleep(0.1)
        
        return last_status


BUTTONS = [
    {"text": "Чат с ИИ", "command": "/gigachat"},
    {"text": "Анализ файлов", "command": "/file"},
    {"text": "Изображения", "command": "/edit"},
    {"text": "ИИ Агент", "command": "/aiagent"},
    {"text": "Настройки", "command": "/settings"},
    {"text": "Оплата", "command": "/billing"},
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
