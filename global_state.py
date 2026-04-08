import os
from dotenv import load_dotenv

load_dotenv(override=True)

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

# ─── Настройки webhook ───
# URL, на который MAX будет присылать обновления (строго HTTPS, порт 443)
# Для продакшена укажите реальный домен,
# например: https://mybot.example.com/webhook
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
# Секрет для проверки подлинности webhook (задайте в .env или оставьте пустым)
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
