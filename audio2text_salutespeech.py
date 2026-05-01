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

# Конфигурация с значениями по умолчанию
OAUTH_URL = SALUTE_SPEECH_OAUTH_URL or "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
BASE_URL = SALUTE_SPEECH_BASE_URL or "https://smartspeech.sber.ru/rest/v1"

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
    print("SALUTE_SPEECH_AUTH_KEY ", SALUTE_SPEECH_AUTH_KEY)
    if not SALUTE_SPEECH_AUTH_KEY:
        raise ValueError("SALUTE_SPEECH_AUTH_KEY не задан в .env")

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "RqUID": str(uuid.uuid4()),
        "Authorization": f"Basic {SALUTE_SPEECH_AUTH_KEY}"
    }
    # Scope: SALUTE_SPEECH_PERS (физлица) или SALUTE_SPEECH_CORP (юрлица)
    payload = {"scope": "SALUTE_SPEECH_PERS"}

    async with _get_client() as client:
        try:
            logger.info(f"Запрос токена SaluteSpeech: {OAUTH_URL}")
            response = await client.post(OAUTH_URL, headers=headers, data=payload)
            
            if response.status_code != 200:
                # Логируем тело ответа при ошибке для диагностики
                error_text = response.text
                logger.error(f"Ошибка получения токена: {response.status_code} - {error_text}")
                raise Exception(f"SaluteSpeech Auth Error: {error_text}")
            
            response.raise_for_status()
            data = response.json()
            
            _cached_token = data["access_token"]
            # expires_at в миллисекундах, переводим в секунды
            _token_expires_at = data["expires_at"] / 1000
            
            logger.info("Токен SaluteSpeech успешно получен")
            return _cached_token
            
        except Exception as e:
            logger.error(f"Исключение при запросе токена: {e}")
            raise


async def upload_file(file_path: str, token: str) -> str:
    """Загружает аудиофайл на сервер SaluteSpeech и возвращает data_id."""
    url = f"{BASE_URL}/data/upload"
    headers = {"Authorization": f"Bearer {token}"}

    async with _get_client() as client:
        with open(file_path, "rb") as f:
            files = {"file": (os.path.basename(file_path), f, "audio/ogg")}
            response = await client.post(url, headers=headers, files=files)
            
            if response.status_code != 200:
                logger.error(f"Ошибка загрузки файла: {response.status_code} - {response.text}")
                response.raise_for_status()
            
            return response.json()["data_id"]


async def create_recognition_task(data_id: str, token: str) -> str:
    """Создает задачу на распознавание аудио и возвращает task_id."""
    url = f"{BASE_URL}/speech:recognize"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "data_id": data_id,
        "model": "general",
        "language": "ru-RU"
    }

    async with _get_client() as client:
        response = await client.post(url, headers=headers, json=payload)
        
        if response.status_code != 200:
            logger.error(f"Ошибка создания задачи: {response.status_code} - {response.text}")
            response.raise_for_status()
            
        return response.json()["task_id"]


async def wait_for_task_completion(task_id: str, token: str, timeout: int = 60) -> dict:
    """Ожидает завершения задачи и возвращает результат."""
    url = f"{BASE_URL}/task/{task_id}"
    headers = {"Authorization": f"Bearer {token}"}
    
    start_time = time.time()
    
    async with _get_client() as client:
        while time.time() - start_time < timeout:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            task_data = response.json()
            
            status = task_data.get("status")
            if status == "done":
                return task_data
            elif status == "error":
                raise Exception(f"Ошибка распознавания: {task_data.get('error')}")
            
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
    
    logger.info(f"Загрузка файла {file_path} в SaluteSpeech...")
    data_id = await upload_file(file_path, token)
    
    logger.info(f"Создание задачи распознавания для data_id: {data_id}")
    task_id = await create_recognition_task(data_id, token)
    
    logger.info(f"Ожидание результата задачи: {task_id}")
    result = await wait_for_task_completion(task_id, token)
    
    # Парсинг результата SaluteSpeech
    try:
        res_data = result.get("result", {})
        
        # Вариант 1: Список слов (words)
        if "words" in res_data:
            words = res_data["words"]
            text = " ".join([w.get("word", "") for w in words])
            return text.strip()
        
        # Вариант 2: Список сегментов (segments)
        if "segments" in res_data:
            segments = res_data["segments"]
            text = " ".join([seg.get("text", "") for seg in segments])
            return text.strip()
            
        # Вариант 3: Прямой текст
        if "text" in res_data:
            return res_data["text"].strip()
            
    except Exception as e:
        logger.error(f"Ошибка парсинга результата: {e}")
    
    return str(result)
