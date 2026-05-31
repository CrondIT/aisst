import os
import sys
import logging
from dotenv import load_dotenv
from pathlib import Path
from typing import Optional, Dict, Any, List

# load_dotenv(override=True)
load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)

logger = logging.getLogger(__name__)

# Global variables that need to be accessible across the entire project
# ВНИМАНИЕ: С переходом на Redis (USE_REDIS=true) эти словари не используются,
# так как состояния хранятся в Redis для согласованности между Gunicorn воркерами
# Оставлены для обратной совместимости, но не рекомендуются к использованию
if os.getenv("USE_REDIS", "false").lower() != "true":
    user_contexts = {}  # Хранилище контекста для каждого пользователя и режима
    user_modes = {}  # Хранит текущий режим для каждого пользователя
    user_edit_data = {}  # Хранит данные для редактирования изображений
    user_file_data = {}  # Хранит данные для анализа файлов
    user_edit_pending = {}  # Хранит ожидание промпта для редактирования изображ.
    user_pending_delete = {}  # Хранит ожидание подтверждения удаления файла
    user_previous_modes = {}  # Хранит предыдущий режим для каждого пользователя
    edited_photo_id = {}  # Хранит ID отредактированного изображения
    # Хранит путь к последнему отредактированному
    # изображению для каждого пользователя
    user_last_edited_images = {}
    # Хранит очередь изображений для редактирования для каждого пользователя
    user_edit_images_queue = {}
else:
    # Заглушки, чтобы код не падал при импорте
    user_contexts = user_modes = user_edit_data = user_file_data = {}
    user_edit_pending = user_pending_delete = user_previous_modes = {}
    edited_photo_id = user_last_edited_images = user_edit_images_queue = {}
    user_mentor_state = {}  # Состояние ментора для каждого пользователя

MAX_CONTEXT_MESSAGES = 5
MAX_REF_IMAGES = 6  # Максимальное количество изображений для редактирования

# разрешенные расширения для разных режимов
# ext in ALLOWED_EXTENSIONS.get("guestrag") -> True или False
ALLOWED_EXTENSIONS = {
    "rag": {"pdf", "txt", "docx", "doc", "xlsx", "xls"},
    "chat": {
        ".pdf",
        ".docx",
        ".txt",
        ".xlsx",
        ".xls",
        ".jpg",
        ".jpeg",
        ".png",
        ".bmp",
        ".tiff",
        ".webp",
        ".doc",  # Old Microsoft Word format
        ".odf",  # OpenDocument Text
        ".ods",  # OpenDocument Spreadsheet
        ".odp",  # OpenDocument Presentation
        ".ppt",  # PowerPoint slides
        ".pptx",  # PowerPoint slides (newer format)
    },
}

TEMP_DIR = os.getenv("TEMP_DIR", "temp")
GUEST_RAG_DIR = os.getenv("GUEST_RAG_DIR", "rag/guest")

ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

# Данные бота
MAX_API_TOKEN = os.getenv("MAX_API_TOKEN")
MAX_BASE_URL = os.getenv("MAX_BASE_URL")
MAX_DB_PATH = os.getenv("MAX_DB_PATH")

# Данные для подключения к GigaChat
GIGACHAT_CLIENT_ID = os.getenv("GIGACHAT_CLIENT_ID")
GIGACHAT_CLIENT_SECRET = os.getenv("GIGACHAT_CLIENT_SECRET")
GIGACHAT_API_KEY = os.getenv("GIGACHAT_API_KEY")
GIGACHAT_SCOPE = os.getenv("GIGACHAT_SCOPE") or "GIGACHAT_API_PERS"
RUS_TRUSTED_ROOT_CA_PEM = os.getenv("RUS_TRUSTED_ROOT_CA_PEM")

