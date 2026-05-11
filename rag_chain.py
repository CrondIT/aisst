"""
Модуль RAG-цепочки: GigaChat (LangChain) + ChromaDB.
Версия 2: умное извлечение источника — статья/раздел/глава из текста чанка.
Промпты загружаются из базы данных.
"""

import gc
import os
import re
import tracemalloc
from typing import Optional

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langchain_core.language_models import BaseChatModel

from rag_embeddings import get_giga_embeddings
from global_state import GUEST_RAG_DIR
from utils import logger
from prompt_repository import PromptRepository


# ── Мониторинг памяти ─────────────────────────────────────────────────────────
_MEMORY_THRESHOLD_MB = 300  # Порог предупреждения в MB
_tracemalloc_started = False


def _start_memory_tracking():
    """Запускает отслеживание памяти (один раз)."""
    global _tracemalloc_started
    if not _tracemalloc_started:
        tracemalloc.start()
        _tracemalloc_started = True


def _log_memory_usage(prefix: str = ""):
    """Логирует использование памяти. Вызывает WARNING если превышен порог."""
    current, peak = tracemalloc.get_traced_memory()
    current_mb = current / 1024 / 1024
    peak_mb = peak / 1024 / 1024

    if current_mb > _MEMORY_THRESHOLD_MB:
        logger.warning(
            f"{prefix}Память: текущая {current_mb:.1f} MB, "
            f"пиковая {peak_mb:.1f} MB (превышен порог {_MEMORY_THRESHOLD_MB} MB)"
        )
    else:
        logger.debug(
            f"{prefix}Память: текущая {current_mb:.1f} MB, "
            f"пиковая {peak_mb:.1f} MB"
        )


def list_all_documents_in_db(vector_db) -> list[str]:
    """Возвращает список уникальных имён файлов в ChromaDB."""
    try:
        all_docs = vector_db.get()
        sources = set()
        for doc in all_docs.get("metadatas", []):
            if doc and "source" in doc:
                source = doc["source"]
                filename = os.path.basename(source)
                # Убираем префиксы
                filename = re.sub(r"^rag_\d+_", "", filename)
                filename = re.sub(r"^file_\d+_", "", filename)
                filename = re.sub(r"^[\w]+_\d+_", "", filename)
                filename = os.path.splitext(filename)[0]
                sources.add(filename)
        return sorted(sources)
    except Exception as e:
        logger.error(f"Ошибка при получении списка документов: {e}")
        return []


# ── Паттерны для извлечения заголовков из текста чанка ───────────────────────
# Порядок важен: более специфичные — первыми
_HEADING_PATTERNS = [
    # Статья 5, Статья 5.1 + необязательный заголовок
    r"(Стать[яи]\s+\d+(?:\.\d+)*(?:\s*[А-ЯA-Z][^\n]{0,60})?)",
    # Раздел II, Раздел 3
    r"(Раздел\s+(?:[IVXLCDM]+|\d+)(?:\s*[А-ЯA-Z][^\n]{0,60})?)",
    # Глава 3, Глава III
    r"(Глав[аы]\s+(?:[IVXLCDM]+|\d+)(?:\s*[А-ЯA-Z][^\n]{0,60})?)",
    # Пункт 1.2.3
    r"(Пункт\s+\d+(?:\.\d+)*(?:\s*[А-ЯA-Z][^\n]{0,60})?)",
    # § 5
    r"(§\s*\d+(?:\.\d+)*(?:\s*[А-ЯA-Z][^\n]{0,60})?)",
]

_COMPILED = [re.compile(p, re.IGNORECASE | re.MULTILINE)
             for p in _HEADING_PATTERNS]


def _extract_heading(text: str) -> str | None:
    """
    Ищет первое упоминание статьи/раздела/главы в тексте чанка.
    Возвращает найденный заголовок (макс. 160 символов) или None.
    """
    for pattern in _COMPILED:
        match = pattern.search(text)
        if match:
            heading = " ".join(match.group(1).split())
            return heading[:160]
    return None


def _make_source_label(doc) -> str:
    """
    Формирует читаемую подпись источника для одного документа.

    Пример:
        «устав_колледжа.pdf», стр. 12, Статья 5. Права обучающихся
    """
    meta = doc.metadata

    # Имя файла: берём basename, убираем служебные префиксы
    raw_source = meta.get("source", "")
    filename = os.path.basename(raw_source)
    filename = os.path.splitext(filename)[0]
    filename = re.sub(r"^rag_\d+_", "", filename)  # rag_12345_
    filename = re.sub(r"^file_\d+_", "", filename)  # file_4827597_
    filename = re.sub(r"^[\w]+_\d+_", "", filename)  # любой_префикс_цифры_

    # Номер страницы (PyPDFLoader считает с 0, показываем с 1)
    # page = meta.get("page")
    # page_str = f", стр. {page + 1}" if page is not None else ""

    # Заголовок статьи/раздела/главы из текста чанка
    heading = _extract_heading(doc.page_content)
    heading_str = f", {heading}" if heading else ""

    return f"«{filename}»{heading_str}"


