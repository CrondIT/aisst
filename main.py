from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import httpx
import uvicorn
import asyncio
import logging
import os
from global_state import (
    MAX_API_TOKEN,
    MAX_BASE_URL,
    GIGACHAT_API_KEY,
    WEBHOOK_URL,
    WEBHOOK_SECRET,
)
from gigachat import GigaChat
from concurrent.futures import ThreadPoolExecutor

# ThreadPoolExecutor для запуска sync GigaChat в отдельном потоке
executor = ThreadPoolExecutor(max_workers=10)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan-событие: замена устаревшему @app.on_event('startup')"""
    logger.info(f"Startup: WEBHOOK_URL={WEBHOOK_URL!r}")
    logger.info(f"Startup: WEBHOOK_SECRET={'***' if WEBHOOK_SECRET else '(пусто)'}")

    # Startup
    if WEBHOOK_URL:
        await subscribe_webhook()
    else:
        logger.warning(
            "WEBHOOK_URL не задан. Webhook НЕ активирован. "
            "Установите WEBHOOK_URL в .env или используйте Long Polling."
        )
    yield
    # Shutdown (при необходимости можно добавить очистку)


app = FastAPI(lifespan=lifespan)

# Статические файлы
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

logger.info(
    f"MAX_API_TOKEN: {'*' + MAX_API_TOKEN[-10:] if MAX_API_TOKEN else 'None'}"
)
logger.info(f"MAX_BASE_URL: {MAX_BASE_URL}")


giga = GigaChat(
    credentials=GIGACHAT_API_KEY,
    scope="GIGACHAT_API_PERS",
    model="GigaChat",
    ca_bundle_file="russian_trusted_root_ca_pem.crt",
)


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
    # MAX API отправляет секрет в plain text, а не хеш
    return secret_header == WEBHOOK_SECRET


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

    try:
        # Вызываем GigaChat в отдельном потоке (он синхронный)
        answer = await asyncio.get_running_loop().run_in_executor(
            executor,
            lambda: giga.chat(user_text)
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


# ─── Webhook endpoint ───
@app.post("/webhook")
async def webhook(request: Request):
    """Endpoint для приёма webhook-обновлений от MAX"""
    logger.info(f"=== Входящий webhook запрос ===")
    logger.info(f"Method: {request.method}, URL: {request.url}")
    logger.info(f"Headers: {dict(request.headers)}")

    body = await request.body()
    logger.info(f"Body: {body.decode('utf-8', errors='replace')}")

    # Проверка secret
    secret_header = request.headers.get("X-Max-Bot-Api-Secret")
    logger.info(f"X-Max-Bot-Api-Secret header: '{secret_header}'")
    logger.info(f"WEBHOOK_SECRET из .env: '{WEBHOOK_SECRET}'")
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


# ─── Health check / Главная страница ───
@app.get("/")
async def health_check():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path)
    return {"status": "ok", "webhook_url": WEBHOOK_URL or "not set"}


# ─── Управление подписками ───

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
                    f"Ошибка создания webhook: {response.status_code}"
                    f" — {response.text}"
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
    # Для запуска через gunicorn на Unix socket:
    # g gunicorn -w 1 -k uvicorn.workers.UvicornWorker 
    # main:app --bind unix:/tmp/fastapi.sock --umask 000
    uvicorn.run(app, host="0.0.0.0", port=8000)
