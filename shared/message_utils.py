"""Общие утилиты для обработки сообщений."""
from global_state import get_user_file_data
from file_output_utils import docx_utils, pdf_utils, xlsx_utils, rtf_utils


def get_file_extracted_text(user_id: int) -> str:
    """
    Возвращает текст, извлечённый из файла, загруженного пользователем.
    Если файл не загружен — возвращает пустую строку.
    """
    file_data = get_user_file_data(user_id)
    if file_data and "extracted_text" in file_data:
        return file_data["extracted_text"]
    return ""


async def check_and_send_formatted(
    user_text: str, user_id: int, answer: str
) -> str | None:
    """
    Проверяет, запросил ли пользователь конкретный формат файла,
    создаёт и отправляет файл нужного формата.
    Возвращает строку-уведомление или None, если формат не запрошен.
    """
    if docx_utils.check_user_wants_word_format(user_text):
        await docx_utils.send_docx_response(user_id, answer)
        return "Вот Ваш файл в формате Word"
    if pdf_utils.check_user_wants_pdf_format(user_text):
        await pdf_utils.send_pdf_response(user_id, answer)
        return "Вот Ваш файл в формате PDF"
    if xlsx_utils.check_user_wants_xlsx_format(user_text):
        await xlsx_utils.send_xlsx_response(user_id, answer)
        return "Вот Ваш файл в формате Excel"
    if rtf_utils.check_user_wants_rtf_format(user_text):
        await rtf_utils.send_rtf_response(user_id, answer)
        return "Вот Ваш файл в формате RTF"
    return None
