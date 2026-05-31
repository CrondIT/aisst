"""Обработчик прямых LLM-режимов (gigachatpro, chatgpt, gemini)."""
from fastapi import Request

import db
from prompt_builder import full_prompt
from shared.message_utils import (
    get_file_extracted_text,
    check_and_send_formatted
)
from handlers.base import ModeHandler


class LlmDirectHandler(ModeHandler):
    """Параметризованный обработчик для прямых LLM-вызовов.

    Объединяет идентичную логику режимов gigachatpro, chatgpt, gemini,
    отличающихся только клиентом, моделью и сообщением об ошибке.
    """

    def __init__(
        self,
        client_attr: str,
        model_name: str,
        mode_name: str,
        error_msg: str,
    ):
        self.client_attr = client_attr
        self.model_name = model_name
        self.mode_name = mode_name
        self.error_msg = error_msg

    async def handle(
        self,
        request: Request,
        user_text: str,
        sender: dict,
    ) -> str | None:
        user_id = int(sender.get("user_id"))

        if not hasattr(request.app.state, self.client_attr):
            return self.error_msg

        client = getattr(request.app.state, self.client_attr)

        # проверяем есть ли файл для включения в контекст
        extracted_text = get_file_extracted_text(user_id)
        # получаем полный промпт с текстом файла, историей и контролем токенов
        user_prompt = await full_prompt(user_id, user_text, extracted_text)

        answer = await client.chat(
            messages=user_prompt,
            model=self.model_name,
        )

        await db.add_billing(user_id, self.mode_name, user_text, 0, 5)

        # Если пользователь запросил конкретный формат 
        # — создаём и отправляем файл
        formatted = await check_and_send_formatted(user_text, user_id, answer)
        return formatted if formatted is not None else answer
