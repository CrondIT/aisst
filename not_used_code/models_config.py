"""Configuration for AI models used by the bot."""
from google import genai
from openai import OpenAI
import token_utils
from global_state import (
    OPENAI_API_KEY_CHAT,
    OPENAI_API_KEY_IMAGE,
    GEMINI_API_KEY,
    MODELS,
)


# Инициализация клиентов OpenAI для разных режимов
client_chat = OpenAI(api_key=OPENAI_API_KEY_CHAT)
client_image = OpenAI(api_key=OPENAI_API_KEY_IMAGE)
# Инициализация клиента Gemini
client_edit_image = genai.Client(api_key=GEMINI_API_KEY)
# print(list(client_edit_image.models.list()))


async def get_gemini_models_info() -> str:
    """
    Возвращает информацию о доступных моделях Gemini в виде строки.
    """

    try:
        models = client_edit_image.models.list()
        lines = ["🤖 Доступные модели Gemini:\n"]
        print(models)

        for model in models:
            # Имя модели теперь в атрибуте 'name'
            model_id = model.name.split("/")[-1]
            input_tokens = model.input_token_limit
            output_tokens = model.output_token_limit

            # Новый атрибут 'supported_actions' вместо
            # 'supported_generation_methods'
            methods = ", ".join(model.supported_actions)

            # Температура может быть не у всех моделей
            temp = (
                f"{model.temperature:.1f}"
                if hasattr(model, "temperature")
                and model.temperature is not None
                else "не задана"
            )

            lines.append(
                f"🔹 *{model_id}*\n"
                f" Вход: {input_tokens} токенов\n"
                f" Выход: {output_tokens} токенов\n"
                f" Методы: {methods}\n"
                f" Температура: {temp}\n"
            )

        return "\n".join(lines)
    except Exception as e:
        return f"❌ Ошибка при получении моделей Gemini: {str(e)}"


async def get_openai_models_info() -> str:
    try:
        # УБИРАЕМ await — вызов синхронный!
        models = client_image.models.list()
        lines = ["🤖 Доступные модели OpenAI:\n"]
        for model in models:
            lines.append(f"🔹 `{model.id}`")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ Ошибка: `{e}`"


async def ask_gpt51_with_web_search(
    context_history: list,
    enable_web_search: bool = True,
) -> str:
    """
    Задать вопрос GPT-5.2 с опциональным интернет-поиском.

    :param context_history: История сообщений в формате:
        [
            {"role": "system", "content": "Ты полезный ассистент"},
            {"role": "user", "content": "Вопрос"}
        ]
    :param enable_web_search: Разрешить ли web search
    :return: Текст ответа модели
    """

    if not context_history:
        raise ValueError("context_history не должен быть пустым")

    # Инструменты подключаем только если разрешён поиск
    tools = []
    if enable_web_search:
        tools.append(
            {
                "type": "web_search",
            }
        )
    try:
        response = client_chat.responses.create(
            model=MODELS["chat"],
            input=context_history,
            tools=tools,
            timeout=300,
        )

        # Самый простой и безопасный способ получить текст
        return response.output_text.strip()

    except Exception as e:
        print("Ошибка при запросе к GPT:", e)
        raise


async def generate_image(prompt: str) -> str:
    """Генерирует изображение с помощью DALL-E"""
    model_name = MODELS["image"]  # Используем константу
    # Проверяем длину промпта на токены (ограничение для DALL-E)
    prompt_tokens = token_utils.token_counter.count_openai_tokens(
        prompt, model_name
    )
    max_tokens = token_utils.get_token_limit(model_name)

    if prompt_tokens > max_tokens:
        # Обрезаем промпт до допустимого размера
        avg_token_size = 4  # средний размер токена в символах
        max_chars = max_tokens * avg_token_size
        prompt = prompt[:max_chars]

    try:
        response = client_image.images.generate(
            model=model_name,
            prompt=prompt,
            size="1024x1024",
            quality="standard",
            n=1,
        )
        return response.data[0].url
    except Exception as e:
        raise Exception(f"Ошибка генерации изображения: {str(e)}")


async def transcribe_voice(file_path: str) -> str:
    """Преобразует голосовое сообщение в текст с помощью Whisper API."""
    with open(file_path, "rb") as audio_file:
        transcription = client_chat.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
        )
    return transcription.text