# Данные для подключения к SaluteSpeech
SALUTE_CLIENT_ID = os.getenv("SALUTE_CLIENT_ID")
SALUTE_SCOPE = os.getenv("SALUTE_SCOPE")
SALUTE_SPEECH_AUTH_KEY = os.getenv("SALUTE_SPEECH_AUTH_KEY")
SALUTE_SPEECH_OAUTH_URL = os.getenv("SALUTE_SPEECH_OAUTH_URL")
SALUTE_SPEECH_BASE_URL = os.getenv("SALUTE_SPEECH_BASE_URL")

# Данные для подключения к OpenAI и Gemini
# Получаем токены для разных режимов
OPENAI_API_KEY_CHAT = os.getenv("OPENAI_API_KEY")
OPENAI_API_KEY_IMAGE = os.getenv("OPENAI_API_KEY_IMAGE")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Модели для разных режимов
MODELS = {
    "chat": "gpt-5.2-chat-latest",
    "image": "gemini-3-pro-image-preview",
    "edit": "gemini-3-pro-image-preview",
    "ai_file": "gpt-5.2-chat-latest",
    "gigachat": "GigaChat",
    "gigachatpro": "GigaChat",
    "chatgpt": "gpt-5.2-chat-latest",   # режим ChatGPT
    "gemini": "gemini-2.5-pro",          # режим Gemini
}

# ─── Настройки webhook ───
# URL, на который MAX будет присылать обновления (строго HTTPS, порт 443)
# Для продакшена укажите реальный домен,
# например: https://mybot.example.com/webhook
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
# Секрет для проверки подлинности webhook (обязателен в .env)
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

# ─── Безопасность ───
# Токен для доступа к админ-эндпоинтам (/subscriptions)
ADMIN_API_TOKEN = os.getenv("ADMIN_API_TOKEN", "")

# Список доверенных IP для /webhook (пусто = без фильтрации)
# MAX API может присылать с разных IP — задайте реальные при необходимости
TRUSTED_WEBHOOK_IPS = [
    ip.strip()
    for ip in os.getenv("TRUSTED_WEBHOOK_IPS", "").split(",")
    if ip.strip()
]

# Rate limiting: макс. запросов на пользователя в минуту
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "30"))

# ─── Версионирование промптов ───
# Максимальное количество хранимых версий для каждого промпта
PROMPT_VERSIONS_LIMIT = int(os.getenv("PROMPT_VERSIONS_LIMIT", "10"))
# Количество версий для быстрого отката (хранится больше чем показывается)
PROMPT_VERSIONS_KEEP = int(os.getenv("PROMPT_VERSIONS_KEEP", "15"))

# ─── Прокси ───
PROXY_IP = os.getenv("PROXY_IP", "")
PROXY_PORT = int(os.getenv("PROXY_PORT", "1080"))
PROXY_USER = os.getenv("PROXY_USER", "")
PROXY_PASSWORD = os.getenv("PROXY_USER_PASSWORD", "")


def get_token_limit(model_name: str) -> int:
    """
    Get the maximum token limit for a specific model
    """
    model_name_lower = model_name.lower()
    limits = {
        # OpenAI models
        "gpt-5.2": 128000,
        "gpt-5.1": 128000,
        "gpt-4o-mini": 128000,
        "gpt-4-turbo": 128000,
        "gpt-4o": 128000,
        "gpt-4": 8192,
        "gpt-5.2-chat-latest": 128000,
        # DALL-E models
        "dall-e-3": 4096,  # Prompt length limit
        # Gemini models
        "imagen-4.0-generate-001": 8192,
        "gemini-2.5-pro": 2097152,
        "gemini-2.5-flash-image": 32768,
        "gemini-3-pro-image-preview": 32768,
        "gemini-1.5-pro": 1048576,
        "gemini-1.0-pro": 32768,
        # GigaChat models
        "gigachat-2-max": 128000,
        "gigachat-2-pro": 32768,
        "gigachat-2": 8192,
        "gigachat-4": 128000,
        "gigachat": 8192,
        # GigaChat Embeddings (not used for chat, just for similarity)
        "embeddings": 8192,
    }

    return limits.get(model_name_lower, 4096)  # Default fallback


