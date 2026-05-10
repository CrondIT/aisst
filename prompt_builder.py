"""Модуль для формирования промптов с историей и JSON-схемами."""
from file_output_utils import docx_utils
from file_output_utils import xlsx_utils
from file_output_utils import pdf_utils
from file_output_utils import rtf_utils
import token_utils
import max_api
from global_state import (
    get_user_context,
    get_user_mode,
    MAX_CONTEXT_MESSAGES,
    SYSTEM_PROMPTS,
    RTF_PROMPT,
    MODELS,
)


async def full_prompt(
    user_id: int,
    user_message: str,
    extracted_text: str,
):
    """
    Формирует пользовательский промпт с историей
    и JSON схемой если нужно вывести ответ в виде файла
    """

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

    user_mode = get_user_mode(user_id)
    model_name = MODELS.get(user_mode)
    max_tokens = token_utils.get_token_limit(model_name)
    reserved_tokens_for_context = 2500

    # Если есть текст из файла - формируем расширенный промпт
    if user_message and extracted_text:

        user_mode = get_user_mode(user_id)
        model_name = MODELS.get(user_mode)
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

        history = get_user_context(user_id, user_mode)
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

        system_message = SYSTEM_PROMPTS.get("ai_file") or ""
        full_context = [
            {"role": "system", "content": system_message},
            {"role": "user", "content": augmented_question}
        ]
        return full_context
    else:
        # Без файла - просто возвращаем вопрос пользователя
        return [
            {"role": "user", "content": user_message}
        ]
