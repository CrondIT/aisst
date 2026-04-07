from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import httpx
import uvicorn
import asyncio
import logging
import os
import hashlib
from global_state import MAX_API_TOKEN, MAX_BASE_URL, GIGACHAT_API_KEY
from gigachat import GigaChat
from concurrent.futures import ThreadPoolExecutor

app = FastAPI()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

logger.info(
    f"MAX_API_TOKEN: {'*' + MAX_API_TOKEN[-10:] if MAX_API_TOKEN else 'None'}"
)
logger.info(f"MAX_BASE_URL: {MAX_BASE_URL}")

# ─── Настройки webhook ───
# URL, на который MAX будет присылать обновления (строго HTTPS, порт 443)
# Для продакшена укажите реальный домен,
# например: https://mybot.example.com/webhook
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
# Секрет для проверки подлинности webhook (задайте в .env или оставьте пустым)
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

giga = GigaChat(
    credentials=GIGACHAT_API_KEY,
    scope="GIGACHAT_API_PERS",
    model="GigaChat",
    ca_bundle_file="russian_trusted_root_ca_pem.crt",
)

# ThreadPoolExecutor для запуска sync GigaChat в отдельном потоке
executor = ThreadPoolExecutor(max_workers=10)


async def send_message(user_id: int, text: str):
    """Отправка сообщения через API MAX"""
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
                    f"Ошибка отправки:{response.status_code} — {response.text}"
                )
            return response.status_code
        except Exception as e:
            logger.error(f"Исключение при отправке: {e}")
            return None


def verify_webhook_secret(
        payload_body: bytes,
        secret_header: str | None
) -> bool:
    """Проверка подлинности webhook по secret (если WEBHOOK_SECRET задан)"""
    if not WEBHOOK_SECRET:
        return True  # secret не настроен — пропускаем
    if not secret_header:
        return False
    expected = hashlib.sha256(
        (WEBHOOK_SECRET + payload_body.decode("utf-8")).encode("utf-8")
    ).hexdigest()
    return secret_header == expected


async def process_update(update: dict):
    """Обработка одного обновления"""
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

    if not user_id or not user_text:
        logger.warning(f"Пропущено: user_id={user_id}, text={user_text}")
        return

    logger.info(f"Сообщение от {sender.get('name')}: {user_text}")

    # Вызываем GigaChat в отдельном потоке (он синхронный)
    loop = asyncio.get_event_loop()
    answer = await loop.run_in_executor(executor, lambda: giga.chat(user_text))

    reply_text = answer.choices[0].message.content

    # Обрезаем если ответ > 4000 символов (лимит MAX API)
    if len(reply_text) > 4000:
        reply_text = reply_text[:3997] + "..."

    await send_message(user_id, reply_text)


# ─── Webhook endpoint ───
@app.post("/webhook")
async def webhook(request: Request):
    """Endpoint для приёма webhook-обновлений от MAX"""
    body = await request.body()

    # Проверка secret
    secret_header = request.headers.get("X-Max-Bot-Api-Secret")
    if not verify_webhook_secret(body, secret_header):
        logger.warning("Неверный X-Max-Bot-Api-Secret — запрос отклонён")
        raise HTTPException(status_code=403, detail="Invalid secret")

    try:
        data = await request.json()
    except Exception:
        logger.error("Не удалось распарсить JSON webhook")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    logger.info(f"Webhook payload: {data.get('update_type', 'unknown')}")

    # Платформа может присылать один Update или массив
    if isinstance(data, list):
        for update in data:
            await process_update(update)
    else:
        await process_update(data)

    # Обязательно вернуть 200 OK в течение 30 секунд
    return JSONResponse(content={"status": "ok"})


# ─── Health check ───
@app.get("/")
async def health_check():
    return {"status": "ok", "webhook_url": WEBHOOK_URL or "not set"}


# ─── Управление подписками ───
@app.on_event("startup")
async def startup():
    """При старте — создаём webhook-подписку (если WEBHOOK_URL задан)"""
    if not WEBHOOK_URL:
        logger.warning(
            "WEBHOOK_URL не задан. Webhook НЕ активирован. "
            "Установите WEBHOOK_URL в .env или используйте Long Polling."
        )
        return

    await subscribe_webhook()


async def subscribe_webhook():
    """Создание webhook-подписки через POST /subscriptions"""
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
                    f"Ошибка создания webhook: {response.status_code} — {response.text}"
                )
        except Exception as e:
            logger.error(f"Исключение при создании webhook: {e}")


@app.get("/subscriptions")
async def get_subscriptions():
    """Просмотр текущих подписок"""
    url = f"{MAX_BASE_URL}/subscriptions"
    headers = {"Authorization": MAX_API_TOKEN}
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        return response.json()


@app.delete("/subscriptions")
async def delete_subscription(subscription_id: int = None):
    """Удаление webhook-подписки"""
    url = f"{MAX_BASE_URL}/subscriptions"
    headers = {"Authorization": MAX_API_TOKEN}
    if subscription_id is not None:
        params = {"subscription_id": subscription_id}
    else:
        params = {}
    async with httpx.AsyncClient() as client:
        response = await client.delete(url, headers=headers, params=params)
        return response.json()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
