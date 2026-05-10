"""
Модуль RAG-цепочки для режима Mentor (проверка знаний).
Включает цепочки для генерации вопросов и оценки ответов студентов.
Поддерживает фильтрацию по конкретному документу через параметр document_name.
"""

import gc
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

Формат ответа (ТОЛЬКО вопрос, ничего кроме вопроса):
Вопрос: <ваш вопрос>"""

_QUESTION_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _QUESTION_SYSTEM_PROMPT),
    ("human", "Тема для проверки: {topic}"),
])


# ── Промпт для оценки ответа студента ─────────────────────────────────────────
_EVALUATION_SYSTEM_PROMPT = """Ты — опытный преподаватель, который оценивает ответы студента.

Материал из документов (эталон):
{context}

Вопрос, на который отвечал студент:
{question}

Ответ студента:
{answer}

Задание:
1. Определи, правильный ли ответ студента.
2. Оцени по шкале:
   - "ПРАВИЛЬНО" — ответ полностью соответствует эталону
   - "ЧАСТИЧНО" — ответ содержит верную идею, но неполный или неточный
   - "НЕПРАВИЛЬНО" — ответ неверный или отсутствует

3. Дай краткую обратную связь (2-4 предложения):
   - Похвали, если верно
   - Объясни ошибку, если неверно
   - Укажи верный ответ

Формат ответа:
ОЦЕНКА: <ПРАВИЛЬНО/ЧАСТИЧНО/НЕПРАВИЛЬНО>
ОБРАТНАЯ СВЯЗЬ: <ваш комментарий>"""

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


# ── Вспомогательные функции для работы с ChromaDB ──────────────────────────────
def _get_vector_db():
    """Получает инстанс ChromaDB."""
    from load_from_file import check_vector_db
    
    embeddings = get_giga_embeddings(model_name="Embeddings")
    vector_db = check_vector_db(persist_dir=GUEST_RAG_DIR, embeddings=embeddings)
    return vector_db


def _search_documents(
    query: str,
    document_name: str | None = None,
    k: int = 3,
) -> list:
    """
    Поиск документов в ChromaDB с опциональной фильтрацией по имени документа.
    
    Args:
        query: Поисковый запрос (тема)
        document_name: Имя документа для фильтрации (частичное совпадение)
        k: Количество результатов
    
    Returns:
        Список найденных документов
    """
    vector_db = _get_vector_db()
    
    if document_name:
        # Фильтрация по конкретному документу через where
        # ChromaDB поддерживает точное совпадение в where
        try:
            docs = vector_db.similarity_search(
                query=query,
                k=k * 2,  # Запрашиваем больше, т.к. фильтр может отфильтровать
                filter={"filename": document_name},
            )
            logger.info(f"Mentor: поиск '{query}' в документе '{document_name}', найдено {len(docs)}")
            return docs[:k]
        except Exception as e:
            logger.warning(f"Mentor: фильтр по '{document_name}' не сработал: {e}")
            # Fallback — ищем без фильтра
            docs = vector_db.similarity_search(query=query, k=k)
            return docs
    else:
        # Поиск по всем документам
        docs = vector_db.similarity_search(query=query, k=k)
        logger.info(f"Mentor: поиск '{query}' по всем документам, найдено {len(docs)}")
        return docs


# ── Синглтоны цепочек (без фильтра — для общего контекста) ─────────────────────
_question_chain = None
_evaluation_chain = None


def _get_question_chain(lc_llm: BaseChatModel):
    """Возвращает синглтон цепочки генерации вопроса."""
    global _question_chain
    
    if _question_chain is None:
        vector_db = _get_vector_db()
        
        retriever = vector_db.as_retriever(
            search_type="mmr",
            search_kwargs={"k": 3, "fetch_k": 10, "lambda_mult": 0.8}
        )
        
        _question_chain = (
            {
                "context": retriever | RunnableLambda(_format_context),
                "topic": RunnablePassthrough(),
            }
            | _QUESTION_PROMPT
            | lc_llm
            | StrOutputParser()
        )
    
    return _question_chain


def _get_evaluation_chain(lc_llm: BaseChatModel):
    """Возвращает синглтон цепочки оценки ответа."""
    global _evaluation_chain
    
    if _evaluation_chain is None:
        vector_db = _get_vector_db()
        
        retriever = vector_db.as_retriever(
            search_type="mmr",
            search_kwargs={"k": 3, "fetch_k": 10, "lambda_mult": 0.8}
        )
        
        _evaluation_chain = (
            {
                "context": retriever | RunnableLambda(_format_context),
                "question": RunnablePassthrough(),
                "answer": RunnablePassthrough(),
            }
            | _EVALUATION_PROMPT
            | lc_llm
            | StrOutputParser()
        )
    
    return _evaluation_chain


# ── Публичные функции ─────────────────────────────────────────────────────────

async def generate_question(
    topic: str,
    lc_llm: BaseChatModel,
    user_id: int,
    document_name: str | None = None,
) -> dict:
    """
    Генерирует проверочный вопрос по заданной теме.
    
    Args:
        topic: Тема для проверки
        lc_llm: LangChain-совместимая модель
        user_id: ID пользователя (для логирования)
        document_name: Имя конкретного документа для поиска (опционально)
    
    Returns:
        dict с ключами:
        - success: bool
        - question: str - сгенерированный вопрос
        - context: str - контекст из документов
        - error: str - сообщение об ошибке (если есть)
    """
    try:
        chain = _get_question_chain(lc_llm)
        
        result: str = await chain.ainvoke(topic)
        
        # Извлекаем вопрос из результата
        question = result.strip()
        if question.startswith("Вопрос:"):
            question = question[8:].strip()
        
        logger.info(
            f"Mentor: сгенерирован вопрос для user={user_id}, "
            f"тема='{topic}', документ='{document_name}'"
        )
        
        # Получаем контекст для оценки (с фильтром по документу если указан)
        docs = _search_documents(
            query=topic,
            document_name=document_name,
            k=3,
        )
        context = _format_context(docs)
        
        if not docs:
            return {
                "success": False,
                "question": "",
                "context": "",
                "error": f"Не найдены документы по теме '{topic}'"
                + (f" в документе '{document_name}'" if document_name else ""),
            }
        
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
    try:
        chain = _get_evaluation_chain(lc_llm)
        
        result: str = await chain.ainvoke({
            "question": question,
            "answer": student_answer,
        })
        
        # Парсим результат
        evaluation = "НЕПРАВИЛЬНО"
        feedback = result
        
        for line in result.split("\n"):
            if line.startswith("ОЦЕНКА:"):
                eval_part = line[8:].strip().upper()
                if "ПРАВИЛЬНО" in eval_part:
                    evaluation = "ПРАВИЛЬНО"
                elif "ЧАСТИЧНО" in eval_part:
                    evaluation = "ЧАСТИЧНО"
                elif "НЕПРАВИЛЬНО" in eval_part:
                    evaluation = "НЕПРАВИЛЬНО"
            elif line.startswith("ОБРАТНАЯ СВЯЗЬ:"):
                feedback = line[17:].strip()
        
        logger.info(
            f"Mentor: оценен ответ user={user_id}: {evaluation}"
        )
        
        gc.collect()
        
        return {
            "success": True,
            "evaluation": evaluation,
            "feedback": feedback if feedback else result,
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
    
    Args:
        partial_name: Частичное имя документа
    
    Returns:
        Полное имя документа или None если не найден
    """
    from load_from_file import get_all_filenames_from_vector_db
    
    try:
        all_docs = get_all_filenames_from_vector_db()
        search_lower = partial_name.lower()
        
        for line in all_docs.split("\n"):
            if search_lower in line.lower():
                # Извлекаем имя из формата "1. имя"
                parts = line.split(". ", 1)
                if len(parts) > 1:
                    return parts[1].strip()
        return None
    except Exception as e:
        logger.error(f"Mentor: ошибка поиска документа: {e}")
        return None