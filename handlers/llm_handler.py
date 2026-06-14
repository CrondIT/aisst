"""Обработчик прямых LLM-режимов (gigachatpro, chatgpt, gemini)."""
import os
import token_utils
from fastapi import Request

import db
import max_api
from prompt_builder import full_prompt
from shared.message_utils import (
    get_file_extracted_text,
    check_and_send_formatted
)
from handlers.base import ModeHandler
from global_state import (
    get_user_context_async,
    set_user_context_async,
    MAX_CONTEXT_MESSAGES,
    MODELS,
    TEMP_DIR,
    get_user_gemini_image_queue,
    clear_user_gemini_image_queue,
    get_user_gemini_files,
    get_user_chat_image_queue,
    clear_user_chat_image_queue,
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

        # 3. Проверяем, есть ли файл
        extracted_text = get_file_extracted_text(user_id)

        # Для gemini — собираем очередь изображений и накопленные файлы
        gemini_image_paths = None
        if self.mode_name == "gemini":
            gemini_image_paths = get_user_gemini_image_queue(user_id)
            clear_user_gemini_image_queue(user_id)
            gemini_files = get_user_gemini_files(user_id)
            if gemini_files:
                parts = []
                for f in gemini_files:
                    parts.append(f"[{f['name']}]:\n{f['text']}")
                file_text = "\n\n---\n\n".join(parts)
                extracted_text = (
                    f"{file_text}\n\n{extracted_text}"
                    if extracted_text else file_text
                )

        # Для chat (OpenAI) — собираем очередь изображений
        chat_image_paths = None
        if self.mode_name == "chat":
            chat_image_paths = get_user_chat_image_queue(user_id)
            clear_user_chat_image_queue(user_id)

        # full_prompt сам обработает запрос формата (JSON-схему) и контекст
        user_prompt = await full_prompt(
            user_id, user_text, extracted_text, context=context
        )

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
        chat_kwargs = {"messages": user_prompt, "model": self.model_name}
        if gemini_image_paths:
            chat_kwargs["image_paths"] = gemini_image_paths
        if chat_image_paths:
            chat_kwargs["image_paths"] = chat_image_paths
            chat_kwargs["enable_image_generation"] = True
        answer = await client.chat(**chat_kwargs)

        # 6. Обрабатываем результат (ChatResult для OpenAI, str для остальных)
        if isinstance(answer, str):
            text_answer = answer
            image_bytes = None
        else:
            text_answer = answer.text or ""
            image_bytes = answer.image

        # Если сгенерировано изображение — отправляем его
        if image_bytes:
            os.makedirs(TEMP_DIR, exist_ok=True)
            await max_api.send_generated_image(
                user_id=user_id,
                image_bytes=image_bytes,
                caption=text_answer or "Сгенерировано изображение",
            )

        # 7. Добавляем ответ модели в контекст
        context.append({
            "role": "assistant",
            "content": text_answer or "✅ Изображение сгенерировано",
        })

        # 8. Обрезаем контекст по количеству сообщений (user+assistant пары)
        system_msgs = [m for m in context if m.get("role") == "system"]
        non_system = [m for m in context if m.get("role") != "system"]
        if len(non_system) > MAX_CONTEXT_MESSAGES * 2:
            non_system = non_system[-(MAX_CONTEXT_MESSAGES * 2):]
        context = system_msgs + non_system

        # 9. Сохраняем контекст (в кэш и в БД)
        await set_user_context_async(user_id, self.mode_name, context)

        # 10. Биллинг
        from cost_tracker import calculate_cost
        model_name_for_cost = MODELS.get(self.mode_name) or self.model_name
        usage = answer.usage if not isinstance(answer, str) else None
        cost = calculate_cost(usage=usage, model=model_name_for_cost, mode=self.mode_name)
        await db.add_billing(user_id, self.mode_name, user_text, 0, cost)

        # 11. Если пользователь запросил формат — создаём и отправляем файл
        formatted = await check_and_send_formatted(
            user_text, user_id, text_answer or ""
        )
        return formatted if formatted is not None else (text_answer or "✅ Изображение сгенерировано")
