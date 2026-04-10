import logging
import sys
from loguru import logger


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
                "format": "<red>{time:HH:mm:ss}</red> | <level>{message}</level>",
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
