from fastapi import FastAPI
import httpx
import uvicorn
import asyncio
import logging
import json
import os
from global_state import MAX_API_TOKEN, MAX_BASE_URL, GIGACHAT_API_KEY
#from mygigachat import gigachat
from gigachat import GigaChat

app = FastAPI()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

logger.info(f"MAX_API_TOKEN: {'***' + MAX_API_TOKEN[-10:] if MAX_API_TOKEN else 'None'}")
logger.info(f"MAX_BASE_URL: {MAX_BASE_URL}")

# Путь для сохранения marker
MARKER_FILE = os.path.join(os.path.dirname(__file__), "last_marker.json")


def load_marker():
    """Загрузка marker из файла"""
    if os.path.exists(MARKER_FILE):
        try:
            with open(MARKER_FILE, "r") as f:
                data = json.load(f)
                return data.get("marker")
        except Exception as e:
            logger.warning(f"Не удалось загрузить marker: {e}")
    return None


def save_marker(marker):
    """Сохранение marker в файл"""
    try:
        with open(MARKER_FILE, "w") as f:
            json.dump({"marker": marker}, f)
    except Exception as e:
        logger.warning(f"Не удалось сохранить marker: {e}")


# Marker для отслеживания прочитанных обновлений (загружается из файла)
last_marker = load_marker()

giga = GigaChat(
   credentials=GIGACHAT_API_KEY,
   scope="GIGACHAT_API_PERS",
   model="GigaChat",
   ca_bundle_file="russian_trusted_root_ca_pem.crt"
)


async def send_message(user_id: int, text: str):
    """Функция отправки сообщения через API MAX"""
    url = f"{MAX_BASE_URL}/messages"
    headers = {
        "Authorization": MAX_API_TOKEN,
        "Content-Type": "application/json"
        }
    params = {"user_id": user_id}
    payload = {"text": text}

    async with httpx.AsyncClient() as client:
        response = await client.post(
            url, headers=headers,
            params=params,
            json=payload
            )
        if response.status_code != 200:
            logger.error(
                f"Ошибка отправки: {response.status_code} — {response.text}"
            )
        return response.status_code


async def get_updates(marker=None):
    """Получение обновлений через Long Polling"""
    global last_marker
    url = f"{MAX_BASE_URL}/updates"
    headers = {
        "Authorization": MAX_API_TOKEN,
        "Content-Type": "application/json"
        }
    params = {
        "limit": 100,
        "timeout": 30,  # время ожидания в секундах
        "types": ["message_created"],  # фильтруем только сообщения
    }
    if marker is not None:
        params["marker"] = marker

    async with httpx.AsyncClient() as client:
        response = await client.get(
            url, headers=headers, params=params, timeout=35
            )
        if response.status_code == 200:
            data = response.json()
            new_marker = data.get("marker")
            if new_marker:
                last_marker = new_marker
                save_marker(last_marker)
            return data.get("updates", [])
        else:
            logger.error(f"Ошибка получения обновл.: {response.status_code}")
            return []


async def poll_updates():
    """Основной цикл Long Polling"""
    global last_marker
    logger.info("Запуск Long Polling...")
    while True:
        try:
            updates = await get_updates(last_marker)
            if updates:
                logger.info(f"Получено обновлений: {len(updates)}")
                for update in updates:
                    if update.get("update_type") == "message_created":
                        message = update.get("message", {})
                        sender = message.get("sender", {})
                        body = message.get("body", {})

                        user_id = sender.get("user_id")
                        user_text = body.get("text", "")

                        if user_id and user_text:
                            logger.info(
                                f"Сообщение от {sender.get('name')}: {user_text}"
                            )
                            # Эхо-ответ

                            # answer = await gigachat.ask(user_text)
                            answer = giga.chat(user_text)

                            await send_message(
                                user_id,
                                answer.choices[0].message.content,
                                )
                        else:
                            logger.warning(
                                f"Пропущено: user_id={user_id}, text={user_text}"
                            )
            elif last_marker is not None:
                # Маркер обновился, но новых сообщений нет — сохраняем
                save_marker(last_marker)
        except Exception as e:
            logger.error(f"Ошибка в цикле polling: {e}")
            await asyncio.sleep(5)  # пауза при ошибке

        await asyncio.sleep(1)  # небольшая пауза между запросами


@app.on_event("startup")
async def startup_event():
    """Запуск Long Polling при старте приложения"""
    asyncio.create_task(poll_updates())


@app.get("/")
async def health_check():
    """Проверка работоспособности"""
    return {"status": "ok", "marker": last_marker}


# if __name__ == "__main__":
#    uvicorn.run(app, host="0.0.0.0", port=8000)
