import logging
import httpx
import sys
import os
import re
from PIL import Image
from io import BytesIO
from datetime import datetime
from urllib.parse import urlparse, unquote
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
    """
    if not PROXY_IP:
        return None

    from httpx_socks import AsyncProxyTransport

    proxy_url = get_proxy_url()
    return AsyncProxyTransport.from_url(proxy_url)


class InterceptHandler(logging.Handler):
    """Перехватывает стандартные логи Python и перенаправляет в Loguru."""
    def emit(self, record):
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = sys._getframe(6), 6
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def get_proxy_url() -> str | None:
    """Возвращает URL SOCKS5-прокси. Формат: socks5://user:password@ip:port"""
    if not PROXY_IP:
        return None

    if PROXY_USER and PROXY_PASSWORD:
        return (
            f"socks5://{PROXY_USER}:{PROXY_PASSWORD}@{PROXY_IP}:{PROXY_PORT}"
        )
    return f"socks5://{PROXY_IP}:{PROXY_PORT}"


def setup_logging():
    """Настройка единого логирования через Loguru."""
    logging.root.handlers = [InterceptHandler()]
    logging.root.setLevel(logging.INFO)

    for name in logging.root.manager.loggerDict.keys():
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True

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
                "rotation": "5 MB",
                "retention": 10,
                "enqueue": True,
                "compression": "zip",
            },
        ]
    )


def _sanitize_stem(raw: str, max_len: int) -> str:
    """
    Очищает произвольную строку до безопасного имени файла.
    Убирает расширение, заменяет спецсимволы, обрезает до max_len символов.
    """
    stem = os.path.splitext(raw)[0]              # убираем расширение если есть
    stem = re.sub(r"[^\w\-.]", "_", stem)        # оставляем \w, дефис, точку
    stem = re.sub(r"_+", "_", stem).strip("_")   # схлопываем подчёркивания
    return (stem[:max_len] if stem else "") or "file"


def _safe_stem_from_url(file_url: str, max_len: int = 100) -> str:
    """
    Извлекает имя файла из URL безопасным для ext4 способом.

    Порядок поиска:
    1. Query-параметры: filename=, name=, file=, fname=
       (MAX API: /attachments/getfile?file_id=...&filename=устав.pdf)
    2. basename из path — только если не служебное слово (getfile, download…)
    3. Fallback → "file"

    Реальное имя из Content-Disposition берётся позже в save_user_file.
    """
    _SERVICE_NAMES = {"getfile", "download", "file", "attachment", "get"}

    try:
        from urllib.parse import parse_qs
        parsed = urlparse(file_url)

        # ── 1. Query-параметры ──────────────────────────────────────────────
        query_params = parse_qs(parsed.query)
        for param in ("filename", "name", "file", "fname"):
            values = query_params.get(param)
            if values:
                candidate = unquote(values[0]).strip()
                if candidate:
                    return _sanitize_stem(candidate, max_len)

        # ── 2. basename из path ─────────────────────────────────────────────
        path_basename = unquote(os.path.basename(parsed.path)).strip()
        stem_lower = os.path.splitext(path_basename)[0].lower()
        if path_basename and stem_lower not in _SERVICE_NAMES:
            return _sanitize_stem(path_basename, max_len)

        # ── 3. Fallback ─────────────────────────────────────────────────────
        return "file"

    except Exception:
        return "file"


def _stem_from_content_disposition(header_value: str) -> str | None:
    """
    Извлекает имя файла из заголовка Content-Disposition.

    Поддерживает форматы:
        attachment; filename="устав.pdf"
        attachment; filename*=UTF-8''%D1%83%D1%81%D1%82%D0%B0%D0%B2.pdf
    """
    if not header_value:
        return None

    # RFC 5987: filename*=UTF-8''encoded — приоритетнее обычного filename
    match = re.search(
        r"filename\*\s*=\s*\S+''(.+)", header_value, re.IGNORECASE
    )
    if match:
        return unquote(match.group(1).strip().strip('"'))

    # Обычный: filename="name.pdf" или filename=name.pdf
    match = re.search(
        r'filename\s*=\s*"?([^";\r\n]+)"?', header_value, re.IGNORECASE
    )
    if match:
        return unquote(match.group(1).strip().strip('"'))

    return None


def _trim_to_byte_limit(name: str, limit: int = 240) -> str:
    """
    Обрезает строку так, чтобы её UTF-8 представление
    не превышало limit байт.

    Ubuntu ext4 допускает максимум 255 байт на имя файла.
    Лимит 240 оставляет запас на расширение и разделители.
    """
    encoded = name.encode("utf-8")
    if len(encoded) <= limit:
        return name

    # Обрезаем побайтово с конца, пока не уложимся
    while len(name.encode("utf-8")) > limit:
        name = name[:-1]
    return name


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
    """Скачивает и сохраняет произвольный файл в temp/.

    Имя файла определяется в следующем порядке приоритета:
    1. Content-Disposition заголовок ответа (самое точное имя)
    2. Query-параметры URL (filename=, name=, file=)
    3. basename из path URL (если не служебное слово типа getfile)
    4. Fallback: {default_name}_{user_id}.{ext}

    Итоговый формат: {default_name}_{user_id}_{orig_name}.{ext}

    Ограничения Ubuntu ext4: максимум 255 байт на имя файла.
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(file_url)
            if response.status_code != 200:
                logger.error(f"Ошибка скачивания: {response.status_code}")
                return None

            content = response.content
            content_type = (
                response.headers.get("Content-Type", "").split(";")[0].strip()
            )

            # ── Определяем имя файла ────────────────────────────────────────
            # Приоритет 1: Content-Disposition из заголовков ответа
            cd_header = response.headers.get("Content-Disposition", "")
            cd_name = _stem_from_content_disposition(cd_header)

            if cd_name:
                orig_stem = _sanitize_stem(cd_name, max_len=100)
                logger.debug(f"Имя из Content-Disposition: {cd_name!r}")
            else:
                # Приоритет 2 и 3: из URL
                orig_stem = _safe_stem_from_url(file_url, max_len=100)
                logger.debug(f"Имя из URL: {orig_stem!r}")

            # ── Собираем итоговое имя файла ─────────────────────────────────
            base_name = f"{default_name}_{user_id}_{orig_stem}"
            base_name = _trim_to_byte_limit(
                base_name,
                limit=240 - len(ext) - 1
            )
            filename = f"{base_name}.{ext}"
            filepath = os.path.join(TEMP_DIR, filename)

            # ── Сохраняем ────────────────────────────────────────────────────
            if os.path.exists(filepath):
                logger.warning(
                    f"Файл уже существует, перезаписываем: {filepath}"
                )

            with open(filepath, "wb") as f:
                f.write(content)

            logger.info(f"Сохранён файл: {filepath} ({content_type})")
            return filepath

    except Exception as e:
        logger.error(f"Ошибка сохранения файла: {e}")
        return None
