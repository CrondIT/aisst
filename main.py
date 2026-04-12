"""Главный модуль FastAPI-приложения."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi import APIRouter
import uvicorn
import os
from global_state import (
    MAX_API_TOKEN,
    MAX_BASE_URL,
    GIGACHAT_API_KEY,
    WEBHOOK_URL,
    WEBHOOK_SECRET,
    GIGACHAT_SCOPE,
    ADMIN_API_TOKEN,
)
from fastapi import Depends, Header, HTTPException
from gigachat import GigaChat
import max_api
import db

from utils import logger, setup_logging

# ─── Роуты ───
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


@router.post("/webhook")
async def webhook(request: Request):
    """Endpoint для приёма webhook-обновлений от MAX."""
    _, data = await max_api.handle_webhook(request)

    giga_client = request.app.giga_client

    # Платформа может присылать один Update или массив
    if isinstance(data, list):
        #  process_update (в max_api.py) → `send_message` (в max_api.py)
        # → HTTP POST к MAX API.
        for update_item in data:
            await max_api.process_update(update_item, giga_client)
    else:
        await max_api.process_update(data, giga_client)

    return JSONResponse(content={"status": "ok"})


@router.get("/")
async def index(request: Request):
    return request.app.templates.TemplateResponse(
        request, "index.html"
    )


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


def create_app() -> FastAPI:
    """Фабрика приложения: создаёт и настраивает FastAPI."""

    # ─── Static files ───
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    # ─── Template files ───
    templates_dir = os.path.join(os.path.dirname(__file__), "templates")
    templates = Jinja2Templates(directory=templates_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Lifespan-событие: замена устаревшему @app.on_event('startup')."""
        # ─── Инициализация логирования (один раз при старте) ───
        setup_logging()

        # ─── Валидация безопасности ───
        if not WEBHOOK_SECRET:
            logger.critical(
                "WEBHOOK_SECRET не задан в .env! "
                "Приложение не запущено — настройте секрет."
            )
            raise RuntimeError("WEBHOOK_SECRET is required")
        if not ADMIN_API_TOKEN:
            logger.warning(
                "ADMIN_API_TOKEN не задан — /subscriptions недоступен"
            )

        # ─── Инициализация БД ───
        logger.info("Creating database...")
        await db.create_database()
        logger.info("Database initialized")

        logger.info(f"Startup: WEBHOOK_URL={WEBHOOK_URL!r}")

        # Startup
        if WEBHOOK_URL:
            await max_api.subscribe_webhook()
        else:
            logger.warning(
                "WEBHOOK_URL не задан. Webhook НЕ активирован. "
                "Установите WEBHOOK_URL в .env или используйте Long Polling."
            )
        yield

        # Shutdown
        logger.info("Shutting down, closing DB engine...")
        await db.engine.dispose()

    app = FastAPI(lifespan=lifespan)

    # Монтируем статику если директория существует
    if os.path.isdir(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    app.static_dir = static_dir
    app.templates = templates

    # ─── GigaChat client ───
    app.giga_client = GigaChat(
        credentials=GIGACHAT_API_KEY,
        scope=GIGACHAT_SCOPE,
        model="GigaChat",
        ca_bundle_file="russian_trusted_root_ca_pem.crt",
    )

    # Регистрируем роуты
    app.include_router(router)

    return app


app = create_app()

if __name__ == "__main__":
    logger.info(
        f"MAX_API_TOKEN: "
        f"{'*' + MAX_API_TOKEN[-10:] if MAX_API_TOKEN else 'None'}"
    )
    logger.info(f"MAX_BASE_URL: {MAX_BASE_URL}")
    uvicorn.run(app, host="0.0.0.0", port=8000)
