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
    get_mentor_state,
    set_mentor_state,
    clear_mentor_state,
)
from utils import logger
from load_from_file import (
    save_to_vector_db,
    get_all_filenames_from_vector_db,
    delete_file_from_vector_db,
)

from prompt_builder import full_prompt
import extract_text_from_file_utils
from rag_chain import ask_rag  # ← единственный импорт для RAG
from mentor_chain import generate_question, evaluate_answer, find_document_name

mode_map = {
    "/gigachat": (
        "gigachat",
        "Режим: чат с ИИ по документам колледжа"
    ),
    "/gigachatpro": (
        "gigachatpro",
        "Режим: GigaChat Pro"
    ),
    "/mentor": (
        "mentor",
        "Режим: Проверка знаний по теме Сварка"
    ),
    "/edit": (
        "edit",
        "Режим: редактирование"
    ),
    "/rag": (
        "rag",
        "Режим настройки базы знаний (RAG)." "\n"
        "Загрузка документов в базу знаний - загрузите документ pdf" "\n"
        "Просмотр наименований документов в базе - наберите ls" "\n"
        "Удаление документа из базы знаний:" "\n"
        " для удаления документа отправьте его название" 
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
                extracted_text = ""
                file_data = get_user_file_data(user_id)
                if file_data and "extracted_text" in file_data:
                    extracted_text = file_data["extracted_text"]
                # получаем полный промпт с текстом файла  историей
                # и контролем токенов
                user_prompt = await full_prompt(user_id, user_text, extracted_text)
                #
                answer = await request.app.state.giga_client.chat(
                    messages=user_prompt,
                    model="GigaChat",
                )
                await db.add_billing(user_id, user_mode, user_text, 0, 5)
                return answer
            case "mentor":
                lc_llm = request.app.state.giga_lc_client
                mentor_state = get_mentor_state(user_id)
                
                # Парсинг формата "тема: XXX документ: YYY"
                topic = None
                document_name = None
                
                raw_text = user_text.strip()
                
                # Ищем "документ:" в тексте (захватываем всё после него до конца строки)
                import re
                doc_match = re.search(r'документ:\s*(.+)', raw_text, re.IGNORECASE | re.DOTALL)
                if doc_match:
                    doc_query = doc_match.group(1).strip()
                    document_name = find_document_name(doc_query)
                    if not document_name:
                        return f"Документ '{doc_query}' не найден в базе."
                    # Убираем часть с документом из текста
                    raw_text = re.sub(r'документ:\s*.+', '', raw_text, flags=re.IGNORECASE | re.DOTALL).strip()
                
                # Ищем "тема:" или берём всё как тему
                topic_match = re.search(r'тема:\s*(.+)', raw_text, re.IGNORECASE | re.DOTALL)
                if topic_match:
                    topic = topic_match.group(1).strip()
                elif raw_text:
                    topic = raw_text
                
                # Если состояния нет — начинаем новую сессию
                if mentor_state is None:
                    # Проверяем, есть ли что-то для начала сессии
                    has_params = document_name or (topic and topic not in ("спрашивай", "начали", "старт", "go"))
                    
                    if not topic and not document_name:
                        return (
                            "Введите тему для проверки знаний.\n\n"
                            "Формат:\n"
                            "• тема:сварка\n"
                            "• документ:устав\n"
                            "• тема:сварка документ:устав"
                        )
                    
                    # Генерируем первый вопрос (question_number=1)
                    result = await generate_question(
                        topic=topic if topic else "основы сварки",
                        lc_llm=lc_llm,
                        user_id=user_id,
                        document_name=document_name,
                        question_number=1,
                    )
                    
                    if result["success"]:
                        set_mentor_state(user_id, {
                            "stage": "question",
                            "topic": topic if topic else "основы сварки",
                            "document_name": document_name,
                            "question": result["question"],
                            "context": result["context"],
                            "question_count": 1,
                            "correct_count": 0,
                        })
                        await db.add_billing(user_id, user_mode, f"{topic} ({document_name or 'все документы'})", 0, 3)
                        
                        doc_info = f" (документ: {document_name})" if document_name else ""
                        return (
                            f"Проверяю знания{doc_info}\n\n"
                            f"Вопрос 1:\n{result['question']}\n\n"
                            "Напишите ваш ответ."
                        )
                    else:
                        return result.get("error", "Не удалось найти материалы по теме.")

                # Если есть активный вопрос — обрабатываем ответ студента
                if mentor_state.get("stage") == "question":
                    student_answer = user_text.strip()
                    
                    if not student_answer:
                        return "Пожалуйста, напишите ваш ответ."
                    
                    # Оцениваем ответ
                    result = await evaluate_answer(
                        question=mentor_state["question"],
                        student_answer=student_answer,
                        context=mentor_state["context"],
                        lc_llm=lc_llm,
                        user_id=user_id,
                    )
                    
                    if not result["success"]:
                        return result.get("error", "Ошибка проверки ответа.")
                    
                    # Обновляем статистику
                    question_count = mentor_state.get("question_count", 0) + 1
                    correct_count = mentor_state.get("correct_count", 0)
                    if result["evaluation"] == "ПРАВИЛЬНО":
                        correct_count += 1
                    
                    # Формируем ответ с обратной связью
                    eval_emoji = {
                        "ПРАВИЛЬНО": "✅",
                        "ЧАСТИЧНО": "⚠️",
                        "НЕПРАВИЛЬНО": "❌",
                    }.get(result["evaluation"], "❓")
                    
                    response = (
                        f"{eval_emoji} {result['evaluation']}\n\n"
                        f"{result['feedback']}\n\n"
                        f"Счёт: {correct_count}/{question_count} правильных ответов"
                    )
                    
                    # Спрашиваем, продолжать ли
                    response += (
                        "\n\nПродолжить проверку?\n"
                        "• Напишите 'ещё' для следующего вопроса\n"
                        "• Напишите 'хватит' для завершения"
                    )
                    
                    # Сохраняем состояние с ожиданием решения
                    set_mentor_state(user_id, {
                        **mentor_state,
                        "stage": "feedback",
                        "question_count": question_count,
                        "correct_count": correct_count,
                        "last_result": result,
                    })
                    
                    await db.add_billing(user_id, user_mode, "оценка ответа", 0, 3)
                    return response
                
# Этап обратной связи — пользователь решает продолжать или нет
                if mentor_state.get("stage") == "feedback":
                    user_decision = user_text.strip().lower()
                    
                    if user_decision in ("ещё", "да", "yes", "продолжить", "еще", "+"):
                        # Генерируем следующий вопрос с увеличенным номером
                        topic = mentor_state.get("topic", "")
                        document_name = mentor_state.get("document_name")
                        next_question_num = mentor_state.get("question_count", 0) + 1
                        
                        result = await generate_question(
                            topic=topic,
                            lc_llm=lc_llm,
                            user_id=user_id,
                            document_name=document_name,
                            question_number=next_question_num,
                        )
                        
                        if result["success"]:
                            set_mentor_state(user_id, {
                                **mentor_state,
                                "stage": "question",
                                "question": result["question"],
                                "context": result["context"],
                            })
                            doc_info = f" ({mentor_state.get('document_name', '')})" if mentor_state.get("document_name") else ""
                            return f"Вопрос {next_question_num}:\n\n{result['question']}"
                        else:
                            return result.get("error", "Не удалось сгенерировать следующий вопрос.")
                    
                    elif user_decision in ("хватит", "нет", "no", "стоп", "-", "выход"):
                        # Завершаем сессию
                        question_count = mentor_state.get("question_count", 0)
                        correct_count = mentor_state.get("correct_count", 0)
                        
                        percentage = (correct_count / question_count * 100) if question_count > 0 else 0
                        
                        if percentage >= 80:
                            grade = "Отлично! 🎉"
                        elif percentage >= 60:
                            grade = "Хорошо! 👍"
                        elif percentage >= 40:
                            grade = "Нужно подучить 📚"
                        else:
                            grade = "Рекомендую повторить материал"
                        
                        clear_mentor_state(user_id)
                        
                        summary = (
                            f"Проверка завершена!\n\n"
                            f"Тема: {mentor_state.get('topic', '')}\n"
                            f"Всего вопросов: {question_count}\n"
                            f"Правильных ответов: {correct_count}\n"
                            f"Результат: {percentage:.0f}%\n\n"
                            f"{grade}\n\n"
                            "Для новой проверки введите /mentor и тему."
                        )
                        await db.add_billing(user_id, user_mode, "завершение", 0, 3)
                        return summary
                    
                    else:
                        return (
                            "Не понял. Напишите:\n"
                            "• 'ещё' — следующий вопрос\n"
                            "• 'хватит' — завершить проверку"
                        )
            case "edit":
                return "Режим редактирования ещё не реализован."
            case "rag":
                user_text = user_text.strip()
                user_id = int(sender.get("user_id"))

                # Проверка состояния чтоесть файл на удаление
                pending = get_user_pending_delete(user_id)
                # если ожидаем удаление файлв то спрашиваем подтверждение удаления
                if pending is not None:
                    confirmations = {
                        "1", "да", "yes", "ok"
                    }
                    if user_text.lower() in confirmations:
                        file_to_del = get_user_pending_delete(user_id)
                        clear_user_pending_delete(user_id)
                        await db.add_billing(user_id, user_mode, user_text, 0, 1)
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
                result = get_all_filenames_from_vector_db(search_text=user_text)
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


