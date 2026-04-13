import os
from dotenv import load_dotenv

# Global variables that need to be accessible across the entire project
user_contexts = {}  # Хранилище контекста для каждого пользователя и режима
user_modes = {}  # Хранит текущий режим для каждого пользователя
user_edit_data = {}  # Хранит данные для редактирования изображений
user_file_data = {}  # Хранит данные для анализа файлов
user_edit_pending = {}  # Хранит ожидание промпта для редактирования изображ.
user_previous_modes = {}  # Хранит предыдущий режим для каждого пользователя
edited_photo_id = {}  # Хранит ID отредактированного изображения
# Хранит путь к последнему отредактированному
# изображению для каждого пользователя
user_last_edited_images = {}
# Хранит очередь изображений для редактирования для каждого пользователя
user_edit_images_queue = {}
MAX_CONTEXT_MESSAGES = 5
MAX_REF_IMAGES = 6  # Максимальное количество изображений для редактирования

load_dotenv(override=True)

TEMP_DIR = os.path.join(os.path.dirname(__file__), "temp")

ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

# Данные бота
MAX_API_TOKEN = os.getenv("MAX_API_TOKEN")
MAX_BASE_URL = os.getenv("MAX_BASE_URL") or "https://platform-api.max.ru"

# Данные для подключения к GigaChat
GIGACHAT_CLIENT_ID = os.getenv("GIGACHAT_CLIENT_ID")
GIGACHAT_CLIENT_SECRET = os.getenv("GIGACHAT_CLIENT_SECRET")
GIGACHAT_API_KEY = os.getenv("GIGACHAT_API_KEY")
GIGACHAT_SCOPE = os.getenv("GIGACHAT_SCOPE") or "GIGACHAT_API_PERS"

# Данные для подключения к OpenAI и Gemini
OPENAI_API_KEY_CHAT = os.getenv("OPENAI_API_KEY")
OPENAI_API_KEY_IMAGE = os.getenv("OPENAI_API_KEY_IMAGE")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Модели для разных режимов
MODELS = {
    "chat": "gpt-5.2-chat-latest",
    "image": "gemini-3-pro-image-preview",
    "edit": "gemini-3-pro-image-preview",
    "ai_file": "gpt-5.2-chat-latest",
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

# ─── Прокси ───
PROXY_IP = os.getenv("PROXY_IP", "")
PROXY_PORT = int(os.getenv("PROXY_PORT", "1080"))
PROXY_USER = os.getenv("PROXY_USER", "")
PROXY_PASSWORD = os.getenv("PROXY_USER_PASSWORD", "")


def get_token_limit(model_name: str) -> int:
    """
    Get the maximum token limit for a specific model
    """
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
        # Giga Chat models

    }

    return limits.get(model_name, 4096)  # Default fallback
