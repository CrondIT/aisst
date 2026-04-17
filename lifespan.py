from contextlib import asynccontextmanager

from fastapi import FastAPI
from global_state import (
    GIGACHAT_API_KEY,
    WEBHOOK_URL,
    WEBHOOK_SECRET,
    GIGACHAT_SCOPE,
    ADMIN_API_TOKEN,
)
from gigachat.client import GigaChat
from ai_models import GigaChatClient
# import google.generativeai as genai
import max_api
import db

from utils import logger, setup_logging


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
    # ─── Gemini ───
    # genai.configure(api_key=GEMINI_API_KEY)
    # app.image_model = genai.GenerativeModel(MODELS['image'])

    # ─── GigaChat ───
    giga_client = GigaChat(
        credentials=GIGACHAT_API_KEY,
        scope=GIGACHAT_SCOPE,
        ca_bundle_file="russian_trusted_root_ca_pem.crt",
    )

    app.state.giga_client = GigaChatClient(giga_client)

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
    logger.info("Shutting down, closing giga_client...")
    await giga_client.close()
