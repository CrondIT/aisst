"""
Utility functions for creating and handling RTF files.
"""

import re
import json
import tempfile
import os
import datetime


# Словарь именованных CSS-цветов -> RGB
COLOR_MAP_RTF: dict[str, tuple[int, int, int]] = {
    "black": (0, 0, 0),
    "red": (255, 0, 0),
    "blue": (0, 0, 255),
    "green": (0, 128, 0),
    "navy": (0, 0, 128),
    "purple": (128, 0, 128),
    "orange": (255, 165, 0),
    "gray": (128, 128, 128),
    "darkgray": (169, 169, 169),
    "brown": (165, 42, 42),
    "gold": (255, 215, 0),
    "pink": (255, 192, 203),
    "coral": (255, 127, 80),
    "maroon": (128, 0, 0),
    "teal": (0, 128, 128),
}

# Порядок цветов в colortbl (cf0=auto, cf1=black, cf2=red, ...)
_COLORTBL_ORDER = [
    "black", "red", "blue", "green", "navy", "purple",
    "orange", "gray", "darkgray", "brown", "gold", "pink",
    "coral", "maroon", "teal",
]


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


def _build_colortbl(used_colors: set[str]) -> str:
    """Строит colortbl из использованных цветов."""
    entries = [""]
    for name in _COLORTBL_ORDER:
        if name in used_colors:
            r, g, b = COLOR_MAP_RTF[name]
            entries.append(f"\\red{r}\\green{g}\\blue{b}")
    return "{\\colortbl " + ";".join(entries) + ";}\n"


def _get_color_index(color_name: str, used_colors: set[str]) -> int:
    """Возвращает индекс цвета в colortbl (0 = auto)."""
    idx = 0
    for name in _COLORTBL_ORDER:
        if name in used_colors:
            idx += 1
            if name == color_name.lower():
                return idx
    return 0


def _rtf_encode_text(text: str) -> str:
    """Кодирует текст для RTF: unicode в \\uNNNN?, экранирует \\ {}."""
    result = []
    for ch in text:
        code = ord(ch)
        if code < 128 and ch not in "\\{}":
            result.append(ch)
        elif ch == "\\":
            result.append("\\\\")
        elif ch == "{":
            result.append("\\{")
        elif ch == "}":
            result.append("\\}")
        else:
            result.append(f"\\u{code}?")
    return "".join(result)


def create_rtf_file(reply: str) -> str:
    """
    Создает RTF файл из JSON-ответа LLM.

    Ожидает JSON вида:
    {
        "meta": {"title": "...", "hide_title": false},
        "content": [
            {"type": "paragraph", "text": "...", "bold": false,
             "italic": false, "color": "red", "font_size": 24,
             "indent_first_line": true, "alignment": "left"}
        ]
    }

    Args:
        reply: JSON-строка от LLM

    Returns:
        str: Путь к созданному временному RTF-файлу
    """
    # JSON repair: убираем markdown-обёртку
    cleaned = reply.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    # Исправляем JSON: добавляем кавычки к названиям полей без них
    cleaned = re.sub(
        r'(\{|\,)\s*([a-zA-Z_]\w*)\s*:',
        r'\1"\2":',
        cleaned,
    )
    # Удаляем запятые перед ] и }
    cleaned = re.sub(r',\s*\]', ']', cleaned)
    cleaned = re.sub(r',\s*\}', '}', cleaned)

    data = json.loads(cleaned, strict=False)

    meta = data.get("meta", {})
    title = meta.get("title", "Документ")
    hide_title = meta.get("hide_title", False)
    content_blocks = data.get("content", [])

    # Собираем используемые цвета
    used_colors: set[str] = set()
    for block in content_blocks:
        color = block.get("color")
        if color:
            used_colors.add(color.lower())

    rtf_parts: list[str] = []

    # ----- Начало документа -----
    rtf_parts.append(r"{\rtf1\ansi\ansicpg1251")
    rtf_parts.append("\n")

    # Таблица шрифтов
    rtf_parts.append(r"{\fonttbl {\f0 Arial;}{\f1 Times New Roman;}}")
    rtf_parts.append("\n")

    # Цветовая таблица
    rtf_parts.append(_build_colortbl(used_colors))

    # Поля страницы (1 дюйм = 1440 twips)
    rtf_parts.append(r"\margl1440\margr1440\margt1440\margb1440")
    rtf_parts.append("\n")

    # ----- Заголовок документа -----
    if title and not hide_title:
        rtf_parts.append(r"\pard\plain\qc\cf1\b\fs36 ")
        rtf_parts.append(_rtf_encode_text(title))
        rtf_parts.append(r"\par\par")
        rtf_parts.append("\n")

    # ----- Блоки контента -----
    for block in content_blocks:
        btype = block.get("type", "paragraph")
        text = block.get("text", "")
        bold = block.get("bold", False)
        italic = block.get("italic", False)
        color = block.get("color", "black")
        font_size = block.get("font_size", 24)
        indent_first_line = block.get("indent_first_line", False)
        alignment = block.get("alignment", "left")

        color_idx = _get_color_index(color, used_colors)

        # Сбрасываем форматирование и начинаем абзац
        rtf_parts.append(r"\pard\plain")

        # Выравнивание
        if alignment == "center":
            rtf_parts.append(r"\qc")
        elif alignment == "right":
            rtf_parts.append(r"\qr")
        elif alignment == "justify":
            rtf_parts.append(r"\qj")

        # Цвет
        rtf_parts.append(f"\\cf{color_idx}")

        # Размер шрифта (в половинных пунктах)
        rtf_parts.append(f"\\fs{font_size}")

        # Красная строка (отступ первой строки, 720 twips ~12.7мм)
        if indent_first_line and btype != "heading":
            rtf_parts.append(r"\fi720")

        # Жирный
        if bold or btype == "heading":
            rtf_parts.append(r"\b")

        # Курсив
        if italic:
            rtf_parts.append(r"\i")

        rtf_parts.append(" ")
        rtf_parts.append(_rtf_encode_text(text))
        rtf_parts.append(r"\par")
        rtf_parts.append("\n")

    # ----- Конец документа -----
    rtf_parts.append("}")

    rtf_content = "".join(rtf_parts)

    filename = os.path.join(
        tempfile.gettempdir(),
        f"reply_{int(datetime.datetime.utcnow().timestamp())}.rtf"
    )

    with open(filename, "w", encoding="ascii", errors="ignore") as f:
        f.write(rtf_content)

    return filename


async def send_rtf_response(user_id: int, reply: str):
    """
    Отправляет RTF-файл с ответом пользователю через MAX API.

    Args:
        user_id: ID пользователя в MAX
        reply: JSON-строка для конвертации в RTF
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
            file_type="file"
        )

        if result != 200:
            logger.error(f"Ошибка отправки RTF: status={result}")

    except json.JSONDecodeError as e:
        logger.error(f"Ошибка разбора JSON при создании RTF: {e}")
        await max_api.send_message(user_id, "Не удалось создать RTF документ.")
    except (ValueError, KeyError) as e:
        logger.error(f"Ошибка данных при создании RTF: {e}")
        await max_api.send_message(user_id, "Некорректный ответ от модели.")
    except Exception as e:
        logger.error(f"Ошибка при создании или отправке RTF файла: {e}")
        await max_api.send_message(user_id, f"Ошибка при создании RTF файла: {str(e)}")
    finally:
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass
