"""Модуль бизнес-логики бота: обработка команд и сообщений."""
import asyncio

from fastapi import Request

import db
from global_state import (
    get_user_mode,
    set_user_mode,
    set_user_file_data,
    get_user_file_data,
    get_user_pending_delete,
    set_user_pending_delete,
    clear_user_pending_delete,
    enqueue_task,
    _use_redis,
    clear_mentor_state,
    get_prompt_edit_state,
)
from utils import logger
from rag_chain import (
    save_to_vector_db,
    get_all_filenames_from_vector_db,
    delete_file_from_vector_db,
)
from prompt_builder import full_prompt
from file_output_utils import docx_utils, pdf_utils, xlsx_utils, rtf_utils
import extract_text_from_file_utils
from rag_chain import ask_rag  # ← единственный импорт для RAG
from mentor.mentor_logic import handle_mentor_mode
from prompt_edit import (
    _edit_mode_idle,
    _edit_mode_list,
    _edit_mode_view,
    _edit_mode_edit_system,
    _edit_mode_edit_human,
    _edit_mode_confirm,
)

mode_map = {
    "/gigachat": (
        "gigachat",
        "Режим: чат с ИИ по документам колледжа"
    ),
    "/gigachatpro": (
        "gigachatpro",
        "Режим: GigaChat Pro"
    ),
    "/chatgpt": (
        "chatgpt",
        "Режим: ChatGPT"
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
}


async def handle_command(user_text: str, sender: dict) -> str | None:
    """
    Обработка команд бота - устанавливает режим пользователя
    и возвращает текст ответа для информирования пользователя 
    или None, если команда не распознана.
    """
    if not user_text.startswith("/"):
        return None

    command = user_text.split(maxsplit=1)[0].lower()

    user_name = sender.get("name", "Неизвестный пользователь")
    user_id = int(sender.get("user_id"))
    user_data = await db.get_user(user_id)
    # если пользователь гость то разрещен только один режим (для бота ССТ)
    if user_data["permission"] == 1:
        command = "/gigachat"  

    if command == "/billing":
        if user_data:
            balance = user_data["coins"] + user_data["giftcoins"]
            return f"Уважаемый: {user_name}!\n" f"Ваш баланс: {balance} ₽"
        return f"Пользователь: {user_name} в списках не значится)"
    
    if command == "/mode":
        return get_user_mode(user_id)
    
    if command in mode_map:
        mode, reply = mode_map[command]
        set_user_mode(user_id, mode)
        # Очищаем состояние подтверждения удаления
        clear_user_pending_delete(user_id)
        
        # Для /mentor — очищаем состояние ментора и парсим новую команду
        if command == "/mentor":
            # Очищаем старое состояние ментора для новой сессии
            clear_mentor_state(user_id)
            
            after_command = user_text[len(command):].strip()
            if after_command:
                # Текст после /mentor — пусть handle_message его обработает
                return None
        
        return reply
    
    return "Вы ввели неправильную команду"


async def handle_message(
        request: Request, user_text: str, sender: dict
) -> str | None:
    """Обработка сообщений пользователя."""
    user_id = int(sender.get("user_id"))
    user_mode = get_user_mode(user_id)
    if not user_mode:
        user_mode = "gigachat"
        set_user_mode(user_id, user_mode)
    
    logger.info(
        f"handle_message: user_id={user_id}, mode={user_mode}"
    )
    
    match user_mode:
            case "gigachat":
                lc_llm = request.app.state.giga_lc_client
                answer = await ask_rag(
                    user_text=user_text, lc_llm=lc_llm,
                )
                await db.add_billing(user_id, user_mode, user_text, 0, 2)
                return answer
                
            case "gigachatpro":
                # проверяем есть ли файл для (анализа) включения в контекст
                extracted_text = _get_file_extracted_text(user_id)
                # получаем полный промпт с текстом файла, историей
                # и контролем токенов
                user_prompt = await full_prompt(
                    user_id, user_text, extracted_text
                )
                answer = await request.app.state.giga_client.chat(
                    messages=user_prompt,
                    model="GigaChat",
                )
                await db.add_billing(user_id, user_mode, user_text, 0, 5)
                # Если пользователь запросил конкретный формат
                # — создаём и отправляем файл
                formatted = await _check_and_send_formatted(
                    user_text, user_id, answer
                )
                return formatted if formatted is not None else answer

            case "chatgpt":
                if not hasattr(request.app.state, "openai_client"):
                    return "OpenAI клиент не настроен."
                client = request.app.state.openai_client
                # проверяем есть ли файл для включения в контекст
                extracted_text = _get_file_extracted_text(user_id)
                # получаем полный промпт с текстом файла, историей
                # и контролем токенов
                user_prompt = await full_prompt(
                    user_id, user_text, extracted_text
                )
                answer = await client.chat(
                    messages=user_prompt,
                    model="gpt-5.2-chat-latest",
                )
                await db.add_billing(user_id, user_mode, user_text, 0, 5)
                # Если пользователь запросил конкретный формат
                # — создаём и отправляем файл
                formatted = await _check_and_send_formatted(
                    user_text, user_id, answer
                )
                return formatted if formatted is not None else answer

            case "gemini":
                if not hasattr(request.app.state, "gemini_client"):
                    return "Gemini клиент не настроен."
                client = request.app.state.gemini_client
                # проверяем есть ли файл для включения в контекст
                extracted_text = _get_file_extracted_text(user_id)
                # получаем полный промпт с текстом файла, историей
                # и контролем токенов
                user_prompt = await full_prompt(
                    user_id, user_text, extracted_text
                )
                answer = await client.chat(
                    messages=user_prompt,
                    model="gemini-2.5-pro",
                )
                await db.add_billing(user_id, user_mode, user_text, 0, 5)
                # Если пользователь запросил конкретный формат
                # — создаём и отправляем файл
                formatted = await _check_and_send_formatted(
                    user_text, user_id, answer
                )
                return formatted if formatted is not None else answer
            case "mentor":
                return await handle_mentor_mode(
                    request, user_text, user_id, user_mode
                )
            case "edit":
                return await _handle_edit_mode(user_text, sender)
            case "rag":
                user_text = user_text.strip()
                user_id = int(sender.get("user_id"))

                # Проверка состояния чтоесть файл на удаление
                pending = get_user_pending_delete(user_id)
                # если ожидаем удаление файла
                # то спрашиваем подтверждение удаления
                if pending is not None:
                    confirmations = {
                        "1", "да", "yes", "ok"
                    }
                    if user_text.lower() in confirmations:
                        file_to_del = get_user_pending_delete(user_id)
                        clear_user_pending_delete(user_id)
                        await db.add_billing(
                            user_id, user_mode, user_text, 0, 1
                        )
                        return await asyncio.to_thread(
                            delete_file_from_vector_db, file_to_del
                        )
                    else:
                        clear_user_pending_delete(user_id)
                        return "Удаление отменено."
                # выводим список документов в базе, если пользователь набрал ls
                if user_text.lower() == "ls":
                    docs_list = get_all_filenames_from_vector_db()
                    await db.add_billing(user_id, user_mode, user_text, 0, 1)
                    return docs_list
                
                # поиск файла по имени для возможного удаления
                # если пользователь что то набрал,
                # что бы он ни набрал считаем что это часть имени файла
                # из векторной базы и пытаемся найти файл
                result = get_all_filenames_from_vector_db(
                    search_text=user_text
                )
                if result and not result.startswith("Файл с таким"):
                    # Файл найден, запрашиваем подтверждение
                    set_user_pending_delete(user_id, result)
                    await db.add_billing(user_id, user_mode, user_text, 0, 1)
                    return (
                        f"Найден файл: {result}\n"
                        "Удалить? (Введите 1 / да / yes / ok)" "\n"
                        "Для отмены введите 0 / нет / no / или любой символ) "
                    )
                return result
            
            case None:
                return "Используйте /gigachat для начала общения с ИИ."


async def handle_image(
        request: Request, image_path: str, sender: dict
) -> str | None:
    """Обработка изображений."""
    user_id = int(sender.get("user_id"))
    if get_user_mode(user_id) == "edit":
        return "Режим редактирования ещё не реализован."
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
            file_path=file_name, sender=sender, model_name="Embeddings"
        )
        await db.add_billing(
            user_id, user_mode, "save_to_vector_db", 0, 5, notes=result
        )
        return result
    # если любой другой режим то на данном этапе файл уже сохранен 
    # во временной папке, фиксируем путь в 
    # кроме режима редактирования изображений
    else:
        # извлекаем содержимое файла
        extracted_text = await extract_text_from_file_utils.process_uploaded_file(
                file_name
            )
        # сохраняем содержимое файла
        set_user_file_data(user_id, {"extracted_text": extracted_text})

        return "Файл получен"


