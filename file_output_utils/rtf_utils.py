"""
Utility functions for creating and handling RTF files.
"""

import tempfile
import os
import datetime


def check_user_wants_rtf_format(user_message):
    """
    Проверяет, хочет ли пользователь получить ответ в RTF формате.

    Args:
        user_message (str): Сообщение от пользователя для анализа

    Returns:
        bool: True если пользователь хочет формат RTF, False в противном случае
    """
    message = user_message.lower()

    negative_patterns = [
        "not interested in rtf",
        "not interested in rtf format",
        "don't want rtf",
        "no need for rtf",
        "not rtf",
        "without rtf",
    ]

    for neg_pattern in negative_patterns:
        if neg_pattern in message:
            return False

    positive_indicators = [
        "rtf",
        "формат rtf",
        "rich text format",
        "в rtf",
        "в формате rtf",
        "в формате rich text",
        "rtf документ",
        "документ rtf",
        "в формате документа rtf",
        "в rtf формате",
        "в rich text format",
        "в формате rich text",
    ]

    for indicator in positive_indicators:
        if indicator in message:
            return True

    return False


def create_rtf_file(text: str) -> str:
    """
    Создает RTF файл из текста.
    
    Args:
        text: Текст для конвертации в RTF
    
    Returns:
        str: Путь к созданному временному файлу
    """
    rtf_text = []

    for ch in text:
        if ord(ch) < 128:
            rtf_text.append(ch)
        else:
            rtf_text.append(rf"\u{ord(ch)}?")

    rtf_body = "".join(rtf_text)
    rtf = (
        r"{\rtf1\ansi\ansicpg1251 "
        + rtf_body.replace("\n", r"\par ")
        + "}"
    )

    filename = os.path.join(
        tempfile.gettempdir(),
        f"reply_{int(datetime.datetime.utcnow().timestamp())}.rtf"
    )

    with open(filename, "w", encoding="ascii", errors="ignore") as f:
        f.write(rtf)

    return filename


async def send_rtf_response(user_id: int, reply: str):
    """
    Отправляет RTF-файл с ответом пользователю через MAX API.
    
    Args:
        user_id: ID пользователя в MAX
        reply: Текст для отправки в RTF формате
    """
    import max_api
    from utils import logger
    
    file_path = None
    try:
        file_path = create_rtf_file(reply)
        
        with open(file_path, "rb") as f:
            file_data = f.read()
        
        result = await max_api.send_document(
            user_id=user_id,
            file_data=file_data,
            filename="document.rtf",
            caption="Файл с ответом модели (RTF)",
            file_type="file"
        )
        
        if result != 200:
            logger.error(f"Ошибка отправки RTF: status={result}")
            
    except Exception as e:
        logger.error(f"Ошибка при создании или отправке RTF файла: {e}")
        await max_api.send_message(user_id, f"Ошибка при создании RTF файла: {str(e)}")
    finally:
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass
