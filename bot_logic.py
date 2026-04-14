"""Модуль бизнес-логики бота: обработка команд и сообщений."""

import asyncio
import os
import tempfile
import httpx
from concurrent.futures import ThreadPoolExecutor

import db
from global_state import (
    user_modes,
    GIGACHAT_API_KEY,
    GIGACHAT_SCOPE,
    GIGACHAT_CLIENT_ID,
    GIGACHAT_CLIENT_SECRET,
)
from utils import logger
from gigachat import GigaChat

_executor = ThreadPoolExecutor(max_workers=10)

# Клиент GigaChat для чата
_giga_client = GigaChat(
    credentials=GIGACHAT_API_KEY,
    scope=GIGACHAT_SCOPE,
    model="GigaChat",
    ca_bundle_file="russian_trusted_root_ca_pem.crt",
)

# Параметры для получения токена GigaChat (OAuth2)
_GIGACHAT_AUTH_URL = "https://ngw.devices.sberbank.ru:9877/api/v2/oauth"
_GIGACHAT_TRANSCRIBE_URL = (
    "https://gigachat.devices.sberbank.ru/api/v1/audio/transcriptions"
)
_CA_BUNDLE = os.path.join(os.path.dirname(__file__),
                          "russian_trusted_root_ca_pem.crt")
_GIGACHAT_TOKEN: str | None = None  # Кэшированный токен
_GIGACHAT_TOKEN_EXP: float = 0  # Время истечения


async def _get_giga_token() -> str:
    """Получает access token для GigaChat API."""
    import time

    global _GIGACHAT_TOKEN, _GIGACHAT_TOKEN_EXP
    now = time.time()
    if _GIGACHAT_TOKEN and now < _GIGACHAT_TOKEN_EXP - 30:
        return _GIGACHAT_TOKEN

    import base64

    auth = base64.b64encode(
        f"{GIGACHAT_CLIENT_ID}:{GIGACHAT_CLIENT_SECRET}".encode()
    ).decode()

    async with httpx.AsyncClient(verify=_CA_BUNDLE, timeout=30.0) as client:
        logger.warning("Запрос токена GigaChat...")
        try:
            resp = await client.post(
                _GIGACHAT_AUTH_URL,
                headers={
                    "Authorization": f"Basic {auth}",
                    "RqUID": GIGACHAT_CLIENT_ID,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"scope": GIGACHAT_SCOPE},
            )
            logger.warning(
                "GigaChat token request: status=%d, body=%s",
                resp.status_code,
                resp.text[:200],
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.TimeoutException:
            logger.error("Timeout при запросе токена GigaChat")
            raise RuntimeError("Timeout при запросе токена GigaChat")
        except Exception as e:
            logger.error("HTTP ошибка при запросе токена: %r", e, exc_info=True)
            raise
        _GIGACHAT_TOKEN = data["access_token"]
        # expires_at — в миллисекундах, конвертируем в секунды
        _GIGACHAT_TOKEN_EXP = data.get("expires_at", 0) / 1000
        return _GIGACHAT_TOKEN


async def transcribe_audio(audio_data: bytes, ext: str = ".ogg") -> str:
    """
    Транскрибирует аудио через GigaChat REST API.
    Возвращает распознанный текст.
    """
    logger.warning("transcribe_audio: начало, ext=%s, size=%d", ext, len(audio_data))
    token = await _get_giga_token()
    logger.warning("transcribe_audio: token получен")

    tmp = tempfile.NamedTemporaryFile(
        delete=False,
        suffix=ext,
        dir=os.path.join(os.path.dirname(__file__), "temp"),
    )
    tmp.write(audio_data)
    tmp.close()
    tmp_path = tmp.name

    try:
        def _do_post():
            with open(tmp_path, "rb") as f:
                resp = httpx.post(
                    _GIGACHAT_TRANSCRIBE_URL,
                    headers={"Authorization": f"Bearer {token}"},
                    files={"file": (f"audio{ext}", f, "audio/ogg")},
                    data={"model": "GigaChat"},
                    verify=_CA_BUNDLE,
                    timeout=60,
                )
                logger.warning(
                    "GigaChat transcription: status=%d, body=%s",
                    resp.status_code,
                    resp.text[:500],
                )
                resp.raise_for_status()
                return resp.json()

        result = await asyncio.get_running_loop().run_in_executor(
            _executor, _do_post
        )
        logger.warning("GigaChat transcription response: %s", result)
        return result.get("text", "") or ""
    except Exception as e:
        logger.error(
            "GigaChat transcription failed: %s (type=%s)",
            e, type(e).__name__,
            exc_info=True,
        )
        raise
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


async def handle_command(user_text: str, sender: dict) -> str | None:
    """
    Обработка команд бота.
    Возвращает текст ответа или None, если команда не распознана.
    """
    if not user_text.startswith("/"):
        return None

    command_parts = user_text.split(maxsplit=1)
    command = command_parts[0].lower()

    user_name = sender.get("name", "Неизвестный пользователь")
    user_id = sender.get("user_id")
    user_data = await db.get_user(user_id)

    if command == "/billing":
        if user_data:
            balance = user_data["coins"] + user_data["giftcoins"]
            return (
                f"Уважаемый: {user_name}!\n"
                f"Ваш баланс: {balance} ₽"
            )
        return f"Пользователь: {user_name} в списках не значится)"

    # Будущие команды:
    if command == "/chat":
        user_modes[user_id] = "chat"
        return "chat"
    if command == "/file":
        user_modes[user_id] = "file"
        return "file"
    if command == "/edit":
        user_modes[user_id] = "edit"
        return "edit"

    return None