# Maximum cost per message
COST_PER_MESSAGE = {
    "chat": 7,
    "ai_file": 7,
    "image": 5,
    "edit": 6,
}

# Cost per user's prompt tokens (per 1000000 tokens)
COST_PER_PROMPT = {
    "chat": 2,
    "ai_file": 2,
    "image": 5,
    "edit": 6,
}

# Cost per ai model's answer tokens (per 1000000 tokens)
COST_PER_ANSWER = {
    "chat": 14,
    "ai_file": 14,
    "image": 5,
    "edit": 6,
}


SYSTEM_PROMPTS = {
    "chat": (
        "You are a helpful assistant. "
        "Use web search only when your knowledge may be outdated "
        "or when the user explicitly asks for fresh data."
    ),
    "image": ("Ты помогаешь генерировать изображения."),
    "edit": ("Ты помогаешь редактировать изображения с помощью Gemini."),
    "ai_file": (
        "Ты помощник по анализу документов."
        "Отвечай на вопросы касательно "
        "содержимого предоставленного файла."
    ),
}

RTF_PROMPT = """
    Верни ТОЛЬКО валидныйr rtf без пояснений.
    Не используй markdown, только rtf.
    Не включай тройные кавычки в значениях.
"""


# Shared JSON schema for document generation across formats (DOCX, PDF, etc.)
DOCUMENT_JSON_SCHEMA = """
    Верни ТОЛЬКО валидный JSON без пояснений.
    Не используй markdown, только JSON.
    Не включай тройные кавычки в значениях.
    Строгая схема:
    {
    "meta": {"title": "string", "hide_title": false},
    "header": {
        "content": "string",
        "font_name": "string",
        "font_size": 12,
        "color": "string",
        "bold": false,
        "italic": false,
        "alignment": "left",
        "page_number": {
            "enabled": false,
            "format": "Page {PAGE} of {NUMPAGES}",
            "position": "right"
        }
    },
    "footer": {
        "content": "string",
        "font_name": "string",
        "font_size": 12,
        "color": "string",
        "bold": false,
        "italic": false,
        "alignment": "left",
        "page_number": {
            "enabled": false,
            "format": "Page {PAGE} of {NUMPAGES}",
            "position": "right"
        }
    },
    "blocks": [
        {"type":"heading","level":1,"text":"string", "font_name":"string",
        "font_size":12, "color":"string", "bold":false, "italic":false},
        {"type":"paragraph","text":"string", "font_name":"string",
        "font_size":12, "left_indent":0, "right_indent":0, "space_after":12,
        "alignment":"left", "color":"string", "bold":false, "italic":false,
        "underline":false},
        {"type":"list", "ordered":false, "font_name":"string", "font_size":12,
        "left_indent":0, "right_indent":0, "space_after":12,
        "alignment":"left", "color":"string", "bold":false, "italic":false,
        "items":["item1", "item2"]},
        {"type":"table", "headers":["column1", "column2"],
           "rows":[["value1", "value2"], ["value3", "value4"]],
           "params": {
               "header_font_name":"string",
               "header_font_size":12,
               "header_bold":true,
               "header_italic":false,
               "header_color":"string",
               "body_font_name":"string",
               "body_font_size":12,
               "body_bold":false,
               "body_italic":false,
               "body_color":"string",
               "table_style":"Table Grid",
               "header_bg_color":"string"
           },
           "table_properties": {
               "border": {"style":"single", "size":4, "color":"auto"},
               "cell_margin": {"top": 100, "bottom": 100,
               "left": 100, "right": 100},
               "widths": [2000, 3000]  // Ширина столбцов в TWIP (1/20 пункта)
           },
           "cell_properties": [
               {
                   "row": 0,
                   "col": 0,
                   "bg_color": "#D3D3D3",
                   "text_color": "#000000",
                   "text_wrap": true,
                   "vertical_alignment": "center",
                   "horizontal_alignment": "center",
                   "border": {"top": {"style":"single",
                   "size":4, "color":"auto"}}
               }
           ],
           "row_properties": [
               {
                   "row": 1,
                   "bg_color": "#F0F0F0",
                   "text_color": "#333333"
               }
           ]
        },
        {"type":"math", "formula":"LaTeX formula",
        "caption":"optional caption", "font_name":"string",
        "font_size":12, "math_font_size":12, "caption_font_size":10,
        "bold":false, "italic":true, "alignment":"left", "color":"string"},
        {"type":"function_graph", "function":"mathematical function",
         "x_min":-10, "x_max":10, "title":"Graph Title",
         "xlabel":"x", "ylabel":"y",
         "width":6, "height":4, "line_color":"blue", "line_width":2,
         "show_grid":true, "caption":"optional caption", "alignment":"center"},
        {"type":"toc", "title":"string", "levels":[1,2,3],
         "font_name":"string", "font_size":12, "indent":10,
         "leader_dots":true, "include_pages":true}
    ]
    }
    """


