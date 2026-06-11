"""
Централизованный конфиг AI-моделей.
Единственный источник правды для имён моделей и лимитов токенов.
Читает значения из .env через Pydantic Settings.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelSettings(BaseSettings):
    """Реестр AI-моделей. Все поля маппятся на env-переменные с префиксом MODEL_."""

    model_config = SettingsConfigDict(
        env_prefix="MODEL_",
        case_sensitive=False,
        extra="ignore",
    )

    # GigaChat
    rag_llm: str = "GigaChat-2-Pro"
    embeddings: str = "Embeddings-2"
    gigachatpro: str = "GigaChat-2-Pro"
    aiagent: str = "GigaChat-2"

    # OpenAI
    chat: str = "gpt-5.2-chat-latest"
    ai_file: str = "gpt-5.2-chat-latest"
    chatgpt: str = "gpt-5.2-chat-latest"

    # Gemini
    gemini: str = "gemini-2.5-pro"
    image: str = "gemini-3.1-flash-image-preview"


settings = ModelSettings()

# Единый реестр: режим → имя модели
MODELS: dict[str, str] = {
    "rag_llm":     settings.rag_llm,
    "embeddings":  settings.embeddings,
    "gigachatpro": settings.gigachatpro,
    "aiagent":     settings.aiagent,
    "chat":        settings.chat,
    "ai_file":     settings.ai_file,
    "chatgpt":     settings.chatgpt,
    "gemini":      settings.gemini,
    "image":       settings.image,
}

# Лимиты токенов по моделям (нижний регистр ключей)
_TOKEN_LIMITS: dict[str, int] = {
    # OpenAI
    "gpt-5.2": 128000,
    "gpt-5.1": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4-turbo": 128000,
    "gpt-4o": 128000,
    "gpt-4": 8192,
    "gpt-5.2-chat-latest": 128000,
    # DALL-E
    "dall-e-3": 4096,
    # Gemini
    "imagen-4.0-generate-001": 8192,
    "gemini-2.5-pro": 2097152,
    "gemini-2.5-flash-image": 32768,
    "gemini-3-pro-image-preview": 32768,
    "gemini-1.5-pro": 1048576,
    "gemini-1.0-pro": 32768,
    # GigaChat
    "gigachat-2-max": 128000,
    "gigachat-2-pro": 32768,
    "gigachat-2": 8192,
    "gigachat": 8192,
    # GigaChat Embeddings
    "embeddings": 8192,
    "embeddings-2": 8192,
}


def get_token_limit(model_name: str) -> int:
    """Возвращает максимальный лимит токенов для модели. Дефолт: 4096."""
    return _TOKEN_LIMITS.get(model_name.lower(), 4096)
