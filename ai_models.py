# ai_models.py
# from gigachat.async_client import GigaChatAsync
from gigachat.models import Message
from typing import Optional
from gigachat import GigaChat
from gigachat.exceptions import (
    GigaChatException,
    AuthenticationError,
    RateLimitError,
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    RequestEntityTooLargeError,
    ServerError,
)


class GigaChatClient:
    def __init__(self, client: GigaChat, model: str = "GigaChat"):
        self.client = client
        self.model = model  # Сохраняем модель по умолчанию

    async def generate(
        self,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 512,
        model: Optional[str] = None  # Позволяет переопределить модель
    ) -> str:
        try:
            response = await self.client.chat(
                messages=[Message(role="user", content=prompt)],
                temperature=temperature,
                max_tokens=max_tokens,
                model=model or self.model
            )
            return response.choices[0].message.content
        except Exception as e:
            raise RuntimeError(f"Ошибка при генерации текста: {e}")
        except AuthenticationError as e:
            print(f"Ошибка аутентификации: {e}")
        except RateLimitError as e:
            print(f"Достигнут лимит скорости. "
                  f"Повторите попытку через {e.retry_after} секунд.")
        except BadRequestError as e:
            print(f"Неверный запрос: {e}")
        except ForbiddenError as e:
            print(f"Отказано в доступе: {e}")
        except NotFoundError as e:
            print(f"Запрошенный ресурс не найден: {e}")
        except RequestEntityTooLargeError as e:
            print(f"Слишком большой объем запроса: {e}")
        except ServerError as e:
            print(f"Ошибка сервера: {e}")
        except GigaChatException as e:
            print(f"Ошибка GigaChat: {e}")
