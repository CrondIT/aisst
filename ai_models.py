from gigachat.models import Chat, Messages, MessagesRole
from typing import Optional, List
from utils import logger
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
        self.model = model

    async def generate(
        self,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 512,
        model: Optional[str] = None,
        async_mode: bool = True,
    ) -> str:
        model_name = model or self.model
        logger.info(
            f"GigaChat: запрос к модели {model_name},"
            f" temperature={temperature}, max_tokens={max_tokens}"
        )
        chat = Chat(
            messages=[Messages(role=MessagesRole.USER, content=prompt)],
            model=model_name,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        try:
            response = await self.client.achat(chat)
            logger.debug(f"GigaChat API response: {response}")

            if not hasattr(response, 'choices') or not response.choices:
                logger.error(f"Пустой ответ от GigaChat API: {response}")
                raise RuntimeError("Пустой ответ от GigaChat API")

            content = response.choices[0].message.content
            if content is None:
                logger.error(f"content=None в ответе: {response}")
                raise RuntimeError("Пустой контент в ответе GigaChat")

            logger.info(f"GigaChat: ответ ({len(content)} символов)")
            return content
        except AuthenticationError as e:
            logger.error(f"Ошибка аутентификации GigaChat: {e}")
            raise RuntimeError(f"Ошибка аутентификации GigaChat: {e}")
        except RateLimitError as e:
            logger.error(
                f"Rate limit GigaChat: повторить через {e.retry_after} сек"
            )
            raise RuntimeError(
                f"Достигнут лимит скорости. "
                f"Повторите через {e.retry_after} сек."
            )
        except BadRequestError as e:
            logger.error(f"Bad request GigaChat: {e}")
            raise RuntimeError(f"Неверный запрос: {e}")
        except ForbiddenError as e:
            logger.error(f"Access forbidden GigaChat: {e}")
            raise RuntimeError(f"Отказано в доступе: {e}")
        except NotFoundError as e:
            logger.error(f"Resource not found GigaChat: {e}")
            raise RuntimeError(f"Ресурс не найден: {e}")
        except RequestEntityTooLargeError as e:
            logger.error(f"Request too large GigaChat: {e}")
            raise RuntimeError(f"Слишком большой объем запроса: {e}")
        except ServerError as e:
            logger.error(f"Server error GigaChat: {e}")
            raise RuntimeError(f"Ошибка сервера GigaChat: {e}")
        except GigaChatException as e:
            logger.error(f"GigaChat error: {e}")
            raise RuntimeError(f"Ошибка GigaChat: {e}")
        except Exception as e:
            error_str = str(e).lower()
            if "402" in error_str or "payment" in error_str or "payment required" in error_str:
                logger.error(f"GigaChat: 402 Payment Required - закончились токены")
                raise RuntimeError("⏰ Услуга временно недоступна. Закончились токены на тарифе GigaChat.")
            logger.error(f"Unexpected error in generate(): {e}", exc_info=True)
            raise RuntimeError(f"Ошибка при генерации текста: {e}")

    async def chat(
        self,
        messages: List[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        model: Optional[str] = None,
    ) -> str:
        """
        Чат с историей сообщений через GigaChat.
        
        Args:
            messages: Список сообщений в формате LangChain/OpenAI:
                [{"role": "system", "content": "..."},
                 {"role": "user", "content": "..."},
                 {"role": "assistant", "content": "..."}]
            temperature: Температура генерации
            max_tokens: Максимальное количество токенов в ответе
            model: Название модели (по умолчанию используется self.model)
        
        Returns:
            Текст ответа модели
        """
        model_name = model or self.model
        logger.info(
            f"GigaChat.chat: модель={model_name}, "
            f"сообщений={len(messages)}, "
            f"temperature={temperature}, max_tokens={max_tokens}"
        )
        
        # Преобразуем сообщения в формат GigaChat
        gigachat_messages = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            
            # Маппинг ролей
            if role == "system":
                gigachat_role = MessagesRole.SYSTEM
            elif role == "assistant":
                gigachat_role = MessagesRole.ASSISTANT
            elif role == "user":
                gigachat_role = MessagesRole.USER
            else:
                gigachat_role = MessagesRole.USER
            
            gigachat_messages.append(
                Messages(role=gigachat_role, content=content)
            )
        
        chat = Chat(
            messages=gigachat_messages,
            model=model_name,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        
        try:
            response = await self.client.achat(chat)
            logger.debug(f"GigaChat API response: {response}")

            if not hasattr(response, 'choices') or not response.choices:
                logger.error(f"Пустой ответ от GigaChat API: {response}")
                raise RuntimeError("Пустой ответ от GigaChat API")

            content = response.choices[0].message.content
            if content is None:
                logger.error(f"content=None в ответе: {response}")
                raise RuntimeError("Пустой контент в ответе GigaChat")

            logger.info(f"GigaChat.chat: ответ ({len(content)} символов)")
            return content
        except AuthenticationError as e:
            logger.error(f"Ошибка аутентификации GigaChat: {e}")
            raise RuntimeError(f"Ошибка аутентификации GigaChat: {e}")
        except RateLimitError as e:
            logger.error(f"Rate limit GigaChat: {e.retry_after} сек")
            raise RuntimeError(f"Достигнут лимит скорости. Повторите через {e.retry_after} сек.")
        except BadRequestError as e:
            logger.error(f"Bad request GigaChat: {e}")
            raise RuntimeError(f"Неверный запрос: {e}")
        except ForbiddenError as e:
            logger.error(f"Access forbidden GigaChat: {e}")
            raise RuntimeError(f"Отказано в доступе: {e}")
        except NotFoundError as e:
            logger.error(f"Resource not found GigaChat: {e}")
            raise RuntimeError(f"Ресурс не найден: {e}")
        except RequestEntityTooLargeError as e:
            logger.error(f"Request too large GigaChat: {e}")
            raise RuntimeError(f"Слишком большой объем запроса: {e}")
        except ServerError as e:
            logger.error(f"Server error GigaChat: {e}")
            raise RuntimeError(f"Ошибка сервера GigaChat: {e}")
        except GigaChatException as e:
            logger.error(f"GigaChat error: {e}")
            raise RuntimeError(f"Ошибка GigaChat: {e}")
        except Exception as e:
            error_str = str(e).lower()
            if "402" in error_str or "payment" in error_str or "payment required" in error_str:
                logger.error(f"GigaChat: 402 Payment Required - закончились токены")
                raise RuntimeError("⏰ Услуга временно недоступна. Закончились токены на тарифе GigaChat.")
            logger.error(f"Unexpected error in chat(): {e}", exc_info=True)
            raise RuntimeError(f"Ошибка при генерации текста: {e}")
