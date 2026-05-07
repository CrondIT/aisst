"""
Конфигурация Redis для очереди сообщений.
"""

import os
from dotenv import load_dotenv

# Загрузить переменные из файла .env
load_dotenv()

# Конфигурация Redis
REDIS_CONFIG = {
    "host": os.getenv("REDIS_HOST", "localhost"),
    "port": int(os.getenv("REDIS_PORT", 6379)),
    "db": int(os.getenv("REDIS_DB", 0)),
    "password": os.getenv("REDIS_PASSWORD", None),
    "ssl": os.getenv("REDIS_SSL", "false").lower() == "true",
}

# Префикс для всех ключей Redis
REDIS_PREFIX = os.getenv("REDIS_PREFIX", "bot")

# Таймауты
REDIS_SOCKET_TIMEOUT = int(os.getenv("REDIS_SOCKET_TIMEOUT", 5))
REDIS_SOCKET_CONNECT_TIMEOUT = int(
    os.getenv("REDIS_SOCKET_CONNECT_TIMEOUT", 5)
)

# Настройки retry
REDIS_RETRY_ON_TIMEOUT = (
    os.getenv("REDIS_RETRY_ON_TIMEOUT", "true").lower() == "true"
)
REDIS_MAX_RETRIES = int(os.getenv("REDIS_MAX_RETRIES", 3))

# TTL для разных типов данных (в секундах)
REDIS_TTL = {
    "user_context": int(os.getenv("REDIS_TTL_USER_CONTEXT", 3600)),  # 1 час
    "user_mode": int(os.getenv("REDIS_TTL_USER_MODE", 7200)),  # 2 часа
    "user_files": int(os.getenv("REDIS_TTL_USER_FILES", 1800)),  # 30 минут
    "user_edit": int(os.getenv("REDIS_TTL_USER_EDIT", 1800)),  # 30 минут
    "task_result": int(os.getenv("REDIS_TTL_TASK_RESULT", 300)),  # 5 минут
    "rate_limit": int(os.getenv("REDIS_TTL_RATE_LIMIT", 60)),  # 1 минута
}

# Настройки очередей
QUEUE_CONFIG = {
    "chat": os.getenv("QUEUE_CHAT", "chat"),
    "file": os.getenv("QUEUE_FILE", "file"),
    "image_gen": os.getenv("QUEUE_IMAGE_GEN", "image:gen"),
    "image_edit": os.getenv("QUEUE_IMAGE_EDIT", "image:edit"),
    "high_priority": os.getenv("QUEUE_HIGH_PRIORITY", "high"),
    "low_priority": os.getenv("QUEUE_LOW_PRIORITY", "low"),
}

# Максимальное количество задач в очереди (для мониторинга)
MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", 1000))

# Интервал опроса очередей (в секундах)
WORKER_POLL_INTERVAL = float(os.getenv("WORKER_POLL_INTERVAL", 1.0))

# Количество воркеров
NUM_WORKERS = int(os.getenv("NUM_WORKERS", 4))
