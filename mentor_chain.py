"""
Модуль RAG-цепочки для режима Mentor (проверка знаний).
Включает цепочки для генерации вопросов и оценки ответов студентов.
Поддерживает фильтрацию по конкретному документу через параметр document_name.
"""

import gc
import random
import time
from typing import Optional

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langchain_core.language_models import BaseChatModel

from rag_embeddings import get_giga_embeddings
from global_state import GUEST_RAG_DIR
from utils import logger


# ── Промпт для генерации вопроса ─────────────────────────────────────────────
_QUESTION_SYSTEM_PROMPT = """Ты — строгий преподаватель колледжа, который проверяет знания студента.

Контекст из документов колледжа:
{context}

Задание:
1. На основе КОНТЕКСТА сформулируй ОДИН проверочный вопрос.
2. Вопрос должен проверять понимание ключевого материала.
3. Вопрос должен иметь КОНКРЕТНЫЙ ответ (факт, определение, число, название).
4. Не задавай вопросы типа "объясните", "опишите" — только фактические вопросы.
5. НЕ добавляй префикс "Вопрос:" — просто напиши сам вопрос.

Формат ответа: ТОЛЬКО сам вопрос, без лишних слов."""

_QUESTION_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _QUESTION_SYSTEM_PROMPT),
    ("human", "Сформулируй проверочный вопрос по теме: {topic}"),
])


# ── Промпт для оценки ответа студента ─────────────────────────────────────────
_EVALUATION_SYSTEM_PROMPT = """Ты — строгий преподаватель, который проверяет знания студента.

Материал из документов (эталон):
{context}

Вопрос, на который отвечал студент:
{question}

Ответ студента:
{answer}

ВНИМАНИЕ: Будь КРАЙНЕ строг при оценке. Оценивай буквально.

"ПРАВИЛЬНО" — только если:
- Ответ ТОЧНО совпадает с эталоном
- Числа, названия, буквенные коды идентичны эталону
- Нет ни одной ошибки

"ЧАСТИЧНО" — если:
- Ответ содержит верную идею, но неполон
- Упущены важные детали эталонного ответа

"НЕПРАВИЛЬНО" — если:
- Названия отличаются хотя бы одним символом/буквой
- Числа не совпадают
- Упомянуты неверные данные (не те авторы, не тот год, не тот формат)
- Ответ не раскрывает суть вопроса

Формат (ТОЛЬКО эти две строки):
ОЦЕНКА: ПРАВИЛЬНО или ЧАСТИЧНО или НЕПРАВИЛЬНО
ОБРАТНАЯ СВЯЗЬ: Одно предложение"""

_EVALUATION_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _EVALUATION_SYSTEM_PROMPT),
    ("human", ""),
])


# ── Помощник для форматирования контекста ─────────────────────────────────────
def _format_context(docs: list) -> str:
    """
    Формирует строку контекста из найденных документов.
    Используется для обоих типов цепочек.
    """
    if not docs:
        return "Документы по теме не найдены."
    
    parts = []
    for doc in docs:
        source = doc.metadata.get("source", "неизвестный документ")
        parts.append(
            f"Документ: {source}\n"
            f"{doc.page_content.strip()}"
        )
    return "\n\n".join(parts)


# ── Синглтоны цепочек с отслеживанием параметров ──────────────────────────────
_question_chain = None
_question_chain_llm = None
_evaluation_chain = None
_evaluation_chain_llm = None


def _get_vector_db():
    """Получает инстанс ChromaDB через load_from_file."""
    from load_from_file import check_vector_db
    
    embeddings = get_giga_embeddings(model_name="Embeddings")
    vector_db = check_vector_db(persist_dir=GUEST_RAG_DIR, embeddings=embeddings)
    return vector_db


def _search_documents(
    query: str,
    document_name: str | None = None,
    k: int = 3,
    seed: int | None = None,
) -> list:
    """
    Поиск документов в ChromaDB с опциональной фильтрацией по имени документа.
    Возвращает разные фрагменты при каждом вызове (для разнообразия вопросов).
    
    Args:
        query: Поисковый запрос (тема)
        document_name: Имя документа для фильтрации (частичное совпадение)
        k: Количество результатов
        seed: Seed для random (для воспроизводимости при отладке)
    
    Returns:
        Список найденных документов
    """
    vector_db = _get_vector_db()
    
    try:
        # Запрашиваем больше документов для разнообразия
        fetch_k = k * 4
        
        if document_name:
            docs = vector_db.similarity_search(
                query=query,
                k=fetch_k,
                filter={"filename": document_name},
            )
            logger.info(f"Mentor: поиск '{query}' в документе '{document_name}', найдено {len(docs)}")
        else:
            docs = vector_db.similarity_search(query=query, k=fetch_k)
            logger.info(f"Mentor: поиск '{query}' по всем документам, найдено {len(docs)}")
        
        # Перемешиваем для разнообразия вопросов
        if seed is not None:
            random.seed(seed)
        random.shuffle(docs)
        
        return docs[:k]
    except Exception as e:
        logger.error(f"Mentor: ошибка поиска в ChromaDB: {e}", exc_info=True)
        return []


def _get_question_chain(lc_llm: BaseChatModel):
    """
    Возвращает синглтон цепочки генерации вопроса.
    Пересоздаёт цепочку если lc_llm изменился.
    """
    global _question_chain, _question_chain_llm
    
    # Пересоздаём если lc_llm изменился
    if _question_chain is None or _question_chain_llm is not id(lc_llm):
        _question_chain = (
            {
                "context": RunnablePassthrough(),
                "topic": RunnablePassthrough(),
            }
            | _QUESTION_PROMPT
            | lc_llm
            | StrOutputParser()
        )
        _question_chain_llm = id(lc_llm)
        logger.info(f"Mentor: создана новая цепочка вопросов (lc_llm id={id(lc_llm)})")
    
    return _question_chain


def _get_evaluation_chain(lc_llm: BaseChatModel):
    """
    Возвращает синглтон цепочки оценки ответа.
    Пересоздаёт цепочку если lc_llm изменился.
    """
    global _evaluation_chain, _evaluation_chain_llm
    
    if _evaluation_chain is None or _evaluation_chain_llm is not id(lc_llm):
        _evaluation_chain = (
            {
                "context": RunnablePassthrough(),
                "question": RunnablePassthrough(),
                "answer": RunnablePassthrough(),
            }
            | _EVALUATION_PROMPT
            | lc_llm
            | StrOutputParser()
        )
        _evaluation_chain_llm = id(lc_llm)
        logger.info(f"Mentor: создана новая цепочка оценки (lc_llm id={id(lc_llm)})")
    
    return _evaluation_chain


# ── Публичные функции ─────────────────────────────────────────────────────────

async def generate_question(
    topic: str,
    lc_llm: BaseChatModel,
    user_id: int,
    document_name: str | None = None,
    question_number: int = 1,
) -> dict:
    """
    Генерирует проверочный вопрос по заданной теме.
    
    Args:
        topic: Тема для проверки
        lc_llm: LangChain-совместимая модель
        user_id: ID пользователя (для логирования)
        document_name: Имя конкретного документа для поиска (опционально)
        question_number: Номер вопроса (для разнообразия через seed)
    
    Returns:
        dict с ключами:
        - success: bool
        - question: str - сгенерированный вопрос
        - context: str - контекст из документов
        - error: str - сообщение об ошибке (если есть)
    """
    try:
        # Используем time + user_id как seed для случайности при первом вопросе
        current_time = int(time.time() * 1000) % 1000000
        seed = (current_time + user_id) % 1000000
        
        docs = _search_documents(
            query=topic,
            document_name=document_name,
            k=3,
            seed=seed,
        )
        
        if not docs:
            return {
                "success": False,
                "question": "",
                "context": "",
                "error": f"Не найдены документы по теме '{topic}'"
                + (f" в документе '{document_name}'" if document_name else ""),
            }
        
        context = _format_context(docs)
        
        # Теперь генерируем вопрос с полученным контекстом
        chain = _get_question_chain(lc_llm)
        result: str = await chain.ainvoke({
            "topic": topic,
            "context": context,
        })
        
        # Обработка результата - сохраняем как есть
        question = result.strip()
        
        logger.info(
            f"Mentor: сгенерирован вопрос для user={user_id}, "
            f"тема='{topic}', документ='{document_name}', "
            f"вопрос_номер={question_number}, фрагментов={len(docs)}"
        )
        
        gc.collect()
        
        return {
            "success": True,
            "question": question,
            "context": context,
        }
        
    except Exception as e:
        logger.error(f"Mentor: ошибка генерации вопроса: {e}", exc_info=True)
        return {
            "success": False,
            "question": "",
            "context": "",
            "error": "Не удалось сгенерировать вопрос. Попробуйте другую тему.",
        }


