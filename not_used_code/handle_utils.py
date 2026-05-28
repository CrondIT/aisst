"""
Utility functions for handling user interactions, 
messages, and edit modes (MAX API).
Разработано для работы с несколькими Gunicorn воркерами через Redis.
"""

import os
import token_utils
import file_utils
import models_config
import ai_models
import image_edit_utils
import max_api
from global_state import (
    get_user_context,
    set_user_context,
    get_user_mode,
    set_user_mode,
    get_user_file_data,
    set_user_file_data,
    get_user_edit_data,
    set_user_edit_data,
    get_user_edit_queue,
    set_user_edit_queue,
    set_user_pending_delete,
    clear_user_pending_delete,
    MAX_CONTEXT_MESSAGES,
    MAX_REF_IMAGES,
    SYSTEM_PROMPTS,
    RTF_PROMPT,
    MODELS,
    COST_PER_PROMPT,
    COST_PER_ANSWER,
)
from file_output_utils import docx_utils
from file_output_utils import xlsx_utils
from file_output_utils import pdf_utils
from file_output_utils import rtf_utils
from file_output_utils.docx_utils import send_docx_response
from file_output_utils.pdf_utils import send_pdf_response
from file_output_utils.xlsx_utils import send_xlsx_response
from file_output_utils.rtf_utils import send_rtf_response


def initialize_user_context(user_id: int, current_mode: str):
    """Инициализирует контекст для текущего режима пользователя"""
    context = get_user_context(user_id, current_mode)
    if len(context) <= 1 and context[0].get("role") == "system":
        system_message = SYSTEM_PROMPTS.get(current_mode)
        new_context = [{"role": "system", "content": system_message}]
        set_user_context(user_id, current_mode, new_context)


