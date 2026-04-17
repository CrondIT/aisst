"""Utility functions for creating and handling PDF files."""

import re
from reportlab.lib.pagesizes import A4
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

# from reportlab.platypus.doctemplate import BaseDocTemplate, PageTemplate
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import io
import json
from global_state import DOCUMENT_JSON_SCHEMA
from telegram import InputFile
from telegram.helpers import escape_markdown
from message_utils import send_long_message
from reportlab.lib.colors import Color as RLColor
import reportlab.lib.colors as colors_module

# Создаем объект изображения для ReportLab
from reportlab.platypus import Image as RLImage

# Константы для размера страницы
DEFAULT_PAGE_SIZE = A4

# Константы для полей страницы
PAGE_LEFT_MARGIN = 20 * mm
PAGE_RIGHT_MARGIN = 10 * mm
PAGE_TOP_MARGIN = 20 * mm
PAGE_BOTTOM_MARGIN = 20 * mm

# Константы для отступов колонтитулов (в миллиметрах)
HEADER_TOP_MARGIN = 10 * mm  # отступ от верхнего края страницы (в мм)
FOOTER_BOTTOM_MARGIN = 10 * mm  # отступ от нижнего края страницы (в мм)


def draw_header_footer(canvas, doc, header_info=None, footer_info=None):
    """Функция для рисования колонтитулов на странице"""
    canvas.saveState()

    # Используем значения по умолчанию, если пользователь не предоставил свои
    if header_info is None:
        header_info = DEFAULT_HEADER_INFO
    if footer_info is None:
        footer_info = DEFAULT_FOOTER_INFO

    # Рисуем верхний колонтитул
    if header_info:
        header_text = header_info.get("text", "")
        font_name = header_info.get("font_name", CYRILLIC_FONT)
        font_size = header_info.get("font_size", 10)
        color = header_info.get("color", "black")
        alignment = header_info.get("alignment", "left")

        # Нормализуем имя шрифта
        font_name = normalize_font_name(font_name)

        # Создаем стиль для колонтитула
        header_style = ParagraphStyle(
            "HeaderStyle",
            parent=getSampleStyleSheet()["Normal"],
            fontName=font_name,
            fontSize=font_size,
        )

        # Определяем выравнивание
        alignment_val = TA_LEFT
        if alignment == "center":
            alignment_val = TA_CENTER
        elif alignment == "right":
            alignment_val = TA_RIGHT
        elif alignment == "justify":
            alignment_val = (
                TA_LEFT  # ReportLab не поддерживает justify напрямую
            )

        header_style.alignment = alignment_val

        # Применяем цвет, если указан
        if color:
            header_style.textColor = parse_color(color)

        # Создаем параграф с текстом колонтитула
        header_para = Paragraph(header_text, header_style)

        # Определяем позицию - чуть ниже верхнего края страницы
        y_position = (
            doc.height
            + PAGE_TOP_MARGIN
            + PAGE_BOTTOM_MARGIN
            - HEADER_TOP_MARGIN
        )  # отступ от верхнего края
        x_position = doc.leftMargin

        # Рисуем колонтитул
        w, h = header_para.wrap(doc.width, doc.height)  # Получаем размеры
        header_para.drawOn(canvas, x_position, y_position)

    # Рисуем нижний колонтитул
    if footer_info:
        footer_text = footer_info.get("text", "")

        # Заменяем теги номера страницы на реальный номер страницы
        # Поддерживаемые теги: {page}, {pageNumber}, {current_page}
        import re

        # Получаем номер текущей страницы из канваса
        page_number = canvas.getPageNumber()
        footer_text = re.sub(
            r"\{page\}|\{pageNumber\}|\{current_page\}",
            str(page_number),
            footer_text,
        )

        font_name = footer_info.get("font_name", CYRILLIC_FONT)
        font_size = footer_info.get("font_size", 10)
        color = footer_info.get("color", "black")
        alignment = footer_info.get("alignment", "left")

        # Нормализуем имя шрифта
        font_name = normalize_font_name(font_name)

        # Создаем стиль для колонтитула
        footer_style = ParagraphStyle(
            "FooterStyle",
            parent=getSampleStyleSheet()["Normal"],
            fontName=font_name,
            fontSize=font_size,
        )

        # Определяем выравнивание
        alignment_val = TA_LEFT
        if alignment == "center":
            alignment_val = TA_CENTER
        elif alignment == "right":
            alignment_val = TA_RIGHT
        elif alignment == "justify":
            alignment_val = (
                TA_LEFT  # ReportLab не поддерживает justify напрямую
            )

        footer_style.alignment = alignment_val

        # Применяем цвет, если указан
        if color:
            footer_style.textColor = parse_color(color)

        # Создаем параграф с текстом колонтитула
        footer_para = Paragraph(footer_text, footer_style)

        # Определяем позицию - чуть выше нижнего края страницы
        y_position = FOOTER_BOTTOM_MARGIN  # отступ от нижнего края
        x_position = doc.leftMargin

        # Рисуем колонтитул
        w, h = footer_para.wrap(doc.width, doc.height)  # Получаем размеры
        footer_para.drawOn(canvas, x_position, y_position)

    canvas.restoreState()


class CustomDocTemplate(SimpleDocTemplate):
    """Кастомный документ с поддержкой колонтитулов"""

    def __init__(self, filename, header_info=None, footer_info=None, **kwargs):
        super().__init__(filename, **kwargs)

        # Используем значения по умолчанию, если пользователь не предоставил
        if header_info is None:
            header_info = DEFAULT_HEADER_INFO
        if footer_info is None:
            footer_info = DEFAULT_FOOTER_INFO

        self.header_info = header_info
        self.footer_info = footer_info

        # Устанавливаем функции для колонтитулов
        self.onFirstPage = lambda canvas, doc: draw_header_footer(
            canvas, doc, header_info, footer_info
        )
        self.onLaterPages = lambda canvas, doc: draw_header_footer(
            canvas, doc, header_info, footer_info
        )


