"""Главный модуль FastAPI-приложения."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
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
    GEMINI_API_KEY,
    MODELS,
)
from gigachat import GigaChat
from gigachat.async_client import GigaChatAsync
from ai_models import GigaChatClient
import google.generativeai as genai
import max_api
import db

from utils import logger, setup_logging
from routers import router


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

        # ----------- Инициализация ИИ моделей -----------
        genai.configure(api_key=GEMINI_API_KEY)
        app.image_model = genai.GenerativeModel(MODELS['image'])

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
    giga_client = GigaChatAsync(
        credentials=GIGACHAT_API_KEY,
        scope=GIGACHAT_SCOPE,
        model="GigaChat",
        ca_bundle_file="russian_trusted_root_ca_pem.crt",
    )

    app.giga_model = GigaChatClient(giga_client)

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
