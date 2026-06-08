"""
Модуль RAG-цепочки для режима Mentor (проверка знаний).
Включает цепочки для генерации вопросов и оценки ответов студентов.
Поддерживает фильтрацию по конкретному документу через параметр document_name.
Промпты загружаются из базы данных.
"""

import gc
import random
import time
from typing import Optional

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langchain_core.language_models import BaseChatModel

from rag_chain import get_giga_embeddings
from global_state import GUEST_RAG_DIR
from utils import logger
from prompt_repository import PromptRepository


# ── Кэш промптов (обновляется при изменении) ──────────────────────────────────
_question_prompt: Optional[ChatPromptTemplate] = None
_evaluation_prompt: Optional[ChatPromptTemplate] = None
_prompts_loaded = False


async def _load_mentor_prompts() -> tuple[ChatPromptTemplate, ChatPromptTemplate]:
    """
    Загружает промпты ментора из базы данных.
    Кэширует результат для повторных вызовов.
    """
    global _question_prompt, _evaluation_prompt, _prompts_loaded

    if _prompts_loaded and _question_prompt and _evaluation_prompt:
        return _question_prompt, _evaluation_prompt

    question_template = await PromptRepository.get_prompt_template("mentor_question")

    if question_template:
        _question_prompt = question_template
    else:
        from db import DEFAULT_PROMPTS
        question_data = DEFAULT_PROMPTS.get("mentor_question", {})
        _question_prompt = ChatPromptTemplate.from_messages([
            ("system", question_data.get("system", "")),
            ("human", question_data.get("human", "")),
        ])

    # Всегда используем строгий хардкодированный промпт для оценки
    # Это гарантирует корректную работу независимо от содержимого БД
    _evaluation_prompt = _EVALUATION_PROMPT

    _prompts_loaded = True
    logger.info("Mentor промпты загружены (вопрос из БД, оценка - строгий хардкод)")
    return _question_prompt, _evaluation_prompt


def invalidate_prompt_cache():
    """Сбрасывает кэш промптов. Вызывать после обновления промпта."""
    global _prompts_loaded, _question_prompt, _evaluation_prompt
    _prompts_loaded = False
    _question_prompt = None
    _evaluation_prompt = None
    logger.info("Кэш промптов сброшен")


# ── Промпт для оценки ответа студента (МАКСИМАЛЬНО СТРОГИЙ) ────────────────────
_EVALUATION_SYSTEM_PROMPT = """Ты — автоматическая система проверки знаний. НЕ будь вежливым. Оценивай строго.

КОНТЕКСТ:
{context}

ВОПРОС: {question}

ОТВЕТ СТУДЕНТА: {answer}

Проверь ответ по КОНТЕКСТУ. Ответ должен содержать ФАКТЫ из контекста.

Примеры НЕПРАВИЛЬНО:
- Вопрос про газы → ответ "сварные трубы" = НЕПРАВИЛЬНО (нет газов в ответе)
- Вопрос про положение шва → ответ "матрешка упала" = НЕПРАВИЛЬНО (бессмыслица)
- Любой ответ без фактов из контекста = НЕПРАВИЛЬНО

ВЫХОДНЫЕ ДАННЫЕ (строго 3 строки, без объяснений):
ОЦЕНКА: ПРАВИЛЬНО | ЧАСТИЧНО | НЕПРАВИЛЬНО
ОБРАТНАЯ СВЯЗЬ: краткое пояснение
ПРАВИЛЬНЫЙ ОТВЕТ: конкретный ответ из контекста"""

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
    from rag_chain import check_vector_db
    
    embeddings = get_giga_embeddings()
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


def _get_question_chain(lc_llm: BaseChatModel, question_prompt: ChatPromptTemplate):
    """
    Возвращает синглтон цепочки генерации вопроса.
    """
    global _question_chain, _question_chain_llm

    if _question_chain is None or _question_chain_llm is not id(lc_llm):
        _question_chain = (
            {
                "context": RunnablePassthrough(),
                "topic": RunnablePassthrough(),
            }
            | question_prompt
            | lc_llm
            | StrOutputParser()
        )
        _question_chain_llm = id(lc_llm)
        logger.info(f"Mentor: создана новая цепочка вопросов (lc_llm id={id(lc_llm)})")

    return _question_chain