async def handle_file_analysis_mode(
    user_id: int,
    user_message: str,
    sender: dict,
    request=None,
):
    """Handle the ai_file mode functionality separately (MAX API)."""
    from utils import logger

    giga_client = getattr(request, "app", None)
    giga_client = getattr(giga_client, "state", None)
    giga_client = getattr(giga_client, "giga_client", None)
    if giga_client is None:
        await max_api.send_message(
            user_id, "GigaChat клиент не инициализирован."
            )
        return

    wants_word_format = docx_utils.check_user_wants_word_format(user_message)
    wants_pdf_format = pdf_utils.check_user_wants_pdf_format(user_message)
    wants_excel_format = xlsx_utils.check_user_wants_xlsx_format(user_message)
    wants_rtf_format = rtf_utils.check_user_wants_rtf_format(user_message)

    if wants_word_format:
        user_message = user_message + " " + docx_utils.JSON_SCHEMA
    elif wants_pdf_format:
        user_message = user_message + " " + pdf_utils.JSON_SCHEMA_PDF
    elif wants_excel_format:
        user_message = user_message + " " + xlsx_utils.JSON_SCHEMA_EXCEL
    elif wants_rtf_format:
        user_message = user_message + " " + RTF_PROMPT

    file_data = get_user_file_data(user_id)
    
    if (
        user_message
        and file_data
        and "extracted_text" in file_data
    ):
        extracted_text = file_data["extracted_text"]
        model_name = models_config.MODELS.get("ai_file")

        max_tokens = token_utils.get_token_limit(model_name)
        reserved_tokens_for_context = 2500
        max_content_tokens = max_tokens - reserved_tokens_for_context

        avg_token_size = 3
        max_chars = min(
            len(extracted_text), max_content_tokens * avg_token_size
        )
        
        if len(extracted_text) > max_chars:
            truncated_extracted_text = extracted_text[:max_chars]
            await max_api.send_message(
                user_id,
                f"📝 Объем файла превышает лимит. Использую первую "
                f"часть текста ({max_chars} символов) для анализа."
            )
        else:
            truncated_extracted_text = extracted_text

        augmented_question = (
            f"Файл содержит следующий текст: "
            f"{truncated_extracted_text}\n\nВопрос: {user_message}"
        )

        question_tokens = token_utils.token_counter.count_openai_tokens(
            augmented_question, model_name
        )
        
        if question_tokens > max_content_tokens:
            content_and_header_text = (
                f"Файл содержит следующий текст: "
                f"{truncated_extracted_text}\n\nВопрос: "
            )
            content_and_header_tokens = (
                token_utils.token_counter.count_openai_tokens(
                    content_and_header_text, model_name
                )
            )

            available_for_question = (
                max_tokens - content_and_header_tokens - 500
            )

            if available_for_question > 0:
                max_question_chars = int(
                    available_for_question * avg_token_size
                )
                if len(user_message) > max_question_chars:
                    truncated_user_message = user_message[:max_question_chars]
                    augmented_question = (
                        f"Файл содержит следующий текст: "
                        f"{truncated_extracted_text}\n\n"
                        f"Вопрос: {truncated_user_message}"
                    )
                    await max_api.send_message(
                        user_id,
                        f"Вопрос сокращен до {len(truncated_user_message)} с. "
                        f"для укладывания в лимиты вместе с содержимым файла."
                    )
            else:
                max_total_chars = max_content_tokens * avg_token_size
                augmented_question = augmented_question[:max_total_chars]
                await max_api.send_message(
                    user_id,
                    f"Общий объем текста (файл+вопрос) сокращен "
                    f"до {max_total_chars} символов для укладывания в лимиты."
                )

        history = get_user_context(user_id, "ai_file")
        truncated_history = token_utils.truncate_messages_for_token_limit(
            history,
            model=model_name,
            reserve_tokens=reserved_tokens_for_context,
        )
        messages = truncated_history + [
            {"role": "user", "content": augmented_question}
        ]

        if len(messages) > MAX_CONTEXT_MESSAGES:
            messages = messages[-MAX_CONTEXT_MESSAGES:]

        try:
            if messages and messages[-1]["role"] == "user":
                token_counter = token_utils.token_counter
                total_tokens = token_counter.count_openai_messages_tokens(
                    messages, model_name
                )
                max_tokens = token_utils.get_token_limit(model_name)
                if total_tokens > max_tokens:
                    messages = token_utils.truncate_messages_for_token_limit(
                        messages,
                        model=model_name,
                        reserve_tokens=reserved_tokens_for_context,
                    )

                    total_tokens = token_counter.count_openai_messages_tokens(
                        messages, model_name
                    )
                    if (
                        total_tokens > max_tokens
                        and messages
                        and messages[-1]["role"] == "user"
                    ):
                        original_content = messages[-1]["content"]
                        remaining_tokens = max_tokens - (
                            total_tokens
                            - token_utils.token_counter.count_openai_tokens(
                                original_content, model_name
                            )
                        )
                        if remaining_tokens > 0:
                            max_content_chars = remaining_tokens * avg_token_size
                            messages[-1]["content"] = original_content[
                                :max_content_chars
                            ]

            system_message = SYSTEM_PROMPTS.get("ai_file")
            full_context = (
                [{"role": "system", "content": system_message}]
                + truncated_history
                + [{"role": "user", "content": augmented_question}]
            )

            prompt_tokens = (
                token_utils.token_counter.count_openai_messages_tokens(
                    full_context, model_name
                )
            )

            reply = await giga_client.chat(
                messages=full_context,
                model="GigaChat-2-Pro",
            )

            response_tokens = token_utils.token_counter.count_openai_tokens(
                reply, model_name
            )

            new_context = history + [
                {"role": "user", "content": augmented_question},
                {"role": "assistant", "content": reply}
            ]
            set_user_context(user_id, "ai_file", new_context)

            if wants_word_format:
                await send_docx_response(user_id, reply)
            elif wants_pdf_format:
                await send_pdf_response(user_id, reply)
            elif wants_excel_format:
                await send_xlsx_response(user_id, reply)
            elif wants_rtf_format:
                await send_rtf_response(user_id, reply)
            else:
                import json

                try:
                    parsed_reply = json.loads(reply)
                    if isinstance(parsed_reply, dict) and (
                        "meta" in parsed_reply or "blocks" in parsed_reply
                    ):
                        await max_api.send_message(
                            user_id,
                            "Я подготовил структурированный ответ. "
                            "В каком формате вы хотите получить результат?\n"
                            "/get_docx - для получения в формате Word\n"
                            "/get_pdf - для получения в формате PDF\n"
                            "/get_text - для получения в виде текста"
                        )
                        temp_data = get_user_file_data(user_id) or {}
                        if "temp_reply" not in temp_data:
                            temp_data["temp_reply"] = {}
                        temp_data["temp_reply"]["structured_reply"] = reply
                        set_user_file_data(user_id, temp_data)
                    else:
                        await max_api.send_message(user_id, reply)
                except json.JSONDecodeError:
                    await max_api.send_message(user_id, reply)

        except Exception as e:
            error_msg = str(e)
            if "too long" in error_msg.lower() or "token" in error_msg.lower():
                await max_api.send_message(
                    user_id,
                    "⚠️ Длинное сообщение (ai_file). Сократите пожалуйста."
                )
            else:
                await max_api.send_message(
                    user_id,
                    f"⚠️ Ошибка при обращении к модели: {str(e)}"
                )
    else:
        await max_api.send_message(
            user_id,
            "📁 Пожалуйста, сначала загрузите файл для анализа. "
            "Поддерживаются форматы: PDF, DOCX, TXT, XLSX, XLS"
        )


async def handle_image_edit_mode(
    user_id: int,
    user_message: str,
    sender: dict,
    file_path: str = None,
):
    """Handle the image edit mode functionality separately (MAX API)."""
    from utils import logger

    edited_file_path = None
    file_ext = ".png"

    try:
        model_name = MODELS["edit"]
        token_utils.token_counter.count_openai_tokens(
            user_message, model_name
        )

        operation_type = "генерация" if file_path is None else "редактирование"

        await max_api.send_message(
            user_id,
            f"🎨 {operation_type.capitalize()} изображения начата...\n"
            f"Запрос: {user_message}"
        )

        image_paths = []

        if file_path:
            image_paths.append(file_path)

        edit_queue = get_user_edit_queue(user_id)
        if edit_queue:
            valid_paths = [
                path
                for path in edit_queue
                if path is not None and os.path.exists(path)
            ]
            image_paths.extend(valid_paths)

        edit_data = get_user_edit_data(user_id)
        last_edited = edit_data.get("last_image")
        
        if (
            user_message
            and not file_path
            and last_edited
            and last_edited not in image_paths
        ):
            if os.path.exists(last_edited):
                image_paths.append(last_edited)

        if image_paths:
            image_paths = image_paths[:MAX_REF_IMAGES]
        else:
            image_paths = []

        try:
            image_bytes, text_response = await image_edit_utils.edit_image(
                image_paths, user_message
            )
        except Exception as edit_error:
            if "timeout" in str(edit_error).lower():
                await max_api.send_message(
                    user_id,
                    "⏰ Время ожидания редактирования изображения истекло. "
                    "Пожалуйста, попробуйте снова с более простым запросом."
                )
                return
            else:
                raise edit_error

        if text_response is not None:
            await max_api.send_message(user_id, text_response)
        else:
            edited_file_path = f"edited_{user_id}.png"
            with open(edited_file_path, "wb") as f:
                f.write(image_bytes)

            try:
                prefix = (
                    "Сгенерировано по запросу: "
                    if file_path is None
                    else "Отредактировано по запросу: "
                )
                caption = prefix + user_message[:500] if user_message else prefix
                
                result = await max_api.send_generated_image(
                    user_id=user_id,
                    image_bytes=image_bytes,
                    caption=caption,
                )
                
                if result != 200:
                    logger.error(f"Ошибка отправки изображения: status={result}")
                    await max_api.send_message(
                        user_id,
                        "⚠️ Изображение создано, но не удалось отправить."
                    )
            except Exception as e:
                await max_api.send_message(
                    user_id,
                    f"⚠️ Ошибка при отправке изображения: {str(e)}"
                )
                logger.error(f"Ошибка отправки изображения: {e}")

        if last_edited and os.path.exists(last_edited):
            os.remove(last_edited)

        new_edit_data = {"last_image": edited_file_path}
        set_user_edit_data(user_id, new_edit_data)

        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        clear_user_pending_delete(user_id)
        set_user_edit_queue(user_id, [])

    except Exception as e:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)

        if (
            "edited_file_path" in locals()
            and edited_file_path
            and os.path.exists(edited_file_path)
        ):
            os.remove(edited_file_path)

        clear_user_pending_delete(user_id)
        set_user_edit_queue(user_id, [])

        await max_api.send_message(user_id, f"⚠️ Ошибка: {str(e)}")


