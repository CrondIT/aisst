"""Utility functions for creating and handling XLSX files."""

import io
import json
import re
import xlsxwriter

# Константы для формата страницы и полей (в миллиметрах)
DEFAULT_PAGE_WIDTH_MM = 210  # Ширина A4 в мм
DEFAULT_PAGE_HEIGHT_MM = 297  # Высота A4 в мм
DEFAULT_LEFT_MARGIN_MM = 20  # Левое поле по умолчанию
DEFAULT_RIGHT_MARGIN_MM = 10  # Правое поле по умолчанию
DEFAULT_TOP_MARGIN_MM = 15  # Верхнее поле по умолчанию
DEFAULT_BOTTOM_MARGIN_MM = 15  # Нижнее поле по умолчанию

JSON_SCHEMA_EXCEL = """
    Верни ТОЛЬКО валидный JSON без пояснений.
    Не используй markdown, только JSON.
    Не включай тройные кавычки в значениях.
    Строгая схема:
    {
    "meta": {"title": "string", "hide_title": false},
    "header": {
        "content": "string",
        "font_name": "string",
        "font_size": 12,
        "color": "string",
        "bold": false,
        "italic": false,
        "alignment": "left",
        "page_number": {
            "enabled": false,
            "format": "Page {PAGE} of {NUMPAGES}",
            "position": "right"
        }
    },
    "footer": {
        "content": "string",
        "font_name": "string",
        "font_size": 12,
        "color": "string",
        "bold": false,
        "italic": false,
        "alignment": "left",
        "page_number": {
            "enabled": false,
            "format": "Page {PAGE} of {NUMPAGES}",
            "position": "right"
        }
    },
    "sheets": [
        {
            "name": "string",
            "data": [
                ["cell1", "cell2", "cell3"],
                ["row2_col1", "row2_col2", "row2_col3"]
            ],
            "headers": ["header1", "header2", "header3"],
            "formats": {
                "header": {"bold": true, "bg_color": "#D3D3D3"},
                "cell": {"font_size": 12}
            },
            "column_widths": [20, 30, 15],  // Ширина столбцов
            "rows": [
                {
                    "index": 0,
                    "height": 25
                }
            ],
            "cells": [
                {
                    "row": 0,
                    "col": 0,
                    "format": {
                        "bold": true,
                        "bg_color": "#D3D3D3",
                        "border": "thin",
                        "text_wrap": true,
                        "num_format": "#,##0.00",
                        "formula": "=SUM(B2:B10)",
                        "bg_color": "#FFFF00"
                    }
                }
            ]
        }
    ]
    }
    """


