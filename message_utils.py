"""Utility function for splitting messages."""


async def split_long_message(
        text: str, MESSAGE_LIMIT: int = 4096
):
    """
    Разбивает  длинное сообщение на части,
    если оно превышает лимит (4096 символов)
    """
    text_parts = []
    if len(text) <= MESSAGE_LIMIT:
        # Message fits in a single message
        text_parts.append(text)
        return text_parts

    # Split the message by paragraphs first to avoid breaking sentences
    paragraphs = text.split("\n")

    current_message = ""
    for paragraph in paragraphs:
        # Check if adding this paragraph would exceed the limit
        if len(current_message) + len(paragraph) + 1 <= MESSAGE_LIMIT:
            if current_message:
                current_message += "\n" + paragraph
            else:
                current_message = paragraph
        else:
            # Send the current message if it's not empty
            if current_message:
                text_parts.append(current_message)

            # If the single paragraph is too long, split it by sentences
            if len(paragraph) > MESSAGE_LIMIT:
                sentences = paragraph.split(". ")
                temp_message = ""
                for sentence in sentences:
                    if (
                        len(temp_message) + len(sentence) + 2
                        <= MESSAGE_LIMIT
                    ):
                        if temp_message:
                            temp_message += ". " + sentence
                        else:
                            temp_message = sentence
                    else:
                        if temp_message:
                            text_parts.append(temp_message + ".")
                        temp_message = sentence

                # Add the last part if there's anything left
                if temp_message:
                    current_message = temp_message
                else:
                    current_message = ""
            else:
                current_message = paragraph

    # Send the remaining message if there's anything left
    if current_message:
        text_parts.append(current_message)
    return text_parts


def truncate_caption(
        text: str, max_length: int = 1024, prefix: str = ""
) -> str:
    """
    Обрезает текст для caption в Telegram с умным сокращением.

    Telegram ограничивает caption 1024 символами.
    Функция сохраняет важную часть запроса (начало и ключевые детали),
    добавляя многоточие при обрезке.

    Args:
        text: Исходный текст запроса
        max_length: Максимальная длина caption (по умолчанию 1024)
        prefix: Префикс, который нужно добавить перед текстом

    Returns:
        Обрезанный текст с префиксом, готовый для использования в caption
    """
    # Вычисляем доступную длину для текста
    available_length = max_length - len(prefix)

    if len(text) <= available_length:
        return f"{prefix}{text}"

    # Текст слишком длинный - нужно обрезать с умом
    # Стратегия: сохраняем начало и конец, убирая середину
    if available_length > 40:
        # Сохраняем начало (60%) и конец (40%) с многоточием посередине
        start_len = int(available_length * 0.6) - 2  # -2 для "..."
        end_len = available_length - start_len - 3    # -3 для "..."

        start_part = text[:start_len]
        end_part = text[-end_len:] if end_len > 0 else ""

        truncated = (
            f"{start_part}...{end_part}" if end_part else f"{start_part}..."
        )
    else:
        # Очень мало места - просто обрезаем
        truncated = text[:available_length - 3] + "..."

    return f"{prefix}{truncated}"
