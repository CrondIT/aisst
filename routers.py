"""Роутеры FastAPI-приложения."""

import os
import uuid
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from global_state import ADMIN_API_TOKEN, TEMP_DIR
import bot_logic
import max_api

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    conversation_id: str | None = None
    temperature: float = Field(default=0.7, ge=0.0, le=1.5)
    max_tokens: int = Field(default=1500, ge=1, le=4096)


def _verify_admin(
    x_admin_token: str = Header(default=None)
) -> None:
    """Зависимость: проверка ADMIN_API_TOKEN для админ-эндпоинтов."""
    if not ADMIN_API_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="ADMIN_API_TOKEN not configured"
        )
    if x_admin_token != ADMIN_API_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")


@router.get("/")
async def index(request: Request):
    context = {}
    return request.app.templates.TemplateResponse(
        request,
        "index.html",
        context,
    )


@router.post("/webhook")
async def webhook(request: Request):
    """Endpoint для приёма webhook-обновлений от MAX."""
    _, data = await max_api.handle_webhook(request)

    if isinstance(data, list):
        for update_item in data:
            await max_api.process_update(update_item, request)
    else:
        await max_api.process_update(data, request)

    return JSONResponse(content={"status": "ok"})


@router.get("/subscriptions")
async def get_subscriptions(
    _admin: None = Depends(_verify_admin)
):
    """Просмотр текущих подписок (требуется ADMIN_API_TOKEN)."""
    return await max_api.get_subscriptions()


@router.delete("/subscriptions")
async def delete_subscription(
    subscription_id: int = None,
    _admin: None = Depends(_verify_admin)
):
    """Удаление webhook-подписки (требуется ADMIN_API_TOKEN)."""
    return await max_api.delete_subscription(subscription_id)


@router.post("/save-file")
async def upload_file(upload_file: UploadFile):
    if not upload_file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")

    ext = os.path.splitext(upload_file.filename)[1]
    unique_name = f"{uuid.uuid4().hex}{ext}"
    dest = os.path.join(TEMP_DIR, unique_name)

    try:
        contents = await upload_file.read()
        with open(dest, "wb") as f:
            f.write(contents)
    except Exception as exc:
        logger.error(
            "Failed to save uploaded file %s: %s", upload_file.filename, exc
        )
        # Убираем частичный файл при ошибке
        if os.path.exists(dest):
            os.remove(dest)
        raise HTTPException(status_code=500, detail="Failed to save file")

    return JSONResponse(
        content={
            "status": "ok",
            "filename": unique_name,
            "path": dest,
        }
    )


# Пул потоков для синхронного SDK GigaChat
_giga_executor = ThreadPoolExecutor(max_workers=10)


@router.post("/giga-chat")
async def chat_with_giga(body: ChatRequest, request: Request):
    """Endpoint для общения с GigaChat."""
    giga_client = request.app.giga_client

    try:
        from gigachat.models import MessagesRequest, MessageRole

        messages = [
            {"role": MessageRole.USER, "text": body.message}
        ]

        chat_req = MessagesRequest(
            messages=messages,
            temperature=body.temperature,
            max_tokens=body.max_tokens,
            conversation_id=body.conversation_id,
        )

        # Вызываем синхронный SDK в отдельной потоке
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            _giga_executor, lambda: giga_client.chat(chat_req)
        )

        if not response or not response.choices:
            raise HTTPException(
                status_code=502, detail="GigaChat вернул пустой ответ"
            )

        reply = response.choices[0].message.text or ""

        result = {
            "status": "ok",
            "reply": reply,
            "conversation_id": response.conversation_id,
        }

        return JSONResponse(content=result)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("GigaChat chat error: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Ошибка GigaChat: {str(exc)}"
        )


@router.post("/api/send-command")
async def send_command(request: Request):
    """
    API endpoint для отправки команды от имени пользователя
    (из мини-приложения).
    """
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    command = data.get("command", "")
    user_id = data.get("user_id")

    print("command, user_id:", command, user_id)

    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")

    if not command:
        raise HTTPException(status_code=400, detail="Command is required")

    if not command.startswith("/"):
        command = "/" + command

    sender = {"user_id": user_id, "name": "mini_app_user"}

    result = await bot_logic.handle_command(command, sender)
    if result:
        await max_api.send_message(user_id, result)

    return JSONResponse(
        content={"success": True, "command": command, "response": result}
    )


@router.post("/transcribe-giga")
async def transcribe_voice(
    file: UploadFile,
):
    """Распознавание голосового сообщения через GigaChat."""
    # Определяем допустимые расширения
    allowed_exts = {".ogg", ".mp3", ".wav", ".m4a", ".flac", ".opus"}
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in allowed_exts:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Неподдерживаемый формат. "
                f"Допустимые: {', '.join(sorted(allowed_exts))}"
            ),
        )

    try:
        audio_data = await file.read()
        text = await bot_logic.transcribe_audio(audio_data, ext)

        if not text:
            raise HTTPException(
                status_code=502,
                detail="GigaChat вернул пустую транскрипцию",
            )

        return JSONResponse(
            content={
                "status": "ok",
                "text": text,
            }
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Transcription error: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Ошибка транскрибации: {str(exc)}"
        )