async def evaluate_answer(
    question: str,
    student_answer: str,
    context: str,
    lc_llm: BaseChatModel,
    user_id: int,
) -> dict:
    """
    Оценивает ответ студента на вопрос.
    
    Args:
        question: Вопрос ментора
        student_answer: Ответ студента
        context: Контекст из документов (уже подготовленный)
        lc_llm: LangChain-совместимая модель
        user_id: ID пользователя (для логирования)
    
    Returns:
        dict с ключами:
        - success: bool
        - evaluation: str - "ПРАВИЛЬНО" / "ЧАСТИЧНО" / "НЕПРАВИЛЬНО"
        - feedback: str - обратная связь
        - error: str - сообщение об ошибке (если есть)
    """
    # Список фраз, которые НЕ являются ответом
    not_an_answer = (
        "не знаю", "незнаю", "не понимаю", "затрудняюсь",
        "не могу ответить", "без понятия", "хз", "не в курсе",
        "unknown", "dont know", "don't know", "no idea", "idk"
    )
    
    answer_lower = student_answer.lower().strip()
    if answer_lower in not_an_answer or len(answer_lower) < 3:
        return {
            "success": True,
            "evaluation": "НЕПРАВИЛЬНО",
            "feedback": "К сожалению, это неправильный ответ. Попробуйте изучить материал ещё раз.",
        }
    
    try:
        chain = _get_evaluation_chain(lc_llm)
        
        result: str = await chain.ainvoke({
            "question": question,
            "answer": student_answer,
            "context": context,
        })
        
        # Парсим результат
        evaluation = "НЕПРАВИЛЬНО"
        feedback = result
        
        # Ищем ОЦЕНКА и ОБРАТНАЯ СВЯЗЬ в тексте
        for line in result.split("\n"):
            line_upper = line.upper()
            if "ОЦЕНКА" in line_upper and ":" in line:
                eval_part = line.split(":", 1)[1].strip().upper()
                if "ПРАВИЛЬНО" in eval_part and "ЧАСТИЧНО" not in eval_part:
                    evaluation = "ПРАВИЛЬНО"
                elif "ЧАСТИЧНО" in eval_part:
                    evaluation = "ЧАСТИЧНО"
                elif "НЕПРАВИЛЬНО" in eval_part:
                    evaluation = "НЕПРАВИЛЬНО"
            elif "ОБРАТНАЯ СВЯЗЬ" in line_upper or "ОБРАТНАЯ" in line_upper:
                if ":" in line:
                    feedback = line.split(":", 1)[1].strip()
                break
        
        if evaluation == "ПРАВИЛЬНО":
            feedback = "Верно! Ответ правильный."
        elif evaluation == "ЧАСТИЧНО":
            # Для частично правильных — показываем краткую версию без "Ответ студента..."
            sentences = feedback.split(".")
            if len(sentences) > 0:
                # Берём первое предложение с объяснением
                clean_feedback = sentences[0].strip()
                if len(clean_feedback) > 5:
                    feedback = clean_feedback + "."
                else:
                    feedback = "Ответ частично правильный, но требует уточнения."
        
        logger.info(
            f"Mentor: оценен ответ user={user_id}: {evaluation}"
        )
        
        gc.collect()
        
        return {
            "success": True,
            "evaluation": evaluation,
            "feedback": feedback,
        }
        
    except Exception as e:
        logger.error(f"Mentor: ошибка оценки ответа: {e}", exc_info=True)
        return {
            "success": False,
            "evaluation": "ОШИБКА",
            "feedback": "Произошла ошибка при проверке ответа.",
            "error": str(e),
        }


def check_document_exists(document_name: str) -> bool:
    """
    Проверяет, существует ли документ с указанным именем в базе.
    Использует частичное совпадение (case-insensitive).
    
    Args:
        document_name: Имя или часть имени документа
    
    Returns:
        True если документ найден, False иначе
    """
    from load_from_file import get_all_filenames_from_vector_db
    
    try:
        all_docs = get_all_filenames_from_vector_db()
        doc_lower = document_name.lower()
        
        for line in all_docs.split("\n"):
            if doc_lower in line.lower():
                return True
        return False
    except Exception as e:
        logger.error(f"Mentor: ошибка проверки документа: {e}")
        return False


def find_document_name(partial_name: str) -> str | None:
    """
    Находит полное имя документа по частичному совпадению.
    Ищет совпадение каждого слова из partial_name в именах документов.
    
    Args:
        partial_name: Частичное имя документа (может быть фразой)
    
    Returns:
        Полное имя документа или None если не найден
    """
    from load_from_file import get_all_filenames_from_vector_db
    
    try:
        all_docs = get_all_filenames_from_vector_db()
        search_words = partial_name.lower().split()
        
        # Собираем все кандидаты с их "score" совпадения
        candidates = []
        
        for line in all_docs.split("\n"):
            # Извлекаем имя документа из формата "1. имя" или "имя"
            doc_name = line.strip()
            if ". " in doc_name and doc_name.split(". ", 1)[0].isdigit():
                doc_name = doc_name.split(". ", 1)[1].strip()
            
            if not doc_name:
                continue
            
            doc_lower = doc_name.lower()
            
            # Считаем количество совпавших слов
            matched = sum(1 for word in search_words if word in doc_lower)
            
            if matched > 0:
                # Чем больше совпадений, тем лучше
                candidates.append((matched, doc_name))
        
        if candidates:
            # Сортируем по количеству совпадений (убывание)
            candidates.sort(key=lambda x: x[0], reverse=True)
            best_match = candidates[0][1]
            logger.info(f"Mentor: найден документ '{best_match}' для запроса '{partial_name}'")
            return best_match
        
        logger.warning(f"Mentor: документ не найден для запроса '{partial_name}'")
        return None
    except Exception as e:
        logger.error(f"Mentor: ошибка поиска документа: {e}")
        return None