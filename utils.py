import logging
import sys
from loguru import logger
from global_state import PROXY_IP, PROXY_PORT, PROXY_USER, PROXY_PASSWORD


def get_socks_proxy_mount() -> "httpx.HTTPTransport | None":
    """
    Создаёт HTTPTransport с SOCKS5-прокси для httpx.
    Возвращает None, если прокси не настроен.

    Требует: pip install httpx-socks

    Использование:
        transport = get_socks_proxy_mount()
        if transport:
            client = httpx.AsyncClient(transport=transport)
        else:
            client = httpx.AsyncClient()
    """
    if not PROXY_IP:
        return None

    from httpx_socks import AsyncProxyTransport

    proxy_url = get_proxy_url()  # socks5://user:pass@ip
    return AsyncProxyTransport.from_url(proxy_url)


# 1. Создаем класс, который перехватывает стандартные логи и отдает их в Loguru
class InterceptHandler(logging.Handler):
    def emit(self, record):
        # Получаем соответствующий уровень логирования в Loguru
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Находим место в коде, откуда пришел лог
        frame, depth = sys._getframe(6), 6
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def get_proxy_url() -> str | None:
    """
    Возвращает URL SOCKS5-прокси для использования в http-клиентах.
    Формат: socks5://user:password@ip:port
    Если PROXY_IP не задан — возвращает None.
    """
    if not PROXY_IP:
        return None

    if PROXY_USER and PROXY_PASSWORD:
        return (
            f"socks5://{PROXY_USER}:{PROXY_PASSWORD}@{PROXY_IP}:{PROXY_PORT}"
        )
    return f"socks5://{PROXY_IP}:{PROXY_PORT}"


def setup_logging():
    # Полностью очищаем настройки стандартного логгера
    logging.root.handlers = [InterceptHandler()]
    logging.root.setLevel(logging.INFO)

    # Перехватываем логи всех библиотек (uvicorn, fastapi, gunicorn)
    for name in logging.root.manager.loggerDict.keys():
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True

    # Настраиваем сам Loguru (вывод в файл + консоль)
    logger.configure(
        handlers=[
            {
                "sink": sys.stdout,
                "format": "<yellow>{time:HH:mm:ss}</yellow> | <level>{message}</level>",
            },
            {
                "sink": "app_unified.log",
                "rotation": "5 MB",  # размер одного файла
                "retention": 10,  # оставить 10 последних файлов
                "enqueue": True,  # Асинхронно
                "compression": "zip",
            },
        ]
    )
