import httpx
import os
import uuid
import time
import asyncio
from typing import Optional
from loguru import logger
from global_state import (
    RUS_TRUSTED_ROOT_CA_PEM,
    SALUTE_SPEECH_AUTH_KEY,
    SALUTE_SPEECH_OAUTH_URL,
    SALUTE_SPEECH_BASE_URL,
)


# Кэш токена
_cached_token: Optional[str] = None
_token_expires_at: float = 0


def _get_ssl_verify():
    """Возвращает путь к сертификату или False, если файл не найден."""
    if RUS_TRUSTED_ROOT_CA_PEM and os.path.exists(RUS_TRUSTED_ROOT_CA_PEM):
        return RUS_TRUSTED_ROOT_CA_PEM
    logger.warning("SSL сертификат не найден. Проверка SSL отключена.")
    return False


def _get_client() -> httpx.AsyncClient:
    """Создает httpx клиент с настройками SSL."""
    return httpx.AsyncClient(verify=_get_ssl_verify())


async def get_access_token() -> str:
    """Получает Access Token для SaluteSpeech API с кэшированием."""
    global _cached_token, _token_expires_at

    # Если токен еще валиден (оставляем запас 60 сек), возвращаем его
    if _cached_token and time.time() < _token_expires_at - 60:
        return _cached_token
    if not SALUTE_SPEECH_AUTH_KEY:
        logger.error("SALUTE_SPEECH_AUTH_KEY не задан в .env")
        raise ValueError("SALUTE_SPEECH_AUTH_KEY не задан в .env")

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "RqUID": str(uuid.uuid4()),
        "Authorization": f"Basic {SALUTE_SPEECH_AUTH_KEY}",
    }
    # Scope: SALUTE_SPEECH_PERS (физлица) или SALUTE_SPEECH_CORP (юрлица)
    payload = {"scope": "SALUTE_SPEECH_PERS"}

    async with _get_client() as client:
        try:
            response = await client.post(
                SALUTE_SPEECH_OAUTH_URL, headers=headers, data=payload
            )

            if response.status_code != 200:
                # Логируем тело ответа при ошибке для диагностики
                error_text = response.text
                logger.error(
                    f"Ошибка получения токена: {response.status_code} - "
                    f"{error_text}"
                )
                raise Exception(f"SaluteSpeech Auth Error: {error_text}")

            response.raise_for_status()
            data = response.json()

            _cached_token = data["access_token"]
            # expires_at в миллисекундах, переводим в секунды
            _token_expires_at = data["expires_at"] / 1000

            return _cached_token

        except Exception as e:
            logger.error(f"Исключение при запросе токена: {e}")
            raise


async def create_recognition_task(file_path: str, token: str) -> str:
    """Создает задачу на распознавание аудио и возвращает task_id."""
    url = (
        f"{SALUTE_SPEECH_BASE_URL}/speech:recognize?model=general&language=ru-RU"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "audio/ogg;codecs=opus",
    }

    async with _get_client() as client:
        with open(file_path, "rb") as f:
            response = await client.post(
                url, headers=headers, content=f.read()
            )

        if response.status_code != 200:
            logger.error(
                f"Ошибка создания задачи: {response.status_code} - "
                f"{response.text}"
            )
            response.raise_for_status()

        data = response.json()
        # Согласно документации SaluteSpeech: result.task_id
        return data["result"]["task_id"]


async def wait_for_task_completion(
        task_id: str, token: str, timeout: int = 60
    ) -> dict:
    """Ожидает завершения задачи и возвращает результат."""
    url = f"{SALUTE_SPEECH_BASE_URL}/task:{task_id}"
    headers = {"Authorization": f"Bearer {token}"}

    start_time = time.time()

    async with _get_client() as client:
        while time.time() - start_time < timeout:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            task_data = response.json()

            # Согласно документации SaluteSpeech, статус внутри result
            status = task_data.get("result", {}).get("status") or task_data.get(
                "status"
            )
            if status == "done":
                return task_data
            elif status == "error":
                raise Exception(
                    f"Ошибка распознавания: {task_data.get('error')}"
                )

            await asyncio.sleep(2)

    raise TimeoutError("Превышено время ожидания результата распознавания")


async def transcribe_audio(file_path: str) -> str:
    """
    Основная функция для преобразования аудио в текст.
    :param file_path: Путь к аудиофайлу
    :return: Распознанный текст
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Файл не найден: {file_path}")

    token = await get_access_token()

    # Прямая отправка аудио
    url = (
        f"{SALUTE_SPEECH_BASE_URL}/speech:recognize?model=general&language=ru-RU"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "audio/ogg;codecs=opus",
    }

    async with _get_client() as client:
        with open(file_path, "rb") as f:
            response = await client.post(
                url, headers=headers, content=f.read()
            )

        if response.status_code != 200:
            logger.error(
                f"Ошибка распознавания: {response.status_code} - "
                f"{response.text}"
            )
            response.raise_for_status()

        data = response.json()
        return parse_speech_response(data)


def parse_speech_response(data: dict) -> str:
    """Парсит ответ от SaluteSpeech API."""
    result = data.get("result")

    # Вариант 1: Синхронный ответ (список строк)
    if isinstance(result, list):
        text_parts = [str(item).strip() for item in result if item]
        return " ".join(text_parts).strip()

    # Вариант 2: Асинхронный ответ (словарь с task_id)
    if isinstance(result, dict):
        # Если есть task_id, значит нужно ждать завершения
        if "task_id" in result:
            # Этот случай пока оставляем для обратной совместимости
            # В реальности сейчас API возвращает результат сразу
            pass

        # Парсинг финального результата (если пришел сразу)
        # Список слов (words)
        if "words" in result:
            words = result["words"]
            text = " ".join([w.get("word", "") for w in words])
            return text.strip()

        # Список сегментов (segments)
        if "segments" in result:
            segments = result["segments"]
            text = " ".join([seg.get("text", "") for seg in segments])
            return text.strip()

        # Прямой текст
        if "text" in result:
            return result["text"].strip()

    # Если ничего не подошло
    logger.warning(f"Неизвестный формат результата: {data}")
    return str(data)
