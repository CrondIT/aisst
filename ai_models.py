import os
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


class OpenAIClient:
    """Async-обёртка для OpenAI ChatGPT."""

    def __init__(self, api_key: str):
        import httpx
        from openai import AsyncOpenAI
        from utils import get_socks_proxy_mount

        # Если PROXY_IP задан в .env — подключаем SOCKS5-прокси.
        # AsyncOpenAI принимает httpx.AsyncClient через параметр http_client.
        # close() OpenAI-клиента автоматически закрывает переданный httpx-клиент.
        transport = get_socks_proxy_mount()
        if transport:
            http_client = httpx.AsyncClient(transport=transport)
            self._client = AsyncOpenAI(api_key=api_key, http_client=http_client)
            logger.info("OpenAI клиент создан с SOCKS5-прокси")
        else:
            self._client = AsyncOpenAI(api_key=api_key)

    async def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        model: str = "gpt-5.2-chat-latest",
        enable_web_search: bool = True,
    ) -> str:
        model_name = model
        logger.info(
            f"OpenAI.chat: модель={model_name}, "
            f"сообщений={len(messages)}, "
            f"temperature={temperature}, max_tokens={max_tokens}"
        )
         # Инструменты подключаем только если разрешён поиск
        tools = []
        if enable_web_search:
            tools.append(
                {
                    "type": "web_search",
                }
            )
        try:
            # gpt-5.x работает только через responses API 
            # (не chat.completions).
            # Параметры temperature и max_output_tokens не передаём
            response = await self._client.responses.create(
                model=model_name,
                input=messages,
                tools=tools,
                timeout=300,
            )
            content = response.output_text
            if not content:
                raise RuntimeError("Пустой контент в ответе OpenAI")
            logger.info(f"OpenAI.chat: ответ ({len(content)} символов)")
            return content
        except RuntimeError:
            raise
        except Exception as e:
            # Логируем без exc_info чтобы избежать потенциальных
            # проблем loguru при нестандартных типах исключений SDK
            err_type = type(e).__name__
            err_msg = str(e)
            logger.error(f"OpenAI error [{err_type}]: {err_msg}")
            raise RuntimeError(f"Ошибка OpenAI ({err_type}): {err_msg}")

    async def generate_image(
        self,
        image_paths: list[str] | None = None,
        prompt: str = "",
        model: str = "gpt-image-2",
        n: int = 1,
        size: str = "1024x1024",
    ) -> tuple[bytes | None, str | None]:
        """
        Генерирует или редактирует изображение через OpenAI-совместимый API.

        Если передан image_paths — пытается редактировать через images.edit,
        иначе генерирует новое через images.generate.

        Args:
            image_paths: список путей к изображениям (пустой = генерация)
            prompt: текстовое описание
            model: модель (по умолчанию gpt-image-2)
            n: количество изображений (должно быть >= 1)
            size: размер изображения

        Returns:
            (image_bytes, None) — изображение в формате JPEG
            (None, error_message) — ошибка
        """
        import io
        from PIL import Image

        # Валидация параметра n
        if n < 1:
            logger.error(f"OpenAI.generate_image: некорректное значение n={n}, используем n=1")
            n = 1

        logger.info(
            f"OpenAI.generate_image: модель={model}, "
            f"входных_изображений={len([p for p in (image_paths or []) if p])}, "
            f"генерируемых_изображений={n}, "
            f"размер={size}, "
            f"запрос={prompt[:100]}"
        )
        try:
            if image_paths:
                image_path = next(
                    (p for p in image_paths if p and os.path.exists(p)), None
                )
                if image_path is None:
                    raise FileNotFoundError("Нет доступных изображений для редактирования")
                
                # Конвертируем изображение в PNG для правильного MIME-типа
                img = Image.open(image_path)
                png_buffer = io.BytesIO()
                img.save(png_buffer, format="PNG")
                png_buffer.seek(0)
                
                # Передаём как tuple (filename, file_data, mime_type)
                response = await self._client.images.edit(
                    model=model,
                    image=("image.png", png_buffer, "image/png"),
                    prompt=prompt,
                    n=n,
                    size=size,
                )
            else:
                response = await self._client.images.generate(
                    model=model,
                    prompt=prompt,
                    n=n,
                    size=size,
                )

            if not response.data:
                raise RuntimeError("Пустой ответ от OpenAI generate_image")

            # Проверяем, есть ли b64_json (если API всё же вернул base64)
            first_image = response.data[0]
            if hasattr(first_image, 'b64_json') and first_image.b64_json:
                from base64 import b64decode
                image_bytes = b64decode(first_image.b64_json)
            elif hasattr(first_image, 'url') and first_image.url:
                # Скачиваем изображение по URL
                import httpx
                async with httpx.AsyncClient() as http_client:
                    img_response = await http_client.get(first_image.url, timeout=60.0)
                    img_response.raise_for_status()
                    image_bytes = img_response.content
            else:
                raise RuntimeError("В ответе OpenAI нет ни b64_json, ни url")

            # Конвертируем в JPEG
            img = Image.open(io.BytesIO(image_bytes))
            output_buffer = io.BytesIO()
            img.save(output_buffer, "JPEG", quality=95)
            output_buffer.seek(0)
            result_bytes = output_buffer.getvalue()

            logger.info(
                f"OpenAI.generate_image: изображение "
                f"({len(result_bytes)} байт)"
            )
            return result_bytes, None

        except Exception as e:
            err_type = type(e).__name__
            err_msg = str(e)
            
            # Пытаемся извлечь детали ошибки из OpenAI API
            error_detail = err_msg
            if hasattr(e, 'response'):
                try:
                    response_json = e.response.json()
                    if 'error' in response_json:
                        error_info = response_json['error']
                        if isinstance(error_info, dict):
                            error_detail = error_info.get('message', err_msg)
                        else:
                            error_detail = str(error_info)
                except Exception:
                    pass
            
            logger.error(
                f"OpenAI.generate_image: ошибка [{err_type}]: {error_detail}",
                exc_info=True,
            )
            return None, f"Ошибка генерации изображения ({err_type}): {error_detail}"

    async def close(self):
        await self._client.close()

    async def list_models(self) -> str:
        """Возвращает список доступных моделей OpenAI."""
        try:
            models = await self._client.models.list()
            lines = ["🤖 Доступные модели OpenAI:"]
            for model in models.data:
                lines.append(f"🔹 `{model.id}`")
            result = "\n".join(lines)
            logger.info(f"OpenAI.list_models: найдено {len(models.data)} моделей")
            return result
        except Exception as e:
            err_msg = str(e)
            logger.error(f"OpenAI.list_models: ошибка [{type(e).__name__}]: {err_msg}")
            return f"❌ Ошибка при получении моделей OpenAI: {err_msg}"


class GeminiClient:
    """Async-обёртка для Google Gemini."""

    def __init__(self, api_key: str):
        import os
        from google import genai
        from utils import get_proxy_url

        # google-genai SDK не поддерживает поле proxy в HttpOptions.
        # Используем временную установку env-переменных: httpx читает
        # ALL_PROXY / HTTPS_PROXY в своём __init__ 
        # (trust_env=True по умолчанию)
        # — то есть в момент создания genai.Client, а не каждого запроса.
        # После создания клиента окружение восстанавливается, чтобы прокси
        # не подхватили GigaChat, MAX API и другие http-клиенты процесса.
        # SOCKS5 требует установленного пакета httpx-socks.
        proxy_url = get_proxy_url()
        _saved: dict[str, str | None] = {}
        if proxy_url:
            for key in ("ALL_PROXY", "HTTPS_PROXY"):
                _saved[key] = os.environ.get(key)
                os.environ[key] = proxy_url

        self._client = genai.Client(api_key=api_key)

        # Восстанавливаем окружение — 
        # прокси уже захвачен httpx-клиентом внутри genai
        if proxy_url:
            for key, prev in _saved.items():
                if prev is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = prev
            logger.info("Gemini клиент создан с SOCKS5-прокси")

        # _async_httpx_client в genai создаётся лениво (при первом запросе).
        # asyncio-runner в Python 3.12 вызывает aclose() при cleanup до того,
        # как клиент был инициализирован → AttributeError.
        # Патчим aclose() на экземпляре, чтобы перехватить этот случай.
        _original_aclose = self._client._api_client.aclose

        async def _safe_aclose():
            try:
                await _original_aclose()
            except AttributeError:
                pass

        self._client._api_client.aclose = _safe_aclose

    async def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        model: str = "gemini-2.5-pro",
    ) -> str:
        model_name = model
        logger.info(
            f"Gemini.chat: модель={model_name}, "
            f"сообщений={len(messages)}, "
            f"temperature={temperature}, max_tokens={max_tokens}"
        )
        try:
            system_instruction = None
            contents = []
            for msg in messages:
                role = msg.get("role", "user")
                content_text = msg.get("content", "")
                if role == "system":
                    system_instruction = content_text
                elif role == "assistant":
                    contents.append({
                        "role": "model",
                        "parts": [{"text": content_text}],
                    })
                else:
                    contents.append({
                        "role": "user",
                        "parts": [{"text": content_text}],
                    })

            config = {
                "temperature": temperature,
                "max_output_tokens": max_tokens,
            }
            if system_instruction:
                config["system_instruction"] = system_instruction

            response = await self._client.aio.models.generate_content(
                model=model_name,
                contents=contents,
                config=config,
            )
            content = response.text
            if content is None:
                raise RuntimeError("Пустой контент в ответе Gemini")
            logger.info(f"Gemini.chat: ответ ({len(content)} символов)")
            return content
        except Exception as e:
            logger.error(f"Gemini error: {e}", exc_info=True)
            raise RuntimeError(f"Ошибка Gemini: {e}")

    async def close(self):
        """
        Закрывает внутренний httpx-клиент Gemini.
        _async_httpx_client создаётся лениво при первом запросе,
        поэтому если запросов не было — AttributeError игнорируется.
        """
        try:
            await self._client._api_client.aclose()
        except AttributeError:
            pass

    async def list_models(self) -> str:
        """Возвращает список доступных моделей Gemini."""
        try:
            models = self._client.models.list()
            lines = ["🤖 Доступные модели Gemini:"]
            for model in models:
                model_id = model.name.split("/")[-1] if "/" in model.name else model.name
                input_tokens = getattr(model, "input_token_limit", "н/д")
                output_tokens = getattr(model, "output_token_limit", "н/д")
                methods = ", ".join(getattr(model, "supported_actions", []))
                temp = (
                    f"{model.temperature:.1f}"
                    if hasattr(model, "temperature") and model.temperature is not None
                    else "не задана"
                )
                lines.append(
                    f"🔹 *{model_id}*\n"
                    f"  Вход: {input_tokens} токенов\n"
                    f"  Выход: {output_tokens} токенов\n"
                    f"  Методы: {methods}\n"
                    f"  Температура: {temp}"
                )
            result = "\n\n".join(lines)
            logger.info(f"Gemini.list_models: найдено {len(list(models))} моделей")
            return result
        except Exception as e:
            err_msg = str(e)
            logger.error(f"Gemini.list_models: ошибка [{type(e).__name__}]: {err_msg}")
            return f"❌ Ошибка при получении моделей Gemini: {err_msg}"

    async def generate_image(
        self,
        image_paths: list[str],
        prompt: str,
        model: str = None,
    ) -> tuple[bytes | None, str | None]:
        """
        Генерирует новое изображение или редактирует существующие.

        Args:
            image_paths: список путей к изображениям (пустой = генерация)
            prompt: текстовый запрос
            model: модель (по умолчанию берётся из global_state.MODELS)

        Returns:
            (image_bytes, None) — если ответ изображение
            (None, text_response) — если ответ текст
        """
        import io
        from PIL import Image
        from google.genai import types
        from global_state import MODELS

        model_name = model or MODELS["image"]

        contents = []
        if image_paths:
            for path in image_paths:
                if path and os.path.exists(path):
                    img = Image.open(path)
                    buffer = io.BytesIO()
                    img.save(buffer, format="PNG")
                    buffer.seek(0)
                    contents.append(
                        types.Part.from_bytes(
                            data=buffer.read(),
                            mime_type="image/png",
                        )
                    )
            contents.append(prompt)
        else:
            contents = [prompt]

        config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        )

        logger.info(
            f"Gemini.generate_image: модель={model_name}, "
            f"изображений={len([p for p in image_paths if p])}, "
            f"запрос={prompt[:100]}"
        )

        try:
            response = await self._client.aio.models.generate_content(
                model=model_name,
                contents=contents,
                config=config,
            )
        except Exception as e:
            err_type = type(e).__name__
            err_msg = str(e)
            logger.error(
                f"Gemini.generate_image: SDK ошибка [{err_type}]: {err_msg}",
                exc_info=True,
            )
            raise RuntimeError(f"Ошибка Gemini generate_image: {err_msg}")

        # Логируем тип ответа
        logger.info(
            f"Gemini.generate_image: response type={type(response).__name__}"
        )

        # Парсим ответ через response.parts (официальный путь SDK для image-моделей)
        if hasattr(response, "parts") and response.parts:
            for part in response.parts:
                try:
                    if hasattr(part, "inline_data") and part.inline_data is not None:
                        image_bytes = part.inline_data.data
                        if isinstance(image_bytes, str):
                            from base64 import b64decode
                            image_bytes = b64decode(image_bytes)
                        img = Image.open(io.BytesIO(image_bytes))
                        output_buffer = io.BytesIO()
                        img.save(output_buffer, "JPEG", quality=95)
                        output_buffer.seek(0)
                        result_bytes = output_buffer.getvalue()
                        logger.info(
                            f"Gemini.generate_image: изображение "
                            f"({len(result_bytes)} байт)"
                        )
                        return result_bytes, None
                    elif hasattr(part, "text") and part.text is not None:
                        logger.info(
                            f"Gemini.generate_image: текстовый ответ "
                            f"({len(part.text)} символов)"
                        )
                        return None, part.text
                except Exception as e:
                    logger.error(
                        f"Gemini.generate_image: ошибка парсинга part "
                        f"[{type(e).__name__}]: {e}",
                        exc_info=True,
                    )
                    continue

        # Фоллбэк: парсинг через candidates (если response.parts недоступен)
        candidates = None
        try:
            candidates = response.candidates
        except KeyError as e:
            logger.error(
                f"Gemini.generate_image: KeyError '{e}' — "
                f"вероятно API вернул ошибку. "
                f"response={response}"
            )
            raise RuntimeError(
                f"Gemini API вернул ошибку: {e}. "
                f"Проверьте модель и запрос."
            )
        except Exception as e:
            logger.error(
                f"Gemini.generate_image: ошибка доступа к candidates "
                f"[{type(e).__name__}]: {e}",
                exc_info=True,
            )

        if not candidates:
            raise RuntimeError(
                f"Пустой ответ от Gemini generate_image. "
                f"response type={type(response).__name__}"
            )

        for candidate in candidates:
            try:
                if not candidate.content or not candidate.content.parts:
                    continue
                for part in candidate.content.parts:
                    if hasattr(part, "inline_data") and part.inline_data is not None:
                        image_bytes = part.inline_data.data
                        if isinstance(image_bytes, str):
                            from base64 import b64decode
                            image_bytes = b64decode(image_bytes)
                        img = Image.open(io.BytesIO(image_bytes))
                        output_buffer = io.BytesIO()
                        img.save(output_buffer, "JPEG", quality=95)
                        output_buffer.seek(0)
                        result_bytes = output_buffer.getvalue()
                        logger.info(
                            f"Gemini.generate_image: изображение "
                            f"({len(result_bytes)} байт)"
                        )
                        return result_bytes, None
                    elif hasattr(part, "text") and part.text is not None:
                        logger.info(
                            f"Gemini.generate_image: текстовый ответ "
                            f"({len(part.text)} символов)"
                        )
                        return None, part.text
            except Exception as e:
                logger.error(
                    f"Gemini.generate_image: ошибка парсинга candidate "
                    f"[{type(e).__name__}]: {e}",
                    exc_info=True,
                )
                continue

        raise ValueError("Не удалось получить изображение из ответа модели")



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
                logger.error("GigaChat: 402 Payment Required - закончились токены")
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
                logger.error("GigaChat: 402 Payment Required - закончились токены")
                raise RuntimeError("⏰ Услуга временно недоступна. Закончились токены на тарифе GigaChat.")
            logger.error(f"Unexpected error in chat(): {e}", exc_info=True)
            raise RuntimeError(f"Ошибка при генерации текста: {e}")