class XlsxRenderer:
    def __init__(self):
        self.workbook = None

    def render(self, data: dict, output):
        """
        Рендерим JSON документ в XLSX.
        :param data: dict с ключами "meta" и "sheets"
        :param output: path или BytesIO
        """
        # Создаем workbook в памяти
        if isinstance(output, io.BytesIO):
            self.workbook = xlsxwriter.Workbook(output, {"in_memory": True})
        else:
            self.workbook = xlsxwriter.Workbook(output)

        self._render_meta(data.get("meta", {}))
        self._render_sheets(data.get("sheets", []))

        # Сохраняем в файл или BytesIO
        self.workbook.close()

    def _render_meta(self, meta: dict):
        # Создаем общую информацию в отдельном листе, если есть заголовок
        if "title" in meta:
            worksheet = self.workbook.add_worksheet("Info")
            worksheet.write(0, 0, "Title")
            worksheet.write(0, 1, meta["title"])

    def _render_sheets(self, sheets: list):
        for i, sheet_data in enumerate(sheets):
            name = sheet_data.get("name", f"Sheet{i+1}")
            worksheet = self.workbook.add_worksheet(name)

            # Установка полей страницы
            worksheet.set_margins(
                left=DEFAULT_LEFT_MARGIN_MM / 25.4,  # Преобразуем мм в дюймы
                right=DEFAULT_RIGHT_MARGIN_MM / 25.4,
                top=DEFAULT_TOP_MARGIN_MM / 25.4,
                bottom=DEFAULT_BOTTOM_MARGIN_MM / 25.4,
            )

            # Установка ориентации и размера страницы
            worksheet.set_paper(9)  # A4
            worksheet.set_portrait()  # Книжная ориентация

            headers = sheet_data.get("headers", [])
            data_rows = sheet_data.get("data", [])
            formats = sheet_data.get("formats", {})
            column_widths = sheet_data.get("column_widths", [])
            rows_config = sheet_data.get("rows", [])
            cells_config = sheet_data.get("cells", [])

            # Создаем все возможные форматы
            header_format = self._create_format(formats.get("header", {}))
            cell_format = self._create_format(formats.get("cell", {}))
            # Создаем дополнительные форматы, если они определены
            additional_formats = {}
            for format_name, format_props in formats.items():
                if format_name not in [
                    "header",
                    "cell",
                ]:  # Пропускаем уже созданные
                    additional_formats[format_name] = self._create_format(
                        format_props
                    )

            # Устанавливаем высоту строк, если указано
            for row_config in rows_config:
                row_index = row_config.get("index", 0)
                row_height = row_config.get("height", 15)
                worksheet.set_row(row_index, row_height)

            # Записываем заголовки
            for col_num, header in enumerate(headers):
                worksheet.write(0, col_num, header, header_format)

            # Записываем данные
            for row_num, row_data in enumerate(data_rows, start=1):
                for col_num, cell_data in enumerate(row_data):
                    if col_num < len(
                        headers
                    ):  # Только если колонка существует в заголовках
                        # Проверяем, есть ли специальный формат для этой строки
                        # Например, если в строке есть слово "ИТОГО",
                        #  используем формат "total"
                        target_format = cell_format
                        if any(
                            "итого" in str(cell).lower() for cell in row_data
                        ):  # Проверяем, содержит ли строка "ИТОГО"
                            if "total" in additional_formats:
                                target_format = additional_formats["total"]

                        # Проверяем, есть ли специальный формат
                        # для конкретной ячейки
                        cell_found = False
                        for cell_config in cells_config:
                            if (
                                cell_config.get("row") == row_num
                                and cell_config.get("col") == col_num
                            ):
                                cell_format_props = cell_config.get(
                                    "format", {}
                                )
                                cell_specific_format = self._create_format(
                                    cell_format_props
                                )

                                # Проверяем, содержит ли ячейка формулу
                                formula = cell_format_props.get("formula")
                                if formula:
                                    if cell_specific_format:
                                        worksheet.write_formula(
                                            row_num,
                                            col_num,
                                            formula,
                                            cell_specific_format,
                                            cell_data,
                                        )
                                    else:
                                        worksheet.write_formula(
                                            row_num,
                                            col_num,
                                            formula,
                                            cell_data,
                                        )
                                else:
                                    if cell_specific_format:
                                        worksheet.write(
                                            row_num,
                                            col_num,
                                            cell_data,
                                            cell_specific_format,
                                        )
                                    else:
                                        worksheet.write(
                                            row_num,
                                            col_num,
                                            cell_data,
                                            target_format,
                                        )
                                cell_found = True
                                break

                        if not cell_found:
                            # Если нет специального формата для ячейки,
                            # используем стандартный
                            worksheet.write(
                                row_num, col_num, cell_data, target_format
                            )

            # Устанавливаем ширину колонок
            for col_num, header in enumerate(headers):
                # Если указана ширина колонки в column_widths, используем её
                if col_num < len(column_widths):
                    width = column_widths[col_num]
                else:
                    # Автонастройка ширины колонки на основе заголовка
                    width = max(len(str(header)), 10)

                worksheet.set_column(col_num, col_num, width)

    def _create_format(self, format_dict: dict):
        """Создает формат XLSX из словаря параметров"""
        if not format_dict:
            return None

        # Создаем копию словаря, чтобы не изменять оригинальный
        format_copy = format_dict.copy()

        # Преобразуем цвета из HEX в формат XLSX
        if "bg_color" in format_copy and format_copy["bg_color"].startswith(
            "#"
        ):
            format_copy["bg_color"] = format_copy["bg_color"][1:]  # Убираем #

        if "fg_color" in format_copy and format_copy["fg_color"].startswith(
            "#"
        ):
            format_copy["fg_color"] = format_copy["fg_color"][1:]  # Убираем #

        # Обработка параметра border
        if "border" in format_copy:
            border_value = format_copy["border"]
            # В xlsxwriter для полного применения границ нужно установить
            # отдельные параметры для каждой стороны
            if border_value == "thin":
                # Устанавливаем границы для всех сторон
                format_copy.update(
                    {
                        "top": 1,
                        "bottom": 1,
                        "left": 1,
                        "right": 1,
                        "top_color": "black",
                        "bottom_color": "black",
                        "left_color": "black",
                        "right_color": "black",
                    }
                )
            elif border_value == "medium":
                format_copy.update(
                    {
                        "top": 2,
                        "bottom": 2,
                        "left": 2,
                        "right": 2,
                        "top_color": "black",
                        "bottom_color": "black",
                        "left_color": "black",
                        "right_color": "black",
                    }
                )
            elif border_value == "thick":
                format_copy.update(
                    {
                        "top": 3,
                        "bottom": 3,
                        "left": 3,
                        "right": 3,
                        "top_color": "black",
                        "bottom_color": "black",
                        "left_color": "black",
                        "right_color": "black",
                    }
                )
            # Удаляем исходный параметр border, так как он заменен
            # индивидуальными параметрами
            del format_copy["border"]

        # Обработка параметра valign (вертикальное выравнивание)
        if "valign" in format_copy:
            valign_value = format_copy["valign"]
            if valign_value in ["top", "vcenter", "bottom"]:
                format_copy["valign"] = valign_value
            else:
                del format_copy["valign"]  # Удаляем некорректное значение

        # Обработка параметра align (горизонтальное выравнивание)
        if "align" in format_copy:
            align_value = format_copy["align"]
            if align_value in ["left", "center", "right"]:
                format_copy["align"] = align_value
            else:
                del format_copy["align"]  # Удаляем некорректное значение

        # Обработка параметра text_wrap (перенос текста)
        if "text_wrap" in format_copy:
            format_copy["text_wrap"] = bool(format_copy["text_wrap"])

        # Обработка числового формата
        if "num_format" in format_copy:
            # Просто оставляем как есть, xlsxwriter сам обработает
            pass

        # Обработка других параметров, игнорируя неизвестные
        # Создаем список известных параметров
        known_params = {
            "align",
            "valign",
            "bold",
            "italic",
            "underline",
            "font_strikeout",
            "font_script",
            "font_outline",
            "font_shadow",
            "font_family",
            "font_size",
            "font_color",
            "bg_color",
            "fg_color",
            "pattern",
            "border",
            "border_color",
            "top",
            "bottom",
            "left",
            "right",
            "top_color",
            "bottom_color",
            "left_color",
            "right_color",
            "text_wrap",
            "rotation",
            "indent",
            "shrink",
            "merge_range",
            "center_across",
            "reading_order",
            "num_format",
            "locked",
            "hidden",
            "align_vertical",
        }

        # Удаляем неизвестные параметры, чтобы избежать ошибок
        unknown_params = set(format_copy.keys()) - known_params
        for param in unknown_params:
            del format_copy[param]

        try:
            return self.workbook.add_format(format_copy)
        except Exception:
            # Если формат некорректен, возвращаем None
            return None