def parse_color(color_value):
    """
    Универсальная функция для обработки различных форматов цветов

    Args:
        color_value: Значение цвета в одном из поддерживаемых форматов:
                     - строка с именем цвета ('black', 'red', и т.д.)
                     - строка в формате HEX ('#FF0000')
                     - кортеж/список RGB ((255, 0, 0))
                     - объект Color из reportlab

    Returns:
        Объект Color из reportlab
    """
    if isinstance(color_value, RLColor):
        # Если уже объект Color, используем его напрямую
        return color_value
    elif isinstance(color_value, str):
        if color_value.startswith("#"):
            # HEX-формат, например "#FF5733"
            hex_color = color_value.lstrip("#")
            # Проверяем длину HEX-кода (может быть 3 или 6 символов)
            if len(hex_color) == 3:
                # Формат с сокращенным представлением (RGB)
                rgb = tuple(
                    int(hex_color[i], 16) * 17 for i in range(3)
                )  # Умножаем на 17 для расширения 0-F до 0-255
            elif len(hex_color) == 6:
                # Полный формат (RRGGBB)
                rgb = tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
            else:
                raise ValueError(
                    f"Неподдерживаемый формат HEX цвета: {color_value}"
                )
            return RLColor(rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0)
        else:
            # Проверяем, является ли это именованным цветом из reportlab
            try:
                color_obj = getattr(colors_module, color_value.lower())
                if not isinstance(color_obj, RLColor):
                    raise AttributeError
                return color_obj
            except AttributeError:
                raise ValueError(
                    f"Неподдерживаемый формат цвета: {color_value}"
                )
    elif isinstance(color_value, (tuple, list)) and len(color_value) == 3:
        # RGB в формате (R, G, B), где 0 <= R,G,B <= 255
        r, g, b = color_value
        return RLColor(r / 255.0, g / 255.0, b / 255.0)
    else:
        raise ValueError(f"Неподдерживаемый формат цвета: {color_value}")


# JSON схема для PDF, аналогичная DOCX
# JSON_SCHEMA_PDF теперь определена в global_state.py
# и импортируется как DOCUMENT_JSON_SCHEMA
# Для обратной совместимости, создаем алиас
JSON_SCHEMA_PDF = DOCUMENT_JSON_SCHEMA


# Регистрируем шрифт, поддерживающий кириллицу
def register_cyrillic_font():
    """Регистрирует шрифт, поддерживающий кириллицу"""
    try:
        # Попробуем использовать стандартный шрифт Windows
        import platform

        if platform.system() == "Windows":
            font_path = "C:/Windows/Fonts/calibri.ttf"
            bold_font_path = "C:/Windows/Fonts/calibrib.ttf"  # Жирный Calibri
        else:
            # Для других систем можно использовать другие шрифты
            font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            bold_font_path = (
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            )

        # Регистрируем обычный шрифт
        pdfmetrics.registerFont(TTFont("CyrillicFont", font_path))

        # Попробуем зарегистрировать жирное начертание
        try:
            pdfmetrics.registerFont(
                TTFont("CyrillicFont-Bold", bold_font_path)
            )
        except (IOError, OSError):
            # Если не удалось загрузить жирный шрифт,
            #  регистрируем обычный как жирный
            pdfmetrics.registerFont(TTFont("CyrillicFont-Bold", font_path))

        return "CyrillicFont"
    except (IOError, OSError, RuntimeError):
        # Если не удалось загрузить шрифт, используем стандартный
        return "Helvetica"


def clean_html_tags(text):
    """
    Очищает текст от HTML-тегов и других форматирований,
    не поддерживаемых в ReportLab.

    Args:
        text (str): Текст для очистки

    Returns:
        str: Очищенный текст
    """
    if not isinstance(text, str):
        return str(text)

    # Удаляем span теги и их атрибуты
    text = re.sub(r"<span[^>]*>", "", text)
    text = re.sub(r"</span>", "", text)

    # Удаляем другие теги
    text = re.sub(r"<[^>]+>", "", text)

    # Удаляем двойные звездочки (жирный текст в markdown)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)

    # Удаляем одинарные звездочки (курсив в markdown)
    text = re.sub(r"(?<!\*)\*([^\*]+?)\*(?!\*)", r"\1", text)

    # Удаляем подчеркивания (курсив в markdown)
    text = re.sub(r"(?<!_)_([^_]+?)_(?!_)", r"\1", text)

    return text.strip()


CYRILLIC_FONT = register_cyrillic_font()

# Убедимся, что мы используем правильные имена шрифтов
CYRILLIC_FONT_BOLD = "CyrillicFont-Bold"

# Константы для колонтитулов по умолчанию
DEFAULT_HEADER_INFO = {
    "text": "",
    "font_name": CYRILLIC_FONT,
    "font_size": 10,
    "color": "black",
    "alignment": "left",
}

DEFAULT_FOOTER_INFO = {
    "text": "Страница {page}",
    "font_name": CYRILLIC_FONT,
    "font_size": 10,
    "color": "black",
    "alignment": "center",
}


def normalize_font_name(font_name):
    """
    Нормализует имя шрифта, заменяя недопустимые шрифты на зарегистрированные

    Args:
        font_name (str): Имя шрифта из JSON

    Returns:
        str: Нормализованное имя шрифта
    """
    if not font_name:
        return CYRILLIC_FONT

    # Приводим к нижнему регистру и удаляем пробелы для сравнения
    normalized = font_name.lower().replace(" ", "").replace("-", "")

    # Проверяем, является ли шрифт Times New Roman или его вариантом
    if "times" in normalized or "newroman" in normalized:
        return CYRILLIC_FONT

    # Если это один из зарегистрированных шрифтов, возвращаем как есть
    if font_name in ["CyrillicFont", "CyrillicFont-Bold", "Helvetica"]:
        return font_name

    # В остальных случаях используем кириллический шрифт по умолчанию
    return CYRILLIC_FONT


