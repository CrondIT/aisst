"""Обработчик прямых LLM-режимов (gigachatpro, chatgpt, gemini)."""
import token_utils
from fastapi import Request

import db
from prompt_builder import full_prompt
from shared.message_utils import (
    get_file_extracted_text,
    check_and_send_formatted
)
from file_output_utils import docx_utils, pdf_utils, xlsx_utils, rtf_utils
from handlers.base import ModeHandler
from global_state import (
    get_user_context_async,
    set_user_context_async,
    MAX_CONTEXT_MESSAGES,
    MODELS,
)
from utils import logger


class LlmDirectHandler(ModeHandler):
    """Параметризованный обработчик для прямых LLM-вызовов с историей.

    Объединяет идентичную логику режимов gigachatpro, chatgpt, gemini,
    отличающихся только клиентом, моделью и сообщением об ошибке.
    Сохраняет и передаёт контекст диалога (историю сообщений).
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

        # 1. Загружаем контекст (из кэша или БД)
        context = await get_user_context_async(user_id, self.mode_name)

        # 2. Добавляем сообщение пользователя
        context.append({"role": "user", "content": user_text})

        # 3. Проверяем, есть ли файл или запрошен ли формат файла
        extracted_text = get_file_extracted_text(user_id)

        # Проверяем, запросил ли пользователь конкретный формат (PDF, DOCX, XLSX, RTF)
        wants_format = (
            docx_utils.check_user_wants_word_format(user_text)
            or pdf_utils.check_user_wants_pdf_format(user_text)
            or xlsx_utils.check_user_wants_xlsx_format(user_text)
            or rtf_utils.check_user_wants_rtf_format(user_text)
        )

        if extracted_text or wants_format:
            # Есть файл или запрошен формат — full_prompt добавит JSON-схему
            user_prompt = await full_prompt(
                user_id, user_text, extracted_text, context=context
            )
        else:
            # Нет файла и формат не запрошен — используем контекст напрямую
            user_prompt = context

        # 4. Обрезаем контекст по лимиту токенов (только если нет файла,
        #    иначе full_prompt уже сделал обрезку)
        if not extracted_text:
            model_name_for_limits = MODELS.get(self.mode_name)
            truncated = token_utils.truncate_messages_for_token_limit(
                user_prompt,
                model=model_name_for_limits,
                reserve_tokens=2500,
            )
            if len(truncated) > MAX_CONTEXT_MESSAGES:
                truncated = truncated[-MAX_CONTEXT_MESSAGES:]
            user_prompt = truncated

        logger.info(
            f"{self.mode_name}: user_id={user_id}, "
            f"сообщений={len(user_prompt)}"
        )

        # 5. Отправляем в LLM
        answer = await client.chat(
            messages=user_prompt,
            model=self.model_name,
        )

        # 6. Добавляем ответ модели в контекст
        context.append({"role": "assistant", "content": answer})

        # 7. Обрезаем контекст по количеству сообщений (user+assistant пары)
        #    Оставляем system + последние MAX_CONTEXT_MESSAGES пар
        system_msgs = [m for m in context if m.get("role") == "system"]
        non_system = [m for m in context if m.get("role") != "system"]
        if len(non_system) > MAX_CONTEXT_MESSAGES * 2:
            non_system = non_system[-(MAX_CONTEXT_MESSAGES * 2):]
        context = system_msgs + non_system

        # 8. Сохраняем контекст (в кэш и в БД)
        await set_user_context_async(user_id, self.mode_name, context)

        # 9. Биллинг
        await db.add_billing(user_id, self.mode_name, user_text, 0, 5)

        # 10. Если пользователь запросил формат — создаём и отправляем файл
        formatted = await check_and_send_formatted(user_text, user_id, answer)
        return formatted if formatted is not None else answer
