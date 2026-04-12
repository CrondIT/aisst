from PIL import Image
import io

# Импортируем настройки
from global_state import MODELS
from models_config import client_edit_image


async def edit_image(image_paths, prompt: str):
    """
    Редактирует изображения или генерирует новое по описанию.
    Возвращает кортеж: (image_bytes, text_response), где:
    - image_bytes: байты изображения (или None, если ответ текстовый)
    - text_response: текстовый ответ от модели
    (или None, если ответ изображение)

    Args:
        image_paths: список путей к изображениям (или None, если генерация)
        prompt: текстовый запрос
    """
    # print(f"DEBUG edit_image: image_paths type:
    # {type(image_paths)}, value: {image_paths}")
    # если не переданы изображения, то генерим изображение
    if image_paths and isinstance(image_paths, list) and len(image_paths) > 0:
        contents = []
        for image_path in image_paths:
            # print(f"DEBUG: processing image_path: {image_path}")
            if image_path:  # Проверяем, что путь не None
                image = Image.open(image_path)
                contents.append(image)
        contents.append(prompt)
        model = MODELS["edit"]
    else:
        contents = [prompt]
        model = MODELS["image"]

    # Generate an image from a text prompt
    response = client_edit_image.models.generate_content(
        model=model,
        contents=contents,
    )

    # Проверяем структуру ответа Gemini
    if hasattr(response, "candidates") and response.candidates:
        for candidate in response.candidates:
            if hasattr(candidate, "content") and hasattr(
                candidate.content, "parts"
            ):
                for part in candidate.content.parts:
                    if (
                        hasattr(part, "inline_data")
                        and part.inline_data is not None
                    ):
                        # Получаем изображение из inline_data
                        image_bytes = part.inline_data.data

                        # Проверяем, является ли image_bytes строкой
                        if isinstance(image_bytes, str):
                            from base64 import b64decode

                            image_bytes = b64decode(image_bytes)

                        # Открываем изображение из байтов
                        img = Image.open(io.BytesIO(image_bytes))

                        # Конвертируем в байты
                        output_buffer = io.BytesIO()
                        img.save(output_buffer, "JPEG", quality=95)
                        output_buffer.seek(0)
                        edited_image_bytes = output_buffer.getvalue()
                        return edited_image_bytes, None
                    elif hasattr(part, "text") and part.text is not None:
                        return None, part.text
    else:
        # Альтернативная структура ответа Gemini
        for part in response.parts:
            if hasattr(part, "text") and part.text is not None:
                return None, part.text
            elif hasattr(part, "inline_data") and part.inline_data is not None:
                # Получаем изображение из inline_data
                image_bytes = part.inline_data.data

                # Проверяем, является ли image_bytes строкой (возможно base64)
                if isinstance(image_bytes, str):
                    from base64 import b64decode

                    image_bytes = b64decode(image_bytes)

                # Открываем изображение из байтов
                img = Image.open(io.BytesIO(image_bytes))

                # Конвертируем в байты
                output_buffer = io.BytesIO()
                img.save(output_buffer, "JPEG", quality=95)
                output_buffer.seek(0)
                edited_image_bytes = output_buffer.getvalue()
                return edited_image_bytes, None

    # Если не найдено изображение в ответе, выбрасываем исключение
    raise ValueError("Не удалось получить изображение из ответа модели")
