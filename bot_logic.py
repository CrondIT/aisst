"""Модуль бизнес-логики бота: обработка команд и сообщений."""

import asyncio
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor

import db
from global_state import (
    user_modes,
    GIGACHAT_API_KEY,
    GIGACHAT_SCOPE,
)
from utils import logger
from gigachat import GigaChat

_executor = ThreadPoolExecutor(max_workers=10)

# Клиент GigaChat для транскрибации и чата
_giga_client = GigaChat(
    credentials=GIGACHAT_API_KEY,
    scope=GIGACHAT_SCOPE,
    model="GigaChat",
    ca_bundle_file="russian_trusted_root_ca_pem.crt",
)

TEMP_DIR = os.path.join(os.path.dirname(__file__), "temp")


async def transcribe_audio(audio_data: bytes, ext: str = ".ogg") -> str:
    """
    Транскрибирует аудио через GigaChat.
    Возвращает распознанный текст.
    """
    loop = asyncio.get_running_loop()

    tmp = tempfile.NamedTemporaryFile(
        delete=False, suffix=ext, dir=TEMP_DIR
    )
    tmp.write(audio_data)
    tmp.close()
    tmp_path = tmp.name

    try:
        def _do_transcribe():
            with open(tmp_path, "rb") as f:
                return _giga_client.audio.transcriptions.create(
                    file=f,
                    model="GigaChat",
                )

        result = await loop.run_in_executor(_executor, _do_transcribe)
        return getattr(result, "text", "") or ""
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
