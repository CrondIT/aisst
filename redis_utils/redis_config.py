"""
Конфигурация Redis для проекта ассистента колледжа.
Используется для хранения состояний пользователей и очередей задач.
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
REDIS_PREFIX = os.getenv("REDIS_PREFIX", "aisst")

# Таймауты
REDIS_SOCKET_TIMEOUT = int(os.getenv("REDIS_SOCKET_TIMEOUT", 15))
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
    "task_result": int(os.getenv("REDIS_TTL_TASK_RESULT", 1800)),  # 30 мин для RAG
    "rate_limit": int(os.getenv("REDIS_TTL_RATE_LIMIT", 60)),  # 1 минута
}

# Настройки очередей для проекта ассистента колледжа
QUEUE_CONFIG = {
    "rag": os.getenv("QUEUE_RAG", "rag"),  # Загрузка файлов в RAG
    "audio": os.getenv("QUEUE_AUDIO", "audio"),  # Транскрибация аудио
    "file_process": os.getenv("QUEUE_FILE_PROCESS", "file_process"),  # Обработка файлов
    "high_priority": os.getenv("QUEUE_HIGH_PRIORITY", "high"),  # Высокий приоритет
    "low_priority": os.getenv("QUEUE_LOW_PRIORITY", "low"),  # Низкий приоритет
}

# Канал для Pub/Sub уведомлений о завершении задач
REDIS_NOTIFICATION_CHANNEL = os.getenv("REDIS_NOTIFICATION_CHANNEL", "notifications")

# TTL для результатов задач по типу (в секундах)
RAG_TASK_TIMEOUT = int(os.getenv("RAG_TASK_TIMEOUT", 3600))  # 1 час для RAG задач
