import logging
import httpx
import sys
import os
from PIL import Image
from io import BytesIO
from datetime import datetime
from loguru import logger
from global_state import (
    PROXY_IP,
    PROXY_PORT,
    PROXY_USER,
    PROXY_PASSWORD,
    TEMP_DIR,
    MAX_BASE_URL,
    MAX_API_TOKEN,
    )


async def send_message_from_file(user_id: int, text: str) -> int | None:
    """
    Отправка сообщения через API MAX (для использования в фоновых задачах).
    """
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
                    f"Ошибка отправки: {response.status_code} — "
                    f"{response.text}"
                )
            return response.status_code
        except Exception as e:
            logger.error(f"Исключение при отправке: {e}")
            return None


def get_socks_proxy_mount() -> "httpx.HTTPTransport | None":
    """
    Создаёт HTTPTransport с SOCKS5-прокси для httpx.
    Возвращает None, если прокси не настроен.

    Требует: pip install httpx-socks

    Использование:
        transport = get_socks_proxy_mount()
        if transport:
            client = httpx.AsyncClient(transport=transport)
        else:
            client = httpx.AsyncClient()
    """
    if not PROXY_IP:
        return None

    from httpx_socks import AsyncProxyTransport

    proxy_url = get_proxy_url()  # socks5://user:pass@ip
    return AsyncProxyTransport.from_url(proxy_url)


# 1. Создаем класс, который перехватывает стандартные логи
class InterceptHandler(logging.Handler):
    def emit(self, record):
        # Получаем соответствующий уровень логирования в Loguru
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Находим место в коде, откуда пришел лог
        frame, depth = sys._getframe(6), 6
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def get_proxy_url() -> str | None:
    """
    Возвращает URL SOCKS5-прокси для использования в http-клиентах.
    Формат: socks5://user:password@ip:port
    Если PROXY_IP не задан — возвращает None.
    """
    if not PROXY_IP:
        return None

    if PROXY_USER and PROXY_PASSWORD:
        return (
            f"socks5://{PROXY_USER}:{PROXY_PASSWORD}@{PROXY_IP}:{PROXY_PORT}"
        )
    return f"socks5://{PROXY_IP}:{PROXY_PORT}"


def setup_logging():
    # Полностью очищаем настройки стандартного логгера
    logging.root.handlers = [InterceptHandler()]
    logging.root.setLevel(logging.INFO)

    # Перехватываем логи всех библиотек (uvicorn, fastapi, gunicorn)
    for name in logging.root.manager.loggerDict.keys():
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True

    # Настраиваем сам Loguru (вывод в файл + консоль)
    logger.configure(
        handlers=[
            {
                "sink": sys.stdout,
                "format": (
                    "<yellow>{time:HH:mm:ss}</yellow> | "
                    "<level>{message}</level>"
                ),
            },
            {
                "sink": "app_unified.log",
                "rotation": "5 MB",  # размер одного файла
                "retention": 10,  # оставить 10 последних файлов
                "enqueue": True,  # Асинхронно
                "compression": "zip",
            },
        ]
    )


async def save_user_image(image_url: str, user_id: int) -> str | None:
    """Скачивает и сохраняет изображение пользователя в temp/."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(image_url)
            if response.status_code != 200:
                logger.error(f"Ошибка скачивания: {response.status_code}")
                return None

            image = Image.open(BytesIO(response.content))

            if image.mode in ("RGBA", "P"):
                image = image.convert("RGB")

            timestamp = int(datetime.now().timestamp() * 1000)
            filename = f"photo_{user_id}_{timestamp}.jpg"
            filepath = os.path.join(TEMP_DIR, filename)

            image.save(filepath, "JPEG", quality=95)
            logger.info(f"Сохранено изображение: {filepath}")
            return filepath

    except Exception as e:
        logger.error(f"Ошибка сохранения изображения: {e}")
        return None


async def save_user_file(
        file_url: str, user_id: int, ext: str, default_name: str = "file"
) -> str | None:
    """Скачивает и сохраняет произвольный файл в temp/."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(file_url)
            if response.status_code != 200:
                logger.error(f"Ошибка скачивания: {response.status_code}")
                return None

            content = response.content
            content_type = response.headers.get(
                "Content-Type", ""
            ).split(";")[0].strip()

            timestamp = int(datetime.now().timestamp() * 1000)
            filename = f"{default_name}_{user_id}_{timestamp}.{ext}"
            filepath = os.path.join(TEMP_DIR, filename)

            with open(filepath, "wb") as f:
                f.write(content)

            logger.info(f"Сохранён файл: {filepath} ({content_type})")
            return filepath

    except Exception as e:
        logger.error(f"Ошибка сохранения файла: {e}")
        return None