# ==================== Redis Integration ====================
# Функции для работы с Redis (если включён)
# При USE_REDIS=true состояния хранятся ТОЛЬКО в Redis,
# чтобы все Gunicorn воркеры видели одни и те же данные

_use_redis = os.getenv("USE_REDIS", "false").lower() == "true"
# Отладка УДАЛИТЬ!!!
print(
    f"[DEBUG] USE_REDIS={_use_redis}, env='{os.getenv('USE_REDIS')}'",
        flush=True
)

_queue = None


def _get_queue():
    """Ленивая инициализация очереди Redis"""
    global _queue, _use_redis
    if _queue is None and _use_redis:
        try:
            from redis_utils.redis_queue import RedisQueue

            _queue = RedisQueue()
        except Exception as e:
            logging.warning(
                 f"⚠️ Не удалось инициализировать Redis очередь: {e}"
            )
            _use_redis = False
    return _queue


def get_user_context(user_id: int, mode: str) -> list:
    """
    Получает контекст пользователя для указанного режима.
    При USE_REDIS=true - только из Redis.
    При USE_REDIS=false - из памяти (in-memory словарь).
    """
    if _use_redis:
        q = _get_queue()
        if q:
            context = q.get_user_state(user_id, f"context_{mode}")
            if context is not None:
                return context
        # Если Redis недоступен, возвращаем дефолтный контекст
    else:
        # Fallback к памяти (только для одиночного процесса)
        if user_id in user_contexts and mode in user_contexts[user_id]:
            return user_contexts[user_id][mode]

    # Возвращаем дефолтный контекст
    system_message = SYSTEM_PROMPTS.get(mode, "You are a helpful assistant.")
    return [{"role": "system", "content": system_message}]


def set_user_context(user_id: int, mode: str, context: list):
    """
    Сохраняет контекст пользователя для указанного режима.
    При USE_REDIS=true - только в Redis.
    При USE_REDIS=false - в память (in-memory словарь).
    """
    if _use_redis:
        # Сохраняем только в Redis
        q = _get_queue()
        if q:
            q.set_user_state(user_id, f"context_{mode}", context)
    else:
        # Сохраняем в память (только для одиночного процесса)
        if user_id not in user_contexts:
            user_contexts[user_id] = {}
        user_contexts[user_id][mode] = context


def get_user_mode(user_id: int) -> str:
    """Получает текущий режим пользователя"""
    if _use_redis:
        q = _get_queue()
        if q:
            mode = q.get_user_state(user_id, "mode")
            if mode:
                return mode
    else:
        return user_modes.get(user_id, "chat")
    # Дефолтное значение, если Redis недоступен
    return "gigachat"


