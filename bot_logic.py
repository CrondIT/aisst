"""Модуль бизнес-логики бота: обработка команд и сообщений."""
import os
from dataclasses import dataclass
from fastapi import Request

import db
from config import MODELS
from global_state import (
    get_user_mode,
    set_user_mode,
    set_user_file_data,
    clear_user_pending_delete,
    enqueue_task,
    _use_redis,
    clear_mentor_state,
    get_user_edit_queue,
    set_user_edit_queue,
    MAX_REF_IMAGES,
    get_user_edit_data,
    set_user_edit_data,
    clear_user_context_async,
    get_user_context_async,
    set_user_context_async,
    get_user_gemini_image_queue,
    set_user_gemini_image_queue,
    clear_user_gemini_image_queue,
    clear_user_gemini_files,
    get_user_gemini_files,
    add_user_gemini_file,
)
from utils import logger
from keyboards import COLLEGE_BUTTONS, START_BUTTONS
from shared.message_utils import get_file_extracted_text
from rag_chain import save_to_vector_db
from mentor.mentor_logic import handle_mentor_mode
from handlers.base import ModeHandler
from handlers.gigachat_handler import GigachatHandler
from handlers.llm_handler import LlmDirectHandler
from handlers.rag_handler import RagHandler
from handlers.edit_handler import EditHandler
from handlers.image_handler import ImageHandler
import extract_text_from_file_utils


@dataclass
class CommandResult:
    """Результат обработки команды: текст ответа и опциональные кнопки."""
    text: str
    buttons: list[dict] | None = None
    format: str | None = "markdown"

mode_map = {
    "/aiagent": (
        "aiagent",
        "Режим: чат с ИИ по документам колледжа"
    ),
    "/gigachatpro": (
        "gigachatpro",
        "Режим: GigaChat Pro"
    ),
    "/chat": (
        "chat",
        "Режим: Chat"
    ),
    "/gemini": (
        "gemini",
        "Режим: Gemini"
    ),
    "/mentor": (
        "mentor",
        "Режим: Проверка знаний студентов""\n"
        "выберите документ по которому будем проверять, например:""\n"
        "документ:Учебник Введение в основы сварки"
    ),
    "/edit": (
        "edit",
        """
        Режим: редактирование промптов
        Команды:
        - "список" или "list" — показать все промпты
        - "промпт: X" или "выбрать: X" — выбрать промпт для редактирования
        - "система:" — начать редактирование system prompt
        - "человек:" или "human:" — начать редактирование human prompt
        - "сохранить" или "save" — сохранить изменения
        - "отмена" или "cancel" — отменить изменения
        - "версии" или "history" — показать историю версий
        - "откат: N" — откатить к версии N
        - "назад" — вернуться к списку
        """
    ),
    "/rag": (
        "rag",
        "Режим настройки базы знаний (RAG)." "\n"
        "Загрузка документов в базу знаний - загрузите документ pdf" "\n"
        "Просмотр наименований документов в базе - наберите ls" "\n"
        "Удаление документа из базы знаний:" "\n"
        " для удаления документа отправьте его название"
    ),
    "/prompt": (
        "edit",
        "Режим: редактирование промптов"
    ),
    "/image": (
        "image",
        "Режим: генерация и редактирование изображений\n"
        "Отправьте текстовый запрос для создания изображения\n"
        "Или отправьте изображение с описанием для редактирования"
    ),
    "/clear": (
        None,
        "Очистить историю диалога в текущем режиме"
    ),
}


# Режимы, которые обрабатываются через LLM Worker (Redis очередь)
LLM_QUEUE_MODES = {"gigachatpro", "chat", "gemini"}


async def enqueue_llm_request(
    user_text: str,
    sender: dict,
    mode: str,
) -> None:
    """
    Сохраняет сообщение пользователя в контекст и ставит задачу в очередь LLM.

    Вызывается из max_update_handler вместо прямого вызова handle_message
    для LLM-режимов, когда USE_REDIS=True.
    """
    user_id = int(sender.get("user_id"))

    # 1. Загружаем контекст и добавляем сообщение пользователя
    context = await get_user_context_async(user_id, mode)
    context.append({"role": "user", "content": user_text})
    await set_user_context_async(user_id, mode, context)

    # 2. Проверяем, есть ли файл
    extracted_text = get_file_extracted_text(user_id)

    # 3. Для gemini — собираем очереди изображений и файлов
    gemini_image_queue = None
    gemini_extracted_text = None
    if mode == "gemini":
        gemini_image_queue = get_user_gemini_image_queue(user_id)
        clear_user_gemini_image_queue(user_id)
        gemini_files = get_user_gemini_files(user_id)
        if gemini_files:
            parts = []
            for f in gemini_files:
                parts.append(f"[{f['name']}]:\n{f['text']}")
            gemini_extracted_text = "\n\n---\n\n".join(parts)

    # 4. Ставим задачу в очередь
    model = MODELS.get(mode, MODELS["aiagent"])
    task_data = {
        "mode": mode,
        "model": model,
        "user_id": user_id,
        "user_text": user_text,
        "sender": sender,
        "extracted_text": gemini_extracted_text or extracted_text,
        "temperature": 0.7,
    }
    if gemini_image_queue:
        task_data["gemini_image_queue"] = gemini_image_queue

    if not _use_redis:
        raise RuntimeError("USE_REDIS=false — LLM очередь не доступна")

    enqueue_task("llm", task_data, priority="normal")