def _get_evaluation_chain(lc_llm: BaseChatModel, evaluation_prompt: ChatPromptTemplate):
    """
    Возвращает синглтон цепочки оценки ответа.
    """
    global _evaluation_chain, _evaluation_chain_llm

    if _evaluation_chain is None or _evaluation_chain_llm is not id(lc_llm) or _evaluation_chain_llm is None:
        _evaluation_chain = (
            {
                "context": RunnablePassthrough(),
                "question": RunnablePassthrough(),
                "answer": RunnablePassthrough(),
            }
            | evaluation_prompt
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
    """
    try:
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

        question_prompt, _ = await _load_mentor_prompts()
        chain = _get_question_chain(lc_llm, question_prompt)
        result: str = await chain.ainvoke({
            "topic": topic,
            "context": context,
        })

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
    """
    not_an_answer = (
        "не знаю", "незнаю", "не понимаю", "затрудняюсь",
        "не могу ответить", "без понятия", "хз", "не в курсе",
        "unknown", "dont know", "don't know", "no idea", "idk"
    )

    answer_lower = student_answer.lower().strip()
    
    # Проверка на бессмысленные ответы
    nonsense_patterns = (
        "не знаю", "хз", "затрудняюсь", "без понятия",
        "абырвалг", "тентакль", "пвапвап", "фывап", "йцукен",
        "asdf", "qwer", "zxcv", "йцук", "мсмит", "test", "testtest",
        "биполярка", "просто", "надоела", "матрешка", "упала",
        "ерунда", "чушь", "фигня", "ерундовый", "ерундовые",
    )
    
    # Проверка на случайный набор символов
    random_chars = set("йцукенгшщзхъфывапролджэёasdfghjklzxcvbnm")
    unique_chars_in_answer = set(c for c in answer_lower if c in random_chars or c == " ")
    is_random = len(unique_chars_in_answer) <= 3 and len(answer_lower) > 5
    
    is_nonsense = (
        len(answer_lower) < 5 or
        answer_lower in not_an_answer or
        answer_lower in nonsense_patterns or
        is_random or
        any(word in answer_lower for word in nonsense_patterns if len(word) > 4)
    )
    
    if is_nonsense:
        logger.info(f"Mentor: ответ признан бессмысленным: '{student_answer}'")
        return {
            "success": True,
            "evaluation": "НЕПРАВИЛЬНО",
            "feedback": "Это не ответ на вопрос. Попробуйте изучить материал ещё раз.",
        }

    # Принудительно сбрасываем кэш цепочки каждый раз
    global _evaluation_chain, _evaluation_chain_llm
    _evaluation_chain = None
    _evaluation_chain_llm = None

    try:
        _, evaluation_prompt = await _load_mentor_prompts()
        chain = _get_evaluation_chain(lc_llm, evaluation_prompt)

        logger.info(f"Mentor: отправка на оценку - вопрос='{question[:50]}...', ответ='{student_answer[:50]}...'")

        result: str = await chain.ainvoke({
            "question": question,
            "answer": student_answer,
            "context": context,
        })

        logger.info(f"Mentor: результат оценки: {result[:200] if result else 'empty'}...")

        evaluation = "НЕПРАВИЛЬНО"
        feedback = "Ответ неверный."
        correct_answer = None

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
            elif "ПРАВИЛЬНЫЙ ОТВЕТ" in line_upper or "ЭТАЛОННЫЙ ОТВЕТ" in line_upper:
                if ":" in line:
                    correct_answer = line.split(":", 1)[1].strip()

# Если модель сказала "ПРАВИЛЬНО" - дополнительно проверим
        # что ответ хоть как-то связан с контекстом
        if evaluation == "ПРАВИЛЬНО":
            # Ищем слова из ответа в контексте
            context_lower = context.lower()
            answer_words = [w for w in answer_lower.split() if len(w) > 3]
            matches = sum(1 for w in answer_words if w in context_lower)
            
            if matches < 1:
                logger.info(f"Mentor: переопределяю ПРАВИЛЬНО -> НЕПРАВИЛЬНО (мало совпадений с контекстом)")
                evaluation = "НЕПРАВИЛЬНО"
                feedback = "Ответ не соответствует материалам учебника."
            else:
                feedback = "Верно! Ответ правильный."
        elif evaluation == "ЧАСТИЧНО":
            sentences = feedback.split(".")
            if len(sentences) > 0:
                clean_feedback = sentences[0].strip()
                if len(clean_feedback) > 5:
                    feedback = clean_feedback + "."
                else:
                    feedback = "Ответ частично правильный, но требует уточнения."
        else:
            pass

        logger.info(
            f"Mentor: оценен ответ user={user_id}: {evaluation} (ответ='{student_answer[:30]}...')"
        )

        gc.collect()

        result_dict = {
            "success": True,
            "evaluation": evaluation,
            "feedback": feedback,
        }
        
        if correct_answer:
            result_dict["correct_answer"] = correct_answer

        return result_dict

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
    from rag_chain import get_all_filenames_from_vector_db
    
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
    from rag_chain import get_all_filenames_from_vector_db
    
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