def _get_file_extracted_text(user_id: int) -> str:
    """
    Возвращает текст, извлечённый из файла, загруженного пользователем.
    Если файл не загружен — возвращает пустую строку.
    """
    file_data = get_user_file_data(user_id)
    if file_data and "extracted_text" in file_data:
        return file_data["extracted_text"]
    return ""


async def _check_and_send_formatted(
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


async def _handle_edit_mode(user_text: str, sender: dict) -> str:
    """
    Обработка режима редактирования промптов.

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
    user_id = int(sender.get("user_id"))
    user_data = await db.get_user(user_id)

    if user_data is None or user_data.get("permission") != 0:
        return "⛔ Редактирование промптов доступно только администраторам."

    edit_state = get_prompt_edit_state(user_id)
    user_text = user_text.strip()

    if edit_state is None:
        return await _edit_mode_idle(user_text, user_id)

    stage = edit_state.get("stage")

    if stage == "idle":
        return await _edit_mode_idle(user_text, user_id)

    if stage == "list":
        return await _edit_mode_list(user_text, user_id, edit_state)

    if stage == "view":
        result = await _edit_mode_view(user_text, user_id, edit_state)
        # Перезапрашиваем состояние для обработки результата
        new_state = get_prompt_edit_state(user_id)
        if new_state:
            edit_state = new_state
            stage = new_state.get("stage", "")
        return result

    if stage == "edit_system":
        return await _edit_mode_edit_system(user_text, user_id, edit_state)

    if stage == "edit_human":
        return await _edit_mode_edit_human(user_text, user_id, edit_state)

    if stage == "confirm":
        return await _edit_mode_confirm(user_text, user_id, edit_state)

    return "Произошла ошибка. Напишите 'отмена' для сброса."


