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
        model: Optional[str] = None
    ) -> str:
        try:
            response = await self.client.chat(
                messages=[Message(role="user", content=prompt)],
                temperature=temperature,
                max_tokens=max_tokens,
                model=model or self.model
            )
            return response.choices[0].message.content
        except AuthenticationError as e:
            raise RuntimeError(f"Ошибка аутентификации GigaChat: {e}")
        except RateLimitError as e:
            raise RuntimeError(f"Достигнут лимит скорости. Повторите через {e.retry_after} сек.")
        except BadRequestError as e:
            raise RuntimeError(f"Неверный запрос: {e}")
        except ForbiddenError as e:
            raise RuntimeError(f"Отказано в доступе: {e}")
        except NotFoundError as e:
            raise RuntimeError(f"Ресурс не найден: {e}")
        except RequestEntityTooLargeError as e:
            raise RuntimeError(f"Слишком большой объем запроса: {e}")
        except ServerError as e:
            raise RuntimeError(f"Ошибка сервера GigaChat: {e}")
        except GigaChatException as e:
            raise RuntimeError(f"Ошибка GigaChat: {e}")
        except Exception as e:
            raise RuntimeError(f"Ошибка при генерации текста: {e}")
