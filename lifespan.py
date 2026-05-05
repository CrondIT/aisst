from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from global_state import (
    GIGACHAT_API_KEY,
    WEBHOOK_URL,
    WEBHOOK_SECRET,
    GIGACHAT_SCOPE,
    ADMIN_API_TOKEN,
    RUS_TRUSTED_ROOT_CA_PEM,
)
from gigachat.client import GigaChat
from ai_models import GigaChatClient
from langchain_gigachat import GigaChat as LangChainGigaChat

import max_api
import db

from utils import logger, setup_logging

SERVER_START_TIME = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan-событие: замена устаревшему @app.on_event('startup')."""
    global SERVER_START_TIME
    SERVER_START_TIME = datetime.now(timezone.utc)

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
    giga_client = None
    if not GIGACHAT_API_KEY:
        logger.warning("GIGACHAT_API_KEY не задан. Режим GigaChat недоступен.")
    else:
        giga_client = GigaChat(
            credentials=GIGACHAT_API_KEY,
            scope=GIGACHAT_SCOPE,
            ca_bundle_file=RUS_TRUSTED_ROOT_CA_PEM,
        )
        # Кастомный клиент — для прямых generate()-вызовов
        app.state.giga_client = GigaChatClient(giga_client)

        # ─── LangChain-клиент ───  для RAG-цепочки (ask_rag)
        app.state.giga_lc_client = LangChainGigaChat(
            credentials=GIGACHAT_API_KEY,
            scope=GIGACHAT_SCOPE,
            model="GigaChat",                  # можно вынести в .env
            ca_bundle_file=RUS_TRUSTED_ROOT_CA_PEM,  # путь к сертификату
        )

        logger.info("GigaChat клиенты инициализированы (native + langchain)")

    # Startup
    if WEBHOOK_URL:
        await max_api.subscribe_webhook()
    else:
        logger.warning(
            "WEBHOOK_URL не задан. Webhook НЕ активирован. "
            "Установите WEBHOOK_URL в .env или используйте Long Polling."
        )

    yield  # ── приложение работает ─────────────────────────────────────

    # Shutdown
    logger.info("Shutting down, closing DB engine...")
    await db.engine.dispose()

    logger.info("Shutting down, closing giga_client...")
    if giga_client is not None:
        giga_client.close()