def create_pdf_from_json(data: dict) -> io.BytesIO:
    """
    Создает PDF документ из JSON данных.

    Args:
        data: dict с ключами "meta" и "blocks" в формате, аналогичному DOCX

    Returns:
        io.BytesIO: PDF файл в виде байтового потока
    """
    # Создаем байтовый поток для PDF
    pdf_buffer = io.BytesIO()

    # Определяем размер страницы из метаданных
    meta = data.get("meta", {})
    page_size_str = meta.get("page_size", "A4").upper()

    # Определяем фактический размер страницы
    if page_size_str == "A4":
        page_size = A4
    elif page_size_str == "LETTER":
        from reportlab.lib.pagesizes import letter

        page_size = letter
    elif page_size_str == "LEGAL":
        from reportlab.lib.pagesizes import legal

        page_size = legal
    elif page_size_str == "A3":
        from reportlab.lib.pagesizes import A3

        page_size = A3
    elif page_size_str == "A5":
        from reportlab.lib.pagesizes import A5

        page_size = A5
    else:
        # Если указан неизвестный формат, используем A4 по умолчанию
        page_size = DEFAULT_PAGE_SIZE

    # Получаем информацию о колонтитулах
    # Обработка заголовков и футеров в новом формате
    header_data = meta.get("header", None)
    footer_data = meta.get("footer", None)

    # Преобразование нового формата в старый для совместимости
    header_info = None
    footer_info = None

    if header_data:
        # Преобразуем новый формат в старый
        header_info = {
            "text": header_data.get("content", ""),
            "font_name": header_data.get("font_name", CYRILLIC_FONT),
            "font_size": header_data.get("font_size", 10),
            "color": header_data.get("color", "black"),
            "alignment": header_data.get("alignment", "left"),
        }

    if footer_data:
        # Преобразуем новый формат в старый
        footer_info = {
            "text": footer_data.get("content", ""),
            "font_name": footer_data.get("font_name", CYRILLIC_FONT),
            "font_size": footer_data.get("font_size", 10),
            "color": footer_data.get("color", "black"),
            "alignment": footer_data.get("alignment", "left"),
        }

    # Создаем документ с настраиваемыми полями и поддержкой колонтитулов
    doc = CustomDocTemplate(
        pdf_buffer,
        pagesize=page_size,
        leftMargin=PAGE_LEFT_MARGIN,
        rightMargin=PAGE_RIGHT_MARGIN,
        topMargin=PAGE_TOP_MARGIN,
        bottomMargin=PAGE_BOTTOM_MARGIN,
        header_info=header_info,
        footer_info=footer_info,
    )

    # Получаем стили
    styles = getSampleStyleSheet()

    # Обновляем стили, чтобы использовать кириллический шрифт
    styles["Normal"].fontName = CYRILLIC_FONT
    styles["Heading1"].fontName = CYRILLIC_FONT
    styles["Heading2"].fontName = CYRILLIC_FONT
    styles["Heading3"].fontName = CYRILLIC_FONT
    styles["Italic"].fontName = CYRILLIC_FONT

    # Список элементов для документа
    elements = []

    # Обрабатываем блоки
    for block in data.get("blocks", []):
        block_type = block.get("type")

        if block_type == "heading":
            level = block.get("level", 1)
            text = block.get("text", "")
            # Используем параметры из JSON, если они есть, иначе - стандартные
            font_name = block.get("font_name", CYRILLIC_FONT)
            font_name = normalize_font_name(
                font_name
            )  # Нормализуем имя шрифта
            font_size = block.get(
                "font_size", 16 if level == 1 else 14 if level == 2 else 12
            )
            text_color = block.get(
                "color", None
            )  # ReportLab поддерживает цвета
            bg_color = block.get(
                "bg_color", None
            )  # Добавляем поддержку заливки

            heading_style = ParagraphStyle(
                f"CustomHeading{level}",
                parent=(
                    styles[f"Heading{min(level, 6)}"]
                    if f"Heading{min(level, 6)}" in styles
                    else styles["Heading1"]
                ),
                fontSize=font_size,
                spaceAfter=12,
                fontName=font_name,
            )
            # Если указан цвет, добавляем его
            if text_color:
                # Применяем цвет к стилю заголовка
                heading_style.textColor = parse_color(text_color)

            # Применяем заливку, если она указана
            if bg_color:
                try:
                    heading_style.backColor = parse_color(bg_color)
                except Exception:
                    # Игнорируем ошибки при установке заливки
                    pass

            heading = Paragraph(text, heading_style)
            elements.append(heading)

        elif block_type == "paragraph":
            text = block.get("text", "")
            # Используем параметры из JSON, если они есть, иначе - стандартные
            font_name = block.get("font_name", CYRILLIC_FONT)
            font_name = normalize_font_name(
                font_name
            )  # Нормализуем имя шрифта
            font_size = block.get("font_size", 12)
            left_indent = block.get("left_indent", 0)
            right_indent = block.get("right_indent", 0)
            space_after = block.get("space_after", 12)
            alignment = block.get("alignment", "left")  # left, center, right
            text_color = block.get("color", None)
            bg_color = block.get(
                "bg_color", None
            )  # Добавляем поддержку заливки

            # Определяем выравнивание
            alignment_val = TA_LEFT
            if alignment == "center":
                alignment_val = TA_CENTER
            elif alignment == "right":
                alignment_val = TA_RIGHT
            elif alignment == "justify":
                alignment_val = (
                    TA_LEFT  # ReportLab не поддерживает justify напрямую
                )

            paragraph_style = ParagraphStyle(
                "CustomParagraph",
                parent=styles["Normal"],
                fontSize=font_size,
                spaceAfter=space_after,
                leftIndent=left_indent,
                rightIndent=right_indent,
                fontName=font_name,
                alignment=alignment_val,
            )

            # Применяем цвет текста, если он указан
            if text_color:
                # Применяем цвет к стилю параграфа
                paragraph_style.textColor = parse_color(text_color)

            # Применяем заливку, если она указана
            if bg_color:
                try:
                    paragraph_style.backColor = parse_color(bg_color)
                except Exception:
                    # Игнорируем ошибки при установке заливки
                    pass

            # Применяем форматирование из JSON
            if block.get("bold"):
                text = f"<b>{text}</b>"
            if block.get("italic"):
                text = f"<i>{text}</i>"
            if block.get("underline"):
                text = f"<u>{text}</u>"

            paragraph = Paragraph(text, paragraph_style)
            elements.append(paragraph)

        elif block_type == "list":
            ordered = block.get("ordered", False)
            items = block.get("items", [])
            # Используем параметры из JSON, если они есть, иначе - стандартные
            font_name = block.get("font_name", CYRILLIC_FONT)
            font_name = normalize_font_name(
                font_name
            )  # Нормализуем имя шрифта
            font_size = block.get("font_size", 12)
            left_indent = block.get("left_indent", 0)
            right_indent = block.get("right_indent", 0)
            space_after = block.get("space_after", 12)
            text_color = block.get("color", None)
            bg_color = block.get("bg_color", None)

            # Создаем стиль для списка
            list_style = ParagraphStyle(
                "ListStyle",
                parent=styles["Normal"],
                fontSize=font_size,
                spaceAfter=space_after,
                leftIndent=left_indent,
                rightIndent=right_indent,
                fontName=font_name,
            )

            # Применяем цвет текста, если он указан
            if text_color:
                list_style.textColor = parse_color(text_color)

            # Применяем заливку, если она указана
            if bg_color:
                try:
                    list_style.backColor = parse_color(bg_color)
                except Exception:
                    # Игнорируем ошибки при установке заливки
                    pass

            for item in items:
                bullet = (
                    "•" if not ordered else "○"
                )  # используем символ маркера
                list_item = Paragraph(f"{bullet} {item}", list_style)
                elements.append(list_item)

        elif block_type == "table":
            headers = block.get("headers", [])
            rows = block.get("rows", [])

            # Используем параметры из JSON, если они есть, иначе - стандартные
            table_params = block.get("params", {})
            header_font_name = table_params.get(
                "header_font_name", "CyrillicFont-Bold"
            )
            header_font_name = normalize_font_name(
                header_font_name
            )  # Нормализуем имя шрифта
            header_font_size = table_params.get("header_font_size", 10)
            header_color = table_params.get("header_color", "black")
            header_bg_color = table_params.get("header_bg_color", "lightgrey")
            header_alignment = table_params.get("header_alignment", "center")
            header_valign = table_params.get("header_valign", "middle")

            body_font_name = table_params.get("body_font_name", CYRILLIC_FONT)
            body_font_name = normalize_font_name(
                body_font_name
            )  # Нормализуем имя шрифта
            body_font_size = table_params.get("body_font_size", 9)
            body_color = table_params.get("body_color", "black")
            body_bg_color = table_params.get("body_bg_color", "white")
            body_alignment = table_params.get("body_alignment", "left")
            body_valign = table_params.get("body_valign", "middle")

            grid_width = table_params.get("grid_width", 0.5)
            grid_color = parse_color(table_params.get("grid_color", "black"))
            align = table_params.get("align", "LEFT")
            valign = table_params.get("valign", "TOP")
            padding_top = table_params.get("padding_top", 6)
            padding_bottom = table_params.get("padding_bottom", 6)
            body_padding_top = table_params.get("body_padding_top", 4)
            body_padding_bottom = table_params.get("body_padding_bottom", 4)

            # Получаем дополнительные параметры
            column_widths = block.get("column_widths", [])
            cell_properties = block.get("cell_properties", [])

            if headers and rows:
                # Подготовим данные таблицы с учетом переноса текста
                # Для этого обернем каждую ячейку в Paragraph
                # с ограниченной шириной
                # Используем параметры из JSON для стиля
                normal_style = ParagraphStyle(
                    "TableNormal",
                    parent=getSampleStyleSheet()["Normal"],
                    fontName=body_font_name,
                    fontSize=body_font_size,
                    wordWrap="LTR",
                    alignment=TA_LEFT,
                )

                # Подготовим данные таблицы
                processed_table_data = []

                # Обрабатываем заголовки
                processed_headers = []
                for header in headers:
                    cleaned_header = clean_html_tags(str(header))
                    p = Paragraph(cleaned_header, normal_style)
                    processed_headers.append(p)
                processed_table_data.append(processed_headers)

                # Обрабатываем строки данных
                for row in rows:
                    processed_row = []
                    for cell in row:
                        cleaned_cell = clean_html_tags(str(cell))
                        p = Paragraph(cleaned_cell, normal_style)
                        processed_row.append(p)
                    processed_table_data.append(processed_row)

                # Создаем таблицу с обработанными данными
                table = Table(processed_table_data)

                # Устанавливаем ширину столбцов, если указана
                # Добавляем небольшой отступ от краев страницы
                margin_buffer = 10  # 10 points отступа с каждой стороны
                base_available_width = (
                    page_size[0] - PAGE_LEFT_MARGIN - PAGE_RIGHT_MARGIN
                )
                # Уменьшаем доступную ширину на отступы
                adjusted_available_width = (
                    base_available_width - 2 * margin_buffer
                )

                if column_widths:
                    # Убедимся, что количество ширин
                    # соответствует количеству столбцов
                    if len(column_widths) == len(headers):
                        # Проверяем, что общая ширина не превышает доступную
                        total_width = sum(column_widths)
                        if total_width > adjusted_available_width:
                            # Масштабируем ширину столбцов
                            scale_factor = (
                                adjusted_available_width / total_width
                            )
                            scaled_widths = [
                                w * scale_factor for w in column_widths
                            ]
                            table._argW = scaled_widths
                        else:
                            table._argW = [w for w in column_widths]
                    else:
                        # Если количество ширин не совпадает,
                        # используем стандартный расчет
                        num_cols = len(headers)
                        if num_cols > 0:
                            col_widths = [
                                adjusted_available_width / num_cols
                            ] * num_cols
                            table._argW = col_widths
                else:
                    # Стандартный расчет ширины столбцов
                    num_cols = len(headers)
                    if num_cols > 0:
                        col_widths = [
                            adjusted_available_width / num_cols
                        ] * num_cols
                        table._argW = col_widths

                # Создаем стиль таблицы
                table_style_commands = [
                    ("ALIGN", (0, 0), (-1, -1), align),
                    ("VALIGN", (0, 0), (-1, -1), valign),
                    ("GRID", (0, 0), (-1, -1), grid_width, grid_color),
                    ("FONTNAME", (0, 0), (-1, 0), header_font_name),
                    ("FONTSIZE", (0, 0), (-1, 0), header_font_size),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), padding_bottom),
                    ("TOPPADDING", (0, 0), (-1, 0), padding_top),
                    ("FONTNAME", (0, 1), (-1, -1), body_font_name),
                    ("FONTSIZE", (0, 1), (-1, -1), body_font_size),
                    ("BOTTOMPADDING", (0, 1), (-1, -1), body_padding_bottom),
                    ("TOPPADDING", (0, 1), (-1, -1), body_padding_top),
                    (
                        "BACKGROUND",
                        (0, 0),
                        (-1, 0),
                        parse_color(header_bg_color),
                    ),
                    ("TEXTCOLOR", (0, 0), (-1, 0), parse_color(header_color)),
                    ("ALIGN", (0, 0), (-1, 0), header_alignment.upper()),
                    ("VALIGN", (0, 0), (-1, 0), header_valign.upper()),
                    (
                        "BACKGROUND",
                        (0, 1),
                        (-1, -1),
                        parse_color(body_bg_color),
                    ),
                    ("TEXTCOLOR", (0, 1), (-1, -1), parse_color(body_color)),
                    ("ALIGN", (0, 1), (-1, -1), body_alignment.upper()),
                    ("VALIGN", (0, 1), (-1, -1), body_valign.upper()),
                ]

                # Применяем индивидуальные свойства ячеек
                for cell_prop in cell_properties:
                    try:
                        row_val = cell_prop.get("row", 0)
                        col_idx = cell_prop.get("col", 0)

                        # Обработка специальных значений индекса строки
                        if isinstance(row_val, str):
                            if row_val == "last":
                                # Используем индекс последней строки данных
                                # (учитывая, что 0 - заголовки)
                                row_idx = len(rows)
                            elif row_val == "first":
                                row_idx = 1
                            elif row_val == "header":
                                row_idx = 0  # Строка заголовков
                            else:
                                # Если строковое значение не распознано,
                                # используем 0 по умолчанию
                                row_idx = 0
                        else:
                            # Если значение числовое, используем как есть
                            row_idx = int(row_val)

                        # Убедимся, что индекс строки в допустимом диапазоне
                        # 0 - заголовки, 1..len(rows) - строки данных
                        max_valid_index = len(
                            rows
                        )  # максимальный допустимый индекс для строк данных
                        if row_idx < 0:
                            row_idx = 0
                        elif row_idx > max_valid_index:
                            row_idx = max_valid_index

                        # Применяем заливку ячейки
                        if "bg_color" in cell_prop:
                            bg_color = parse_color(cell_prop["bg_color"])
                            table_style_commands.append(
                                (
                                    "BACKGROUND",
                                    (col_idx, row_idx),
                                    (col_idx, row_idx),
                                    bg_color,
                                )
                            )

                        # Применяем цвет текста
                        if "text_color" in cell_prop:
                            text_color = parse_color(cell_prop["text_color"])
                            table_style_commands.append(
                                (
                                    "TEXTCOLOR",
                                    (col_idx, row_idx),
                                    (col_idx, row_idx),
                                    text_color,
                                )
                            )

                        # Применяем шрифт
                        if "font_name" in cell_prop:
                            font_name = normalize_font_name(
                                cell_prop["font_name"]
                            )
                            table_style_commands.append(
                                (
                                    "FONTNAME",
                                    (col_idx, row_idx),
                                    (col_idx, row_idx),
                                    font_name,
                                )
                            )

                        # Применяем размер шрифта
                        if "font_size" in cell_prop:
                            font_size = cell_prop["font_size"]
                            table_style_commands.append(
                                (
                                    "FONTSIZE",
                                    (col_idx, row_idx),
                                    (col_idx, row_idx),
                                    font_size,
                                )
                            )

                        # Применяем выравнивание
                        if "alignment" in cell_prop:
                            alignment = cell_prop["alignment"].upper()
                            table_style_commands.append(
                                (
                                    "ALIGN",
                                    (col_idx, row_idx),
                                    (col_idx, row_idx),
                                    alignment,
                                )
                            )

                        # Применяем вертикальное выравнивание
                        if "valign" in cell_prop:
                            valign = cell_prop["valign"].upper()
                            table_style_commands.append(
                                (
                                    "VALIGN",
                                    (col_idx, row_idx),
                                    (col_idx, row_idx),
                                    valign,
                                )
                            )

                        # Применяем границы
                        if (
                            "border_width" in cell_prop
                            and "border_color" in cell_prop
                        ):
                            border_width = cell_prop["border_width"]
                            border_color = parse_color(
                                cell_prop["border_color"]
                            )

                            # Применяем границы ко всем сторонам
                            table_style_commands.extend(
                                [
                                    (
                                        "LINEABOVE",
                                        (col_idx, row_idx),
                                        (col_idx, row_idx),
                                        border_width,
                                        border_color,
                                    ),
                                    (
                                        "LINEBELOW",
                                        (col_idx, row_idx),
                                        (col_idx, row_idx),
                                        border_width,
                                        border_color,
                                    ),
                                    (
                                        "LINELEFT",
                                        (col_idx, row_idx),
                                        (col_idx, row_idx),
                                        border_width,
                                        border_color,
                                    ),
                                    (
                                        "LINERIGHT",
                                        (col_idx, row_idx),
                                        (col_idx, row_idx),
                                        border_width,
                                        border_color,
                                    ),
                                ]
                            )
                    except Exception:
                        # Игнорируем ошибки при применении свойств ячейки
                        pass

                # Применяем стиль к таблице
                table.setStyle(TableStyle(table_style_commands))

                elements.append(table)
                elements.append(Spacer(1, 12))

        elif block_type == "math":
            # Для формул добавляем как изображение
            # с формулой или как обычный параграф
            formula = block.get("formula", "")
            caption = block.get("caption", "")

            # Используем параметры из JSON, если они есть, иначе - стандартные
            font_name = block.get("font_name", CYRILLIC_FONT)
            font_name = normalize_font_name(
                font_name
            )  # Нормализуем имя шрифта
            font_size = block.get("font_size", None)
            # Используем font_size как fallback,
            # если конкретные параметры не указаны
            math_font_size = (
                block.get("math_font_size", None) or font_size or 12
            )
            caption_font_size = (
                block.get("caption_font_size", None) or font_size or 10
            )
            bold = block.get("bold", False)
            italic = block.get(
                "italic", True
            )  # По умолчанию курсив для формул
            alignment = block.get("alignment", "left")  # left, center, right

            # Попробуем создать изображение с формулой с помощью matplotlib
            try:
                import matplotlib
                import matplotlib.pyplot as plt
                import io as io_module

                matplotlib.use("Agg")  # Use non-interactive backend

                # Настройка фигуры matplotlib с размерами,
                # пропорциональными длине формулы
                # Преобразуем размеры из миллиметров в дюймы (1 дюйм = 25.4 мм)
                base_width_mm = 60  # базовая ширина в мм
                width_factor = min(
                    len(formula) / 10, 3
                )  # масштабируем в зависимости от длины формулы, не более х3
                width_mm = base_width_mm * width_factor

                # Высота пропорциональна ширине, но с минимальным значением
                height_mm = max(
                    width_mm * 0.2, 15
                )  # высота в мм, не менее 15 мм

                # Преобразуем миллиметры в дюймы для matplotlib
                width_inches = width_mm / 25.4
                height_inches = height_mm / 25.4

                fig, ax = plt.subplots(figsize=(width_inches, height_inches))
                ax.text(
                    0.5,
                    0.5,
                    f"${formula}$",
                    fontsize=16,
                    ha="center",
                    va="center",
                    transform=ax.transAxes,
                )
                ax.axis("off")  # Скрыть оси

                # Сохраняем в байтовый поток
                img_buffer = io_module.BytesIO()
                plt.savefig(
                    img_buffer, format="png", bbox_inches="tight", dpi=150
                )
                img_buffer.seek(0)

                # Закрываем фигуру matplotlib
                plt.close(fig)

                # Преобразуем размеры из миллиметров в точки
                # (1 мм = 2.834645669 points)
                width_points = width_mm * 2.834645669
                height_points = height_mm * 2.834645669

                # Создаем изображение с подходящими размерами
                img = RLImage(
                    img_buffer, width=width_points, height=height_points
                )

                # Выравнивание изображения
                if alignment == "center":
                    img.hAlign = "CENTER"
                elif alignment == "right":
                    img.hAlign = "RIGHT"
                else:
                    img.hAlign = "LEFT"

                elements.append(img)

                if caption:
                    # Создаем стиль для подписи
                    caption_style = ParagraphStyle(
                        "CaptionStyle",
                        parent=styles["Normal"],
                        fontSize=caption_font_size,
                        fontName=font_name,
                        alignment=alignment_val,
                    )

                    caption_para = Paragraph(caption, caption_style)
                    elements.append(caption_para)

            except ImportError:
                # Если matplotlib не установлен, используем обычный текст
                # Определяем выравнивание
                alignment_val = TA_LEFT
                if alignment == "center":
                    alignment_val = TA_CENTER
                elif alignment == "right":
                    alignment_val = TA_RIGHT

                # Создаем стиль для формулы
                math_style = ParagraphStyle(
                    "MathStyle",
                    parent=styles["Normal"],
                    fontSize=math_font_size,
                    fontName=font_name,
                    alignment=alignment_val,
                )

                # Формируем текст формулы
                formula_text = f"Формула: {formula}"
                if bold:
                    formula_text = f"<b>{formula_text}</b>"
                if italic:
                    formula_text = f"<i>{formula_text}</i>"

                # Добавляем формулу как специальный параграф
                math_para = Paragraph(formula_text, math_style)
                elements.append(math_para)

                if caption:
                    # Создаем стиль для подписи
                    caption_style = ParagraphStyle(
                        "CaptionStyle",
                        parent=styles["Normal"],
                        fontSize=caption_font_size,
                        fontName=font_name,
                        alignment=alignment_val,
                    )

                    caption_para = Paragraph(caption, caption_style)
                    elements.append(caption_para)
            except Exception as e:
                # Если возникла ошибка при создании изображения,
                # используем обычный текст
                print(f"Ошибка при создании изображения формулы: {e}")

                # Определяем выравнивание
                alignment_val = TA_LEFT
                if alignment == "center":
                    alignment_val = TA_CENTER
                elif alignment == "right":
                    alignment_val = TA_RIGHT

                # Создаем стиль для формулы
                math_style = ParagraphStyle(
                    "MathStyle",
                    parent=styles["Normal"],
                    fontSize=math_font_size,
                    fontName=font_name,
                    alignment=alignment_val,
                )

                # Формируем текст формулы
                formula_text = f"Формула: {formula}"
                if bold:
                    formula_text = f"<b>{formula_text}</b>"
                if italic:
                    formula_text = f"<i>{formula_text}</i>"

                # Добавляем формулу как специальный параграф
                math_para = Paragraph(formula_text, math_style)
                elements.append(math_para)

                if caption:
                    # Создаем стиль для подписи
                    caption_style = ParagraphStyle(
                        "CaptionStyle",
                        parent=styles["Normal"],
                        fontSize=caption_font_size,
                        fontName=font_name,
                        alignment=alignment_val,
                    )

                    caption_para = Paragraph(caption, caption_style)
                    elements.append(caption_para)

        elif block_type == "function_graph":
            # Для графиков функций создаем изображение с помощью matplotlib
            try:
                import matplotlib
                import matplotlib.pyplot as plt
                import numpy as np
                import io as io_module

                matplotlib.use("Agg")  # Use non-interactive backend

                function_expr = block.get("function", "x")
                x_min = block.get("x_min", -10)
                x_max = block.get("x_max", 10)
                title = block.get("title", "")
                xlabel = block.get("xlabel", "x")
                ylabel = block.get("ylabel", "y")
                width = block.get("width", 6)
                height = block.get("height", 4)
                line_color = block.get("line_color", "blue")
                line_width = block.get("line_width", 2)
                show_grid = block.get("show_grid", True)
                caption = block.get("caption", "")
                alignment = block.get("alignment", "center")

                # Создаем массив x значений
                x = np.linspace(x_min, x_max, 400)

                # Подготавливаем выражение для вычисления
                # Проверяем, что выражение не содержит LaTeX команды
                # или другие недопустимые элементы
                safe_expr = function_expr.replace(" ", "")

                if "=" in safe_expr:
                    # Если в выражении есть '=', пытаемся извлечь правую часть
                    parts = safe_expr.split("=", 1)
                    if len(parts) > 1:
                        safe_expr = parts[
                            1
                        ].strip()  # Берем правую часть после знака равно
                    else:
                        safe_expr = safe_expr.replace(
                            "=", ""
                        )  # Просто убираем знак равно если он не в уравнении

                # Заменяем '^' на '**' для возведения в степень в Python
                safe_expr = safe_expr.replace("^", "**")

                if (
                    "\\equiv" in safe_expr
                    or "\\pmod" in safe_expr
                    or "\\int" in safe_expr
                    or "\\sum" in safe_expr
                    or "\\frac" in safe_expr
                ):
                    raise ValueError(
                        f"Invalid function expression: '{function_expr}'."
                        f" This appears to be a LaTeX formula, "
                        f"not a mathematical expression."
                        f" Use 'math' block type for LaTeX formulas."
                    )

                # Вычисляем y значения
                # Создаем безопасное окружение для eval
                namespace = {
                    "x": x,
                    "np": np,
                    "sin": np.sin,
                    "cos": np.cos,
                    "tan": np.tan,
                    "exp": np.exp,
                    "log": np.log,
                    "sqrt": np.sqrt,
                    "abs": np.abs,
                    "pi": np.pi,
                    "e": np.e,
                }

                y = eval(safe_expr, {"__builtins__": {}}, namespace)

                # Создаем график
                fig, ax = plt.subplots(figsize=(width, height))
                ax.plot(x, y, color=line_color, linewidth=line_width)

                # Добавляем заголовок и подписи к осям
                if title:
                    ax.set_title(title)
                ax.set_xlabel(xlabel)
                ax.set_ylabel(ylabel)

                # Показываем сетку, если нужно
                if show_grid:
                    ax.grid(True)

                # Сохраняем в байтовый поток
                img_buffer = io_module.BytesIO()
                plt.savefig(
                    img_buffer, format="png", bbox_inches="tight", dpi=150
                )
                img_buffer.seek(0)

                # Закрываем фигуру matplotlib
                plt.close(fig)

                # Преобразуем размеры из дюймов в точки (1 дюйм = 72 точки)
                width_points = width * 72
                height_points = height * 72

                # Создаем изображение с подходящими размерами
                img = RLImage(
                    img_buffer, width=width_points, height=height_points
                )

                # Выравнивание изображения
                if alignment == "center":
                    img.hAlign = "CENTER"
                elif alignment == "right":
                    img.hAlign = "RIGHT"
                else:
                    img.hAlign = "LEFT"

                elements.append(img)

                # Если есть подпись, добавляем её
                if caption:
                    # Определяем выравнивание
                    alignment_val = TA_LEFT
                    if alignment == "center":
                        alignment_val = TA_CENTER
                    elif alignment == "right":
                        alignment_val = TA_RIGHT

                    # Создаем стиль для подписи
                    font_name = block.get("font_name", CYRILLIC_FONT)
                    font_name = normalize_font_name(font_name)
                    caption_font_size = block.get("caption_font_size", 10)

                    caption_style = ParagraphStyle(
                        "CaptionStyle",
                        parent=styles["Normal"],
                        fontSize=caption_font_size,
                        fontName=font_name,
                        alignment=alignment_val,
                    )

                    caption_para = Paragraph(caption, caption_style)
                    elements.append(caption_para)

            except Exception as e:
                # Если возникла ошибка при создании изображения,
                # добавляем сообщение об ошибке
                print(f"Ошибка при создании графика функции: {e}")

                # Определяем выравнивание
                alignment = block.get("alignment", "center")
                alignment_val = TA_LEFT
                if alignment == "center":
                    alignment_val = TA_CENTER
                elif alignment == "right":
                    alignment_val = TA_RIGHT

                # Создаем стиль для сообщения об ошибке
                font_name = block.get("font_name", CYRILLIC_FONT)
                font_name = normalize_font_name(font_name)

                error_style = ParagraphStyle(
                    "ErrorStyle",
                    parent=styles["Normal"],
                    fontSize=10,
                    fontName=font_name,
                    textColor=RLColor(1, 0, 0),  # Красный цвет
                    alignment=alignment_val,
                )

                error_para = Paragraph(
                    f"Ошибка при построении графика: {str(e)}", error_style
                )
                elements.append(error_para)

        elif block_type == "toc":
            # Для оглавления создаем специальный блок
            title = block.get("title", "Оглавление")
            levels = block.get("levels", [1, 2, 3])
            font_name = block.get("font_name", CYRILLIC_FONT)
            font_name = normalize_font_name(font_name)
            font_size = block.get("font_size", 12)
            indent = block.get("indent", 10)
            leader_dots = block.get("leader_dots", True)
            include_pages = block.get("include_pages", True)
            alignment = block.get("alignment", "left")  # left, center, right

            # Создаем заголовок оглавления
            # Определяем выравнивание
            alignment_val = TA_LEFT
            if alignment == "center":
                alignment_val = TA_CENTER
            elif alignment == "right":
                alignment_val = TA_RIGHT
            elif alignment == "justify":
                alignment_val = (
                    TA_LEFT  # ReportLab doesn't have justify for this context
                )

            toc_title_style = ParagraphStyle(
                "TOCTitle",
                parent=styles["Heading1"],
                fontSize=font_size,
                fontName=font_name,
                spaceAfter=12,
                alignment=alignment_val,
            )

            toc_title = Paragraph(title, toc_title_style)
            elements.append(toc_title)

            # В PDF формате сложно сделать динамическое оглавление
            # с гиперссылками
            # Поэтому создаем структурированный список, имитирующий оглавление
            toc_entries = block.get(
                "entries",
                [
                    {"text": "Введение", "level": 1, "page": 1},
                    {"text": "Основная часть", "level": 1, "page": 3},
                    {"text": "Подраздел 1", "level": 2, "page": 4},
                    {"text": "Подраздел 2", "level": 2, "page": 6},
                    {"text": "Заключение", "level": 1, "page": 8},
                    {"text": "Список литературы", "level": 1, "page": 10},
                ],
            )

            for entry in toc_entries:
                level = entry["level"]
                text = entry["text"]
                page = entry.get("page", 1)  # по умолчанию страница 1

                # Проверяем, входит ли уровень в разрешенные
                if level in levels:
                    # Создаем отступ в зависимости от уровня
                    left_padding = 6 + (level - 1) * indent

                    # Определяем выравнивание для элементов оглавления
                    toc_alignment_val = TA_LEFT
                    if alignment == "center":
                        toc_alignment_val = TA_CENTER
                    elif alignment == "right":
                        toc_alignment_val = TA_RIGHT
                    elif alignment == "justify":
                        toc_alignment_val = TA_LEFT

                    toc_entry_style = ParagraphStyle(
                        "TOCEntry",
                        parent=styles["Normal"],
                        fontSize=font_size - 2 if level > 1 else font_size,
                        fontName=font_name,
                        leftIndent=left_padding,
                        spaceAfter=6,
                        alignment=toc_alignment_val,
                    )

                    # Формируем текст оглавления с точками и номером страницы
                    if include_pages and leader_dots:
                        # В простом PDF формате сложно сделать точки
                        # до номера страницы
                        # Поэтому просто добавим текст с информацией о странице
                        toc_text = f"{text} .................... {page}"
                    else:
                        toc_text = text

                    toc_entry = Paragraph(toc_text, toc_entry_style)
                    elements.append(toc_entry)

    # Собираем документ
    doc.build(elements)

    # Перемещаем указатель в начало
    pdf_buffer.seek(0)

    return pdf_buffer