def check_user_wants_xlsx_format(user_message):
    """
    Check if user wants to receive description in XLSX format.

    Args:
        user_message (str): The message from user to analyze

    Returns:
        bool: True if user wants XLSX format, False otherwise
    """
    message = user_message.lower()
    return (
        "xlsx" in message
        or "excel" in message
        or "в экселе" in message
        or "в формате excel" in message
        or "в формате xlsx" in message
        or "в формате xls" in message
        or "в формате таблицы" in message
        or "в виде экселя" in message
        or "в виде xlsx" in message
        or "excel документ" in message
        or "документ excel" in message
        or "табличный формат" in message
    )


async def send_xlsx_response(user_id: int, reply: str):
    """
    Отправляет XLSX-файл с ответом пользователю через MAX API.
    
    Args:
        user_id: ID пользователя в MAX
        reply: Ответ от модели, который будет преобразован в XLSX
    """
    import max_api
    from utils import logger
    
    try:
        if not reply or reply.strip() == "":
            raise ValueError("Пустой ответ от модели")

        cleaned_reply = reply.strip()
        if cleaned_reply.startswith("```json"):
            cleaned_reply = cleaned_reply[7:]
        elif cleaned_reply.startswith("```"):
            cleaned_reply = cleaned_reply[3:]

        if cleaned_reply.endswith("```"):
            cleaned_reply = cleaned_reply[:-3]

        cleaned_reply = cleaned_reply.strip()

        # Исправляем JSON: добавляем кавычки к названиям полей без них
        cleaned_reply = re.sub(
            r'(\{|\,)\s*([a-zA-Z_]\w*)\s*:',
            r'\1"\2":',
            cleaned_reply,
        )
        # Удаляем запятые перед ] и }
        cleaned_reply = re.sub(r',\s*\]', ']', cleaned_reply)
        cleaned_reply = re.sub(r',\s*\}', '}', cleaned_reply)

        data = json.loads(cleaned_reply, strict=False)
        xlsx_io = io.BytesIO()
        renderer = XlsxRenderer()
        renderer.render(data, xlsx_io)
        xlsx_io.seek(0)
        xlsx_bytes = xlsx_io.getvalue()

        result = await max_api.send_document(
            user_id=user_id,
            file_data=xlsx_bytes,
            filename="document.xlsx",
            file_type="file"
        )
        
        if result != 200:
            logger.error(f"Ошибка отправки XLSX: status={result}")
            
    except json.JSONDecodeError as e:
        logger.error(f"Ошибка разбора JSON при создании XLSX: {e}")
        await max_api.send_message(user_id, "Не удалось создать Excel файл.")
    except ValueError as e:
        logger.error(f"Ошибка значения при создании XLSX: {e}")
        await max_api.send_message(user_id, "Пустой ответ от модели.")
    except Exception as e:
        logger.error(f"Ошибка при создании или отправке XLSX файла: {e}")
        await max_api.send_message(user_id, f"Ошибка при создании Excel файла: {str(e)}")
