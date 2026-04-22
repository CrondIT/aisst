"""Главный модуль FastAPI-приложения."""

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import uvicorn
import os
from global_state import (
    MAX_API_TOKEN,
    MAX_BASE_URL,
)
import lifespan

from utils import logger
from routers import router


def create_app() -> FastAPI:
    """Фабрика приложения: создаёт и настраивает FastAPI."""

    # ─── Static files ───
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    # ─── Template files ───
    templates_dir = os.path.join(os.path.dirname(__file__), "templates")
    templates = Jinja2Templates(directory=templates_dir)

    app = FastAPI(
        title="AI SST Bot",
        description="Бот для ГБПОУ РМ ССТ",
        version="1.0.0",
        lifespan=lifespan.lifespan,
    )

    # Монтируем статику если директория существует
    if os.path.isdir(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")


    app.static_dir = static_dir
    app.templates = templates

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
