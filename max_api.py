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


async def send_message(
    user_id: int, text: str, format: str = None
) -> int | None:
    """Отправка сообщения через API MAX.
       Автоматически разбивает текст на части, если он длиннее 4000 символов.
       
       Args:
           user_id: ID пользователя
           text: Текст сообщения
           format: Формат текста ("markdown", "html" или None для plain text)
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
            if format in ("markdown", "html"):
                payload["format"] = format
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


async def send_image(
    user_id: int,
    image_url: str,
    caption: str = None,
) -> int | None:
    """
    Отправка изображения через API MAX.
    
    Args:
        user_id: ID пользователя
        image_url: URL изображения (должен быть доступен из интернета)
        caption: Подпись к изображению (опционально)
    
    Returns:
        HTTP status code или None при ошибке
    """
    url = f"{MAX_BASE_URL}/messages"
    headers = {
        "Authorization": MAX_API_TOKEN,
        "Content-Type": "application/json"
    }
    params = {"user_id": user_id}
    
    # Формируем payload с медиа-группой
    payload = {
        "attachments": [
            {
                "type": "image",
                "payload": {
                    "url": image_url,
                }
            }
        ]
    }
    
    # Добавляем caption если есть
    if caption:
        payload["text"] = caption
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                url, headers=headers, params=params, json=payload
            )
            if response.status_code != 200:
                logger.error(
                    f"Ошибка отправки изображения: "
                    f"{response.status_code} — {response.text}"
                )
            return response.status_code
        except Exception as e:
            logger.error(f"Исключение при отправке изображения: {e}")
            return None


async def send_inline_message(
        user_id: int, text: str, buttons: list[dict],
        format: str = None
) -> int | None:
    """Отправка сообщения с инлайн кнопками через API MAX.
    
    Args:
        user_id: ID пользователя
        text: Текст сообщения
        buttons: Список кнопок (обязателен, импортируйте из keyboards.py)
        format: Формат текста ("markdown", "html" или None для plain text)
    """
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

    if format in ("markdown", "html"):
        payload["format"] = format

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
    """Создание webhook-подписки через POST /subscriptions.
    Перед созданием удаляет существующие подписки с тем же URL,
    чтобы избежать дублирующихся уведомлений.
    """
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

    # Удаляем существующие подписки с тем же URL (защита от дубликатов)
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                subscriptions = response.json()
                # subscriptions может быть списком или dict с ключом subscriptions
                if isinstance(subscriptions, dict):
                    subs_list = subscriptions.get("subscriptions", [])
                elif isinstance(subscriptions, list):
                    subs_list = subscriptions
                else:
                    subs_list = []

                deleted_count = 0
                for sub in subs_list:
                    sub_url = sub.get("url", "")
                    sub_id = sub.get("id") or sub.get("subscription_id")
                    if sub_url == WEBHOOK_URL and sub_id:
                        del_resp = await client.delete(
                            url, headers=headers, params={"subscription_id": sub_id}
                        )
                        if del_resp.status_code == 200:
                            logger.info(
                                f"Удалена старая подписка {sub_id} для {WEBHOOK_URL}"
                            )
                            deleted_count += 1
                        else:
                            logger.warning(
                                f"Не удалось удалить подписку {sub_id}: "
                                f"{del_resp.status_code}"
                            )

                if deleted_count > 0:
                    logger.info(f"Удалено {deleted_count} старых подписок")
            else:
                logger.warning(
                    f"Не удалось получить подписки: {response.status_code}"
                )
    except Exception as e:
        logger.warning(f"Ошибка при очистке старых подписок: {e}")

    # Создаём новую подписку
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


async def upload_file(
    file_data: bytes,
    filename: str,
    file_type: str = "file"
) -> dict | None:
    """
    Загрузка файла в MAX и получение токена для отправки.
    
    Этапы:
    1. POST /uploads?type={file_type} → получение URL загрузки
    2. POST {upload_url} с файлом → получение token
    
    Args:
        file_data: Бинарные данные файла
        filename: Имя файла
        file_type: Тип файла: "file", "image", "video", "audio"
    
    Returns:
        dict с ключами "token" и "type" для использования в сообщении,
        или None при ошибке
    """
    # Шаг 1: Получаем URL для загрузки
    upload_url_endpoint = f"{MAX_BASE_URL}/uploads"
    headers = {"Authorization": MAX_API_TOKEN}
    params = {"type": file_type}
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                upload_url_endpoint, 
                headers=headers, 
                params=params
            )
            
            if response.status_code != 200:
                logger.error(
                    f"Ошибка получения URL загрузки: "
                    f"{response.status_code} — {response.text}"
                )
                return None
            
            upload_data = response.json()
            upload_url = upload_data.get("url")
            
            if not upload_url:
                logger.error("Не получен URL для загрузки файла")
                return None
            
            # Шаг 2: Загружаем файл по полученному URL
            # Определяем MIME-тип по расширению
            mime_types = {
                "pdf": "application/pdf",
                "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "doc": "application/msword",
                "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "xls": "application/vnd.ms-excel",
                "rtf": "application/rtf",
                "txt": "text/plain",
                "png": "image/png",
                "jpg": "image/jpeg",
                "jpeg": "image/jpeg",
                "gif": "image/gif",
                "mp4": "video/mp4",
                "mp3": "audio/mpeg",
                "wav": "audio/wav",
            }
            
            ext = filename.split(".")[-1].lower() if "." in filename else ""
            content_type = mime_types.get(ext, "application/octet-stream")
            
            # Загружаем файл через multipart/form-data
            files = {
                "data": (filename, file_data, content_type)
            }
            upload_headers = {"Authorization": MAX_API_TOKEN}
            
            response = await client.post(
                upload_url,
                headers=upload_headers,
                files=files
            )
            
            if response.status_code not in (200, 201):
                logger.error(
                    f"Ошибка загрузки файла: "
                    f"{response.status_code} — {response.text}"
                )
                return None
            
            # Логируем ответ для отладки
            logger.info(f"Ответ от upload URL: status={response.status_code}, content={response.text[:500]}")
            
            # Проверяем, что ответ не пустой
            if not response.text or response.text.strip() == "":
                # Пустой ответ означает успешную загрузку
                # Извлекаем token из URL загрузки (параметр photoIds)
                from urllib.parse import urlparse, parse_qs
                parsed = urlparse(upload_url)
                query_params = parse_qs(parsed.query)
                token = query_params.get("photoIds", [None])[0]
                
                if token:
                    logger.info(f"Извлечен token из URL: {token}")
                    return {
                        "token": token,
                        "type": file_type
                    }
                else:
                    logger.error("Не удалось извлечь token из URL загрузки")
                    return None
            
            # Парсим JSON ответ
            try:
                result = response.json()
            except Exception as e:
                logger.error(f"Ошибка парсинга JSON ответа: {e}, content={response.text[:200]}")
                return None
            
            # Извлекаем token из разных форматов ответа
            token = None
            
            # Формат 1: {"token": "..."}
            if "token" in result:
                token = result["token"]
            
            # Формат 2: {"photos": {"photoId": {"token": "..."}}}
            elif "photos" in result and isinstance(result["photos"], dict):
                photos = result["photos"]
                # Берём первое фото из словаря
                for photo_id, photo_data in photos.items():
                    if isinstance(photo_data, dict) and "token" in photo_data:
                        token = photo_data["token"]
                        break
            
            # Формат 3: другие возможные варианты
            # Можно добавить при необходимости
            
            if not token:
                logger.error(f"Не получен token после загрузки файла. Ответ: {result}")
                return None
            
            logger.info(f"Успешно получен token для {file_type}")
            return {
                "token": token,
                "type": file_type
            }
            
    except Exception as e:
        logger.error(f"Исключение при загрузке файла: {e}")
        return None


async def send_document(
    user_id: int,
    file_data: bytes,
    filename: str,
    caption: str = None,
    file_type: str = "file",
    max_retries: int = 3,
    retry_delay: float = 1.0
) -> int | None:
    """
    Отправка файла пользователю через MAX API.
    
    Этапы:
    1. Загрузка файла через upload_file() → получение token
    2. POST /messages с attachments → отправка пользователю
    
    Args:
        user_id: ID пользователя
        file_data: Бинарные данные файла
        filename: Имя файла
        caption: Подпись к файлу (опционально)
        file_type: Тип файла: "file", "image", "video", "audio"
        max_retries: Максимальное количество попыток при ошибке attachment.not.ready
        retry_delay: Начальная задержка между попытками (увеличивается экспоненциально)
    
    Returns:
        HTTP status code или None при ошибке
    """
    # Загружаем файл и получаем token
    upload_result = await upload_file(file_data, filename, file_type)
    
    if not upload_result:
        logger.error("Не удалось загрузить файл")
        return None
    
    token = upload_result["token"]
    media_type = upload_result["type"]
    
    # Формируем payload для отправки сообщения с файлом
    url = f"{MAX_BASE_URL}/messages"
    headers = {
        "Authorization": MAX_API_TOKEN,
        "Content-Type": "application/json"
    }
    params = {"user_id": user_id}
    
    payload = {
        "attachments": [
            {
                "type": media_type,
                "payload": {
                    "token": token
                }
            }
        ]
    }
    
    if caption:
        payload["text"] = caption
    
    async with httpx.AsyncClient() as client:
        for attempt in range(max_retries):
            try:
                response = await client.post(
                    url, headers=headers, params=params, json=payload
                )
                
                if response.status_code == 200:
                    return 200
                
                # Проверяем ошибку attachment.not.ready
                try:
                    error_data = response.json()
                    if error_data.get("code") == "attachment.not.ready":
                        if attempt < max_retries - 1:
                            # Увеличиваем задержку экспоненциально
                            current_delay = retry_delay * (2 ** attempt)
                            logger.warning(
                                f"Файл ещё обрабатывается (попытка {attempt + 1}/{max_retries}). "
                                f"Пауза {current_delay:.1f} сек..."
                            )
                            await asyncio.sleep(current_delay)
                            continue
                        else:
                            logger.error(
                                f"Превышено максимальное количество попыток. "
                                f"Файл не готов к отправке."
                            )
                except Exception:
                    pass
                
                logger.error(
                    f"Ошибка отправки документа: "
                    f"{response.status_code} — {response.text}"
                )
                return response.status_code
                
            except Exception as e:
                logger.error(f"Исключение при отправке документа: {e}")
                return None
    
    return None


async def send_generated_image(
    user_id: int,
    image_bytes: bytes,
    caption: str = None,
) -> int | None:
    """
    Отправка сгенерированного/отредактированного изображения пользователю.
    
    Алгоритм:
    1. POST /uploads?type=image → получение URL загрузки
    2. Загрузка изображения по URL → получение token
    3. POST /messages с token → отправка пользователю
    
    Args:
        user_id: ID пользователя
        image_bytes: Бинарные данные изображения
        caption: Подпись к изображению (опционально)
    
    Returns:
        HTTP status code или None при ошибке
    """
    return await send_document(
        user_id=user_id,
        file_data=image_bytes,
        filename="generated_image.png",
        caption=caption,
        file_type="image"
    )