async def handle_chat_mode(
    user_id: int,
    user_message: str,
    sender: dict,
    request=None,
):
    """Handle the chat mode functionality separately (MAX API)."""
    try:
        giga_client = getattr(request, "app", None)
        giga_client = getattr(giga_client, "state", None)
        giga_client = getattr(giga_client, "giga_client", None)
        if giga_client is None:
            await max_api.send_message(
                user_id, "GigaChat клиент не инициализирован."
                )
            return
        model_name = models_config.MODELS.get("chat")
        user_context = []

        wants_word_format = docx_utils.check_user_wants_word_format(user_message)
        wants_pdf_format = pdf_utils.check_user_wants_pdf_format(user_message)
        wants_excel_format = xlsx_utils.check_user_wants_xlsx_format(user_message)
        wants_rtf_format = rtf_utils.check_user_wants_rtf_format(user_message)
        
        if wants_word_format:
            user_message = user_message + " " + docx_utils.JSON_SCHEMA
        elif wants_pdf_format:
            user_message = user_message + " " + pdf_utils.JSON_SCHEMA_PDF
        elif wants_excel_format:
            user_message = user_message + " " + xlsx_utils.JSON_SCHEMA_EXCEL
        elif wants_rtf_format:
            user_message = user_message + " " + RTF_PROMPT

        history = get_user_context(user_id, "chat")
        if history and len(history) > 1:
            temp_history = history + [
                {"role": "user", "content": user_message}
            ]

            user_context = token_utils.truncate_messages_for_token_limit(
                messages=temp_history,
                model=model_name,
                reserve_tokens=1500,
            )
        else:
            user_context = []

        if len(user_context) > MAX_CONTEXT_MESSAGES:
            user_context = user_context[-MAX_CONTEXT_MESSAGES:]

        prompt_tokens = token_utils.token_counter.count_openai_messages_tokens(
            user_context, model_name
        )

        reply = await giga_client.chat(
            messages=user_context,
            model="GigaChat-2-Pro",
        )

        response_tokens = token_utils.token_counter.count_openai_tokens(
            reply, model_name
        )

        new_context = history + [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": reply}
        ]
        set_user_context(user_id, "chat", new_context)

        if wants_word_format:
            await send_docx_response(user_id, reply)
        elif wants_pdf_format:
            await send_pdf_response(user_id, reply)
        elif wants_excel_format:
            await send_xlsx_response(user_id, reply)
        elif wants_rtf_format:
            await send_rtf_response(user_id, reply)
        else:
            await max_api.send_message(user_id, reply)

    except Exception as e:
        await max_api.send_message(user_id, f"⚠️ Ошибка: {str(e)}")


async def handle_message(
    user_id: int,
    user_message: str,
    sender: dict,
    attachments: list = None,
    request=None,
):
    """Main handler for messages (MAX API)."""
    from global_state import user_previous_modes

    current_mode = get_user_mode(user_id)
    if not current_mode:
        current_mode = "chat"
        set_user_mode(user_id, current_mode)

    previous_mode = user_previous_modes.get(user_id)
    
    pending_delete = None
    try:
        from global_state import get_user_pending_delete
        pending_delete = get_user_pending_delete(user_id)
    except Exception:
        pass
    
    if pending_delete and previous_mode and previous_mode != current_mode:
        if os.path.exists(pending_delete):
            os.remove(pending_delete)
        clear_user_pending_delete(user_id)

    edit_data = get_user_edit_data(user_id)
    last_edited = edit_data.get("last_image")
    
    if (
        last_edited
        and previous_mode == "edit"
        and current_mode != "edit"
    ):
        if os.path.exists(last_edited):
            os.remove(last_edited)
        set_user_edit_data(user_id, {})

    user_previous_modes[user_id] = current_mode

    initialize_user_context(user_id, current_mode)

    if current_mode == "ai_file":
        await handle_file_analysis_mode(
            user_id,
            user_message,
            sender,
            request,
        )
        return

    if current_mode == "edit":
        file_path = None
        
        if attachments:
            for att in attachments:
                if att.get("type") == "image":
                    att_url = att.get("payload", {}).get("url")
                    if att_url:
                        from utils import save_user_file
                        file_path = await save_user_file(
                            att_url, user_id, "png", "image"
                        )
                        break
        
        await handle_image_edit_mode(
            user_id,
            user_message,
            sender,
            file_path,
        )
        return

    if current_mode == "chat":
        await handle_chat_mode(
            user_id,
            user_message,
            sender,
        )
        return