def check_user_wants_pdf_format(user_message):
    """
    Проверяет, хочет ли пользователь получить ответ в формате PDF.

    Args:
        user_message (str): Сообщение от пользователя для анализа

    Returns:
        bool: True если пользователь хочет формат PDF, False в противном случае
    """
    message = user_message.lower()
    return (
        "pdf" in message
        or "формат pdf" in message
        or "в pdf" in message
        or "в формате pdf" in message
        or "в формате пдф" in message
        or "пдф" in message
        or "adobe" in message
        or "документ pdf" in message
        or "pdf документ" in message
    )


async def send_pdf_response(update, reply):
    """
    Отправляет PDF-файл с ответом пользователю.

    Args:
        update: Объект обновления Telegram
        reply: Ответ от модели, который будет преобразован в PDF
    """
    try:
        # Проверяем, что reply не пустой
        if not reply or reply.strip() == "":
            raise ValueError("Пустой ответ от модели")
        # Удаляем маркеры кода, если они есть
        cleaned_reply = reply.strip()
        if cleaned_reply.startswith("```json"):
            cleaned_reply = cleaned_reply[7:]  # Удаляем '```json'
        elif cleaned_reply.startswith("```"):
            cleaned_reply = cleaned_reply[3:]  # Удаляем '```'

        if cleaned_reply.endswith("```"):
            cleaned_reply = cleaned_reply[:-3]  # Удаляем закрывающий '```'

        cleaned_reply = cleaned_reply.strip()
        data = json.loads(cleaned_reply)
        pdf_buffer = create_pdf_from_json(data)

        await update.message.reply_document(
            document=InputFile(pdf_buffer, filename="document.pdf"),
            caption="Ваш ответ в формате PDF",
        )
    except json.JSONDecodeError as e:
        # Если ответ не является валидным JSON,
        # отправляем обычное сообщение
        safe_reply = escape_markdown(reply, version=2)
        await send_long_message(update, safe_reply, parse_mode="MarkdownV2")
        print(f"Ошибка разбора JSON при создании PDF: {e}")
    except ValueError as e:
        # Если возникла ошибка значения (например, пустой ответ)
        safe_reply = escape_markdown(reply, version=2)
        await send_long_message(update, safe_reply, parse_mode="MarkdownV2")
        print(f"Ошибка значения при создании PDF: {e}")
    except Exception as e:
        # Если не удалось создать или отправить PDF,
        # отправляем обычное сообщение
        safe_reply = escape_markdown(reply, version=2)
        await send_long_message(update, safe_reply, parse_mode="MarkdownV2")
        print(f"Ошибка при создании или отправке PDF файла: {e}")
