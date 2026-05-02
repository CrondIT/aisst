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

            # Создаем папку temp, если её нет
            os.makedirs(TEMP_DIR, exist_ok=True)

            image.save(filepath, "JPEG", quality=95)
            logger.info(f"Сохранено изображение: {filepath}")
            return filepath

    except Exception as e:
        logger.error(f"Ошибка сохранения изображения: {e}")
        return None


async def save_user_file(
        file_url: str,
        user_id: int,
        ext: str,
        default_name: str = "file",
        name: str = None,
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

            name = name[:90] if len(name) > 90 else name
            filename = f"{default_name}_{user_id}_{name}.{ext}"
            filepath = os.path.join(TEMP_DIR, filename)

            # Создаем папку temp, если её нет
            os.makedirs(TEMP_DIR, exist_ok=True)

            with open(filepath, "wb") as f:
                f.write(content)

            logger.info(f"Сохранён файл: {filepath} ({content_type})")
            return filepath

    except Exception as e:
        logger.error(f"Ошибка сохранения файла: {e}")
        return None


def split_long_message(
        text: str, MESSAGE_LIMIT: int = 4096
):
    """
    Разбивает  длинное сообщение на части,
    если оно превышает лимит
    """
    text_parts = []
    if len(text) <= MESSAGE_LIMIT:
        # Message fits in a single message
        text_parts.append(text)
        return text_parts

    # Split the message by paragraphs first to avoid breaking sentences
    paragraphs = text.split("\n")

    current_message = ""
    for paragraph in paragraphs:
        # Check if adding this paragraph would exceed the limit
        if len(current_message) + len(paragraph) + 1 <= MESSAGE_LIMIT:
            if current_message:
                current_message += "\n" + paragraph
            else:
                current_message = paragraph
        else:
            # Send the current message if it's not empty
            if current_message:
                text_parts.append(current_message)

            # If the single paragraph is too long, split it by sentences
            if len(paragraph) > MESSAGE_LIMIT:
                sentences = paragraph.split(". ")
                temp_message = ""
                for sentence in sentences:
                    if (
                        len(temp_message) + len(sentence) + 2
                        <= MESSAGE_LIMIT
                    ):
                        if temp_message:
                            temp_message += ". " + sentence
                        else:
                            temp_message = sentence
                    else:
                        if temp_message:
                            text_parts.append(temp_message + ".")
                        temp_message = sentence

                # Add the last part if there's anything left
                if temp_message:
                    current_message = temp_message
                else:
                    current_message = ""
            else:
                current_message = paragraph

    # Send the remaining message if there's anything left
    if current_message:
        text_parts.append(current_message)
    return text_parts