class _MentorHandlerWrapper:
    """Тонкая обёртка над handle_mentor_mode для соответствия протоколу."""

    async def handle(self, request, user_text, sender) -> str | None:
        user_id = int(sender.get("user_id"))
        user_mode = get_user_mode(user_id) or "mentor"
        return await handle_mentor_mode(request, user_text, user_id, user_mode)


# Словарь обработчиков режимов
HANDLERS: dict[str, ModeHandler] = {
    "aiagent":     GigachatHandler(),
    "gigachatpro": LlmDirectHandler("giga_client",   MODELS["gigachatpro"], "gigachatpro", "GigaChat Pro клиент не настроен."),
    "chat":        LlmDirectHandler("openai_client",  MODELS["chat"],        "chat",        "OpenAI клиент не настроен."),
    "gemini":      LlmDirectHandler("gemini_client",  MODELS["gemini"],      "gemini",      "Gemini клиент не настроен."),
    "mentor":      _MentorHandlerWrapper(),
    "edit":        EditHandler(),
    "rag":         RagHandler(),
    "image":       ImageHandler("openai_client", MODELS["image"], "OpenAI клиент не настроен."),
}


async def _handle_models_command(app_state: object) -> str:
    """Возвращает список доступных моделей Gemini и OpenAI."""
    lines = []
    
    if app_state and hasattr(app_state, "gemini_client"):
        try:
            gemini_info = await app_state.gemini_client.list_models()
            lines.append(gemini_info)
        except Exception as e:
            logger.error(f"Ошибка получения моделей Gemini: {e}")
            lines.append("❌ Не удалось получить модели Gemini")
    else:
        lines.append("⚠️ Gemini клиент не настроен")
    
    if app_state and hasattr(app_state, "openai_client"):
        try:
            openai_info = await app_state.openai_client.list_models()
            lines.append(openai_info)
        except Exception as e:
            logger.error(f"Ошибка получения моделей OpenAI: {e}")
            lines.append("❌ Не удалось получить модели OpenAI")
    else:
        lines.append("⚠️ OpenAI клиент не настроен")
    
    return "\n\n".join(lines)


async def handle_command(
    user_text: str, sender: dict, app_state: object = None
) -> CommandResult | None:
    """
    Обработка команд бота - устанавливает режим пользователя
    и возвращает CommandResult (текст ответа + опциональные кнопки)
    или None, если команда не распознана.
    """
    if not user_text.startswith("/"):
        return None

    command = user_text.split(maxsplit=1)[0].lower()

    user_name = sender.get("name", "Неизвестный пользователь")
    user_id = int(sender.get("user_id"))
    user_data = await db.get_user(user_id)
    if user_data is None:
        await db.create_user(user_id, user_name)
        user_data = await db.get_user(user_id)
        if user_data is None:
            return CommandResult(text="Ошибка регистрации. Попробуйте позже.")
    # если пользователь гость то разрещен только один режим (для бота ССТ)
    if user_data["permission"] == 1:
        command = "/aiagent"  

    if command == "/college":
        return CommandResult(
            text="Я Ваш персональный ИИ помощник!",
            buttons=COLLEGE_BUTTONS,
            format=None,
        )

    if command == "/start":
        return CommandResult(
            text="Добро пожаловать!",
            buttons=START_BUTTONS,
            format=None,
        )

    if command == "/billing":
        if user_data:
            balance = user_data["coins"] + user_data["giftcoins"]
            return CommandResult(text=f"Уважаемый: {user_name}!\nВаш баланс: {balance} ₽")
        return CommandResult(text=f"Пользователь: {user_name} в списках не значится)")
    
    if command == "/models":
        text = await _handle_models_command(app_state)
        return CommandResult(text=text)
    
    if command == "/mode":
        return CommandResult(text=get_user_mode(user_id))
    
    if command in ("/clear", "/reset"):
        user_mode = get_user_mode(user_id)
        # Очищаем контекст текущего режима (из кэша И из БД)
        await clear_user_context_async(user_id, user_mode)
        if user_mode == "gemini":
            clear_user_gemini_image_queue(user_id)
            clear_user_gemini_files(user_id)
        return CommandResult(text=f"История диалога в режиме '{user_mode}' очищена.")
    
    if command in mode_map:
        mode, reply = mode_map[command]
        
        # При переключении с image — удаляем последний сохранённый файл
        current_mode = get_user_mode(user_id)
        if current_mode == "image" and mode != "image":
            edit_data = get_user_edit_data(user_id)
            last_edited = edit_data.get("last_image")
            if last_edited:
                try:
                    os.remove(last_edited)
                except OSError:
                    pass
            set_user_edit_data(user_id, {})
        # При переключении с gemini — очищаем очереди
        if current_mode == "gemini" and mode != "gemini":
            clear_user_gemini_image_queue(user_id)
            clear_user_gemini_files(user_id)
        
        set_user_mode(user_id, mode)
        # Очищаем состояние подтверждения удаления
        clear_user_pending_delete(user_id)
        
        # Для /image — очищаем очередь изображений для новой сессии
        if command == "/image":
            set_user_edit_queue(user_id, [])
        
        # Для /gemini — очищаем очереди для новой сессии
        if command == "/gemini":
            clear_user_gemini_image_queue(user_id)
            clear_user_gemini_files(user_id)
        
        # Для /mentor — очищаем состояние ментора и парсим новую команду
        if command == "/mentor":
            # Очищаем старое состояние ментора для новой сессии
            clear_mentor_state(user_id)
            
            after_command = user_text[len(command):].strip()
            if after_command:
                # Текст после /mentor — пусть handle_message его обработает
                return None
        
        return CommandResult(text=reply)
    
    return CommandResult(text="Вы ввели неправильную команду")