def set_user_mode(user_id: int, mode: str):
    """Устанавливает текущий режим пользователя"""
    if _use_redis:
        q = _get_queue()
        if q:
            q.set_user_state(user_id, "mode", mode)
    else:
        user_modes[user_id] = mode


def get_user_file_data(user_id: int) -> dict:
    """Получает данные о файлах пользователя"""
    if _use_redis:
        q = _get_queue()
        if q:
            data = q.get_user_state(user_id, "files")
            if data:
                return data
    else:
        return user_file_data.get(user_id, {})
    return {}


def set_user_file_data(user_id: int, data: dict):
    """Сохраняет данные о файлах пользователя"""
    if _use_redis:
        q = _get_queue()
        if q:
            q.set_user_state(user_id, "files", data)
    else:
        user_file_data[user_id] = data


def get_user_edit_data(user_id: int) -> dict:
    """Получает данные для редактирования изображений"""
    if _use_redis:
        q = _get_queue()
        if q:
            data = q.get_user_state(user_id, "edit")
            if data:
                return data
    else:
        return user_edit_data.get(user_id, {})
    return {}


def set_user_edit_data(user_id: int, data: dict):
    """Сохраняет данные для редактирования изображений"""
    if _use_redis:
        q = _get_queue()
        if q:
            q.set_user_state(user_id, "edit", data)
    else:
        user_edit_data[user_id] = data


def get_user_edit_queue(user_id: int) -> list:
    """Получает очередь изображений для редактирования"""
    if _use_redis:
        q = _get_queue()
        if q:
            queue = q.get_user_state(user_id, "edit_queue")
            if queue:
                return queue
    else:
        return user_edit_images_queue.get(user_id, [])
    return []


def set_user_edit_queue(user_id: int, queue: list):
    """Сохраняет очередь изображений для редактирования"""
    if _use_redis:
        q = _get_queue()
        if q:
            q.set_user_state(user_id, "edit_queue", queue)
    else:
        user_edit_images_queue[user_id] = queue


def clear_user_data(user_id: int):
    """Очищает все данные пользователя"""
    if _use_redis:
        q = _get_queue()
        if q:
            q.delete_user_state(user_id)
    else:
        # Очищаем память
        for storage in [
            user_contexts,
            user_file_data,
            user_edit_data,
            user_edit_images_queue,
            user_edit_pending,
            edited_photo_id,
            user_last_edited_images,
            user_modes,  # Добавлено
            user_previous_modes,  # Добавлено
        ]:
            if user_id in storage:
                del storage[user_id]


# ==================== User Pending Delete ====================

def get_user_pending_delete(user_id: int) -> str | None:
    """Получает состояние подтверждения удаления файла для пользователя"""
    if _use_redis:
        q = _get_queue()
        if q:
            data = q.get_user_state(user_id, "pending_delete")
            return data
    else:
        return user_edit_pending.get(user_id)
    return None


def set_user_pending_delete(user_id: int, filename: str):
    """Устанавливает состояние подтверждения удаления файла"""
    if _use_redis:
        q = _get_queue()
        if q:
            q.set_user_state(user_id, "pending_delete", filename)
    else:
        user_edit_pending[user_id] = filename


def clear_user_pending_delete(user_id: int):
    """Очищает состояние подтверждения удаления файла"""
    if _use_redis:
        q = _get_queue()
        if q:
            q.delete_user_state(user_id, "pending_delete")
    else:
        user_edit_pending.pop(user_id, None)


def check_rate_limit(
    user_id: int, action: str, max_requests: int = 10, window_seconds: int = 60
) -> bool:
    """
    Проверяет rate limit для пользователя.

    Args:
        user_id: ID пользователя
        action: Тип действия (chat, image, file)
        max_requests: Максимум запросов в окно
        window_seconds: Размер окна в секундах

    Returns:
        True если запрос разрешён
    """
    if _use_redis:
        q = _get_queue()
        if q:
            return q.check_rate_limit(
                user_id, action, max_requests, window_seconds
            )

    # Без Redis rate limiting не работает
    return True


