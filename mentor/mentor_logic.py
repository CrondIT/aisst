"""Модуль обработки режима Mentor (проверка знаний)."""
import re

from fastapi import Request

import db
from global_state import get_mentor_state, set_mentor_state, clear_mentor_state
from mentor.mentor_chain import generate_question, evaluate_answer, find_document_name


async def handle_mentor_mode(
    request: Request,
    user_text: str,
    user_id: int,
    user_mode: str,
) -> str | None:
    """Обработка режима Mentor (проверка знаний)."""
    lc_llm = request.app.state.giga_lc_client
    mentor_state = get_mentor_state(user_id)

    # Парсинг формата "тема: XXX документ: YYY"
    topic = None
    document_name = None

    raw_text = user_text.strip()

    # Ищем "документ:" в тексте (захватываем всё после него до конца строки)
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
        )

        if result.get("correct_answer"):
            response += f"<b>Правильный ответ:</b> {result['correct_answer']}\n\n"

        response += f"Счёт: {correct_count}/{question_count} правильных ответов"

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