async def handle_message(
        request: Request, user_text: str, sender: dict
) -> str | None:
    """Обработка сообщений пользователя."""
    user_id = int(sender.get("user_id"))
    user_mode = get_user_mode(user_id)
    if not user_mode:
        user_mode = "aiagent"
        set_user_mode(user_id, user_mode)
    
    logger.info(
        f"handle_message: user_id={user_id}, mode={user_mode}"
    )
    
    handler = HANDLERS.get(user_mode)
    if handler is None:
        return "Используйте /gigachat для начала общения с ИИ."

    return await handler.handle(request, user_text, sender)


async def handle_image(
        request: Request, image_path: str, sender: dict
) -> str | None:
    """Обработка изображений."""
    user_id = int(sender.get("user_id"))
    user_mode = get_user_mode(user_id)
    if user_mode == "image":
        # Добавляем изображение в очередь для редактирования
        queue = get_user_edit_queue(user_id)
        queue.append(image_path)
        # Ограничиваем очередь последними MAX_REF_IMAGES
        if len(queue) > MAX_REF_IMAGES:
            queue = queue[-MAX_REF_IMAGES:]
        set_user_edit_queue(user_id, queue)
        return "Изображение получено. Опишите, что нужно изменить."
    if user_mode == "edit":
        return "Режим редактирования ещё не реализован."
    if user_mode == "gemini":
        # Добавляем изображение в очередь gemini-режима
        queue = get_user_gemini_image_queue(user_id)
        queue.append(image_path)
        if len(queue) > MAX_REF_IMAGES:
            queue = queue[-MAX_REF_IMAGES:]
        set_user_gemini_image_queue(user_id, queue)
        return f"Изображение получено ({len(queue)} в очереди). Задайте вопрос."
    return "Режим еще не работает"


async def handle_file(file_name: str, sender: dict) -> str | None:
    """Обработка файлов. Использует Redis очередь для больших файлов."""
    user_id = int(sender.get("user_id"))
    user_mode = get_user_mode(user_id)
    # если в режиме rag то загруженный файл отправляем в векторную базу
    if user_mode == "rag":
        if _use_redis:
            try:
                task_id = enqueue_task("rag", {
                    "file_path": file_name,
                    "user_id": user_id,
                    "sender": sender,
                })
                logger.info(
                    f"RAG задача {task_id[:8]}... добавлена в очередь "
                    f"для пользователя {user_id}"
                )
                return (
                    "📥 Файл принят в обработку.\n"
                    "Это может занять несколько минут.\n"
                    "Вы получите уведомление по завершении."
                )
            except Exception as e:
                logger.error(f"Не удалось добавить задачу в очередь: {e}")
        result = await save_to_vector_db(
            file_path=file_name, sender=sender,
        )
        await db.add_billing(
            user_id, user_mode, "save_to_vector_db", 0, 5, notes=result
        )
        return result
    # если любой другой режим то на данном этапе файл уже сохранен 
    # во временной папке, фиксируем путь в 
    # кроме режима редактирования изображений
    elif user_mode == "gemini":
        # извлекаем содержимое и добавляем в список gemini-файлов
        extracted_text = await extract_text_from_file_utils.process_uploaded_file(
                file_name
            )
        name = os.path.basename(file_name)
        add_user_gemini_file(user_id, {"name": name, "text": extracted_text})
        count = len(get_user_gemini_files(user_id))
        return f"Файл получен ({count} в списке). Задайте вопрос."

    else:
        # извлекаем содержимое файла
        extracted_text = await extract_text_from_file_utils.process_uploaded_file(
                file_name
            )
        # сохраняем содержимое файла
        set_user_file_data(user_id, {"extracted_text": extracted_text})

        return "Файл получен"