def get_queue_stats() -> dict:
    """Получает статистику очередей"""
    if _use_redis:
        q = _get_queue()
        if q:
            return q.get_stats()
    return {"error": "Redis not enabled"}


def enqueue_task(
    queue_type: str, task_data: dict, priority: str = "normal"
) -> str:
    """
    Добавляет задачу в очередь Redis.

    Args:
        queue_type: Тип задачи (chat, file, image:gen, image:edit)
        task_data: Данные задачи
        priority: Приоритет (high, normal, low)

    Returns:
        task_id: Идентификатор задачи
    """
    if not _use_redis:
        raise RuntimeError("Redis не включён (USE_REDIS=false)")

    q = _get_queue()
    if not q:
        raise RuntimeError("Не удалось подключиться к Redis")

    return q.enqueue(queue_type, task_data, priority)


def get_task_result(task_id: str, wait: bool = False, timeout: int = 30):
    """
    Получает результат задачи.

    Args:
        task_id: Идентификатор задачи
        wait: Ждать ли завершения
        timeout: Максимальное время ожидания
    """
    if not _use_redis:
        raise RuntimeError("Redis не включён")

    q = _get_queue()
    if not q:
        raise RuntimeError("Не удалось подключиться к Redis")

    return q.get_task_result(task_id, wait, timeout)


def close_redis_connection():
    """Закрывает подключение к Redis"""
    global _queue
    if _queue:
        _queue.close()
        _queue = None


# ==================== Mentor Agent State ====================

def get_mentor_state(user_id: int) -> dict | None:
    """
    Получает состояние ментора для пользователя.
    
    Returns:
        dict с ключами:
        - stage: str ("idle" | "question" | "feedback")
        - topic: str - тема проверки
        - question: str - текущий вопрос
        - context: str - контекст из ChromaDB
        - question_count: int - количество заданных вопросов
        - correct_count: int - количество правильных ответов
        или None если состояния нет
    """
    if _use_redis:
        q = _get_queue()
        if q:
            return q.get_user_state(user_id, "mentor_state")
    else:
        return user_mentor_state.get(user_id)
    return None


def set_mentor_state(user_id: int, state: dict):
    """Сохраняет состояние ментора для пользователя"""
    if _use_redis:
        q = _get_queue()
        if q:
            q.set_user_state(user_id, "mentor_state", state)
    else:
        global user_mentor_state
        user_mentor_state[user_id] = state


def clear_mentor_state(user_id: int):
    """Очищает состояние ментора для пользователя"""
    if _use_redis:
        q = _get_queue()
        if q:
            q.delete_user_state(user_id, "mentor_state")
    else:
        global user_mentor_state
        user_mentor_state.pop(user_id, None)


# ==================== Prompt Edit State ====================

def get_prompt_edit_state(user_id: int) -> dict | None:
    """
    Получает состояние редактирования промпта для пользователя.
    """
    if _use_redis:
        q = _get_queue()
        if q:
            return q.get_user_state(user_id, "prompt_edit")
    return _prompt_edit_states.get(user_id)


def set_prompt_edit_state(user_id: int, state: dict):
    """Сохраняет состояние редактирования промпта для пользователя"""
    if _use_redis:
        q = _get_queue()
        if q:
            q.set_user_state(user_id, "prompt_edit", state)
            return
    global _prompt_edit_states
    _prompt_edit_states[user_id] = state


def clear_prompt_edit_state(user_id: int):
    """Очищает состояние редактирования промпта"""
    if _use_redis:
        q = _get_queue()
        if q:
            q.delete_user_state(user_id, "prompt_edit")
            return
    global _prompt_edit_states
    _prompt_edit_states.pop(user_id, None)


if os.getenv("USE_REDIS", "false").lower() != "true":
    _prompt_edit_states: dict = {}
else:
    _prompt_edit_states = {}

