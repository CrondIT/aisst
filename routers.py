"""Роутеры FastAPI-приложения."""

import os
import uuid
import logging

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import JSONResponse

from global_state import ADMIN_API_TOKEN, TEMP_DIR
import max_api

logger = logging.getLogger(__name__)

router = APIRouter()


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
    return request.app.templates.TemplateResponse(
        request, "index.html"
    )


@router.post("/webhook")
async def webhook(request: Request):
    """Endpoint для приёма webhook-обновлений от MAX."""
    _, data = await max_api.handle_webhook(request)

    giga_client = request.app.giga_client

    # Платформа может присылать один Update или массив
    if isinstance(data, list):
        for update_item in data:
            await max_api.process_update(update_item, giga_client)
    else:
        await max_api.process_update(data, giga_client)

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


@router.post("/files")
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