def _format_docs(docs: list) -> str:
    """
    Склеивает найденные документы в строку контекста.
    Каждый фрагмент начинается с названия документа.
    Модель должна скопировать название документа в ответ.
    """
    if not docs:
        return "Документы по вашему вопросу не найдены."

    parts = []
    for doc in docs:
        label = _make_source_label(doc)
        parts.append(
            f"Документ: {label}\n"
            f"{doc.page_content.strip()}"
        )
    return "\n\n".join(parts)


# ── Кэш промпта RAG ──────────────────────────────────────────────────────────
_rag_prompt: Optional[ChatPromptTemplate] = None
_rag_prompt_loaded = False


async def _load_rag_prompt() -> ChatPromptTemplate:
    """
    Загружает RAG промпт из базы данных.
    Кэширует результат для повторных вызовов.
    """
    global _rag_prompt, _rag_prompt_loaded

    if _rag_prompt_loaded and _rag_prompt:
        return _rag_prompt

    prompt_template = await PromptRepository.get_prompt_template("rag_default")

    if prompt_template:
        _rag_prompt = prompt_template
    else:
        from db import DEFAULT_PROMPTS
        rag_data = DEFAULT_PROMPTS.get("rag_default", {})
        _rag_prompt = ChatPromptTemplate.from_messages([
            ("system", rag_data.get("system", "")),
            ("human", rag_data.get("human", "")),
        ])

    _rag_prompt_loaded = True
    logger.info("RAG промпт загружен из БД")
    return _rag_prompt


def invalidate_rag_prompt_cache():
    """Сбрасывает кэш RAG промпта."""
    global _rag_prompt_loaded
    _rag_prompt_loaded = False
    logger.info("Кэш RAG промпта сброшен")


# ── Синглтон для LCEL-цепочки ───────────────────────────────────────────────
_rag_chain = None
_rag_chain_llm = None
_rag_chain_params = None
_rag_chain_prompt_id = None


def _get_rag_chain(
    lc_llm: BaseChatModel,
    rag_prompt: ChatPromptTemplate,
    top_k: int = 5,
    fetch_k: int = 20,
    lambda_mult: float = 0.9,
):
    """
    Возвращает синглтон LCEL-цепочки для RAG.
    Пересоздаёт цепочку только если LLM, параметры или промпт изменились.
    """
    global _rag_chain, _rag_chain_llm, _rag_chain_params, _rag_chain_prompt_id

    prompt_id = id(rag_prompt)
    current_params = (id(lc_llm), top_k, fetch_k, lambda_mult, prompt_id)

    if _rag_chain is None or _rag_chain_params != current_params:
        from load_from_file import check_vector_db
        from global_state import GUEST_RAG_DIR
        from rag_embeddings import get_giga_embeddings

        embeddings = get_giga_embeddings(model_name="Embeddings")
        vector_db = check_vector_db(persist_dir=GUEST_RAG_DIR, embeddings=embeddings)

        retriever = vector_db.as_retriever(
            search_type="mmr",
            search_kwargs={
                "k": top_k,
                "fetch_k": fetch_k,
                "lambda_mult": lambda_mult,
            }
        )

        _rag_chain = (
            {
                "context": retriever | RunnableLambda(_format_docs),
                "question": RunnablePassthrough(),
            }
            | rag_prompt
            | lc_llm
            | StrOutputParser()
        )
        _rag_chain_llm = lc_llm
        _rag_chain_params = current_params
        _rag_chain_prompt_id = prompt_id

    return _rag_chain


async def ask_rag(
    user_text: str,
    lc_llm: BaseChatModel,
    top_k: int = 5,
    fetch_k: int = 20,
    lambda_mult: float = 0.9,
) -> str:
    """
    Поиск ответа в ChromaDB + генерация через LangChain-совместимый LLM.
    Использует MMR (Maximum Marginal Relevance) для разнообразия источников.
    Промпт загружается из базы данных.
    """
    try:
        _start_memory_tracking()

        rag_prompt = await _load_rag_prompt()
        chain = _get_rag_chain(lc_llm, rag_prompt, top_k, fetch_k, lambda_mult)

        answer: str = await chain.ainvoke(user_text)
        logger.info(f"RAG ответ: {len(answer)} символов")

        gc.collect()
        _log_memory_usage("RAG: ")

        if "не найден" in answer.lower():
            logger.warning(
                f"RAG: модель не нашла информацию. Запрос: '{user_text}'"
            )

        return answer

    except Exception as e:
        logger.error(f"Ошибка RAG-цепочки: {e}", exc_info=True)
        return "Извините, не удалось найти ответ в базе документов."
