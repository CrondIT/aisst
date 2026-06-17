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
from langchain_core.language_models import BaseChatModel
from langchain_core.documents import Document

from .rag_embeddings import get_giga_embeddings
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


# ── Document Expansion ──────────────────────────────────────────────────────────
def _get_chunk_index(chunk_id: str) -> int:
    """Извлекает номер чанка из ID вида abc123_chunk_42."""
    match = re.search(r'_chunk_(\d+)$', chunk_id)
    return int(match.group(1)) if match else 0


def _get_chunk_count_for_filename(vector_db, filename: str) -> int:
    """Возвращает количество чанков для документа с данным filename."""
    result = vector_db.get(where={"filename": filename}, include=[])
    return len(result.get("ids", []))


async def _retrieve_with_expansion(
    vector_db,
    query: str,
    top_k: int = 8,
    fetch_k: int = 80,
    lambda_mult: float = 0.9,
) -> list[Document]:
    """
    MMR + Document Expansion для маленьких документов.

    Если документ ≤ порога чанков — загружаем все его чанки (в порядке
    исходного файла), а не только top_k от MMR.
    Порог = max(20, всего_чанков_в_БД // 100).
    """
    # 1. MMR retrieval
    retriever = vector_db.as_retriever(
        search_type="mmr",
        search_kwargs={"k": top_k, "fetch_k": fetch_k, "lambda_mult": lambda_mult},
    )
    initial_docs = await retriever.ainvoke(query)

    # 2. Dynamic threshold
    total_chunks = len(vector_db.get(include=[]).get("ids", []))
    expand_threshold = max(20, total_chunks // 100)
    logger.info(
        f"RAG expansion: MMR={len(initial_docs)} доков, "
        f"порог={expand_threshold} чанков (всего в БД {total_chunks})"
    )

    # 3. Identify small docs to expand
    expand_filenames: set[str] = set()
    for doc in initial_docs:
        filename = doc.metadata.get("filename", "")
        if not filename:
            continue
        chunk_count = _get_chunk_count_for_filename(vector_db, filename)
        if 1 < chunk_count <= expand_threshold:
            expand_filenames.add(filename)
            logger.info(f"RAG expansion: будет расширен '{filename}' — {chunk_count} чанков")

    if not expand_filenames:
        return initial_docs

    # 4. Build result: keep non-expanded docs as-is, replace expanded with all chunks
    expanded_docs: list[Document] = []
    already_expanded: set[str] = set()

    for doc in initial_docs:
        filename = doc.metadata.get("filename", "")
        if filename in expand_filenames:
            if filename in already_expanded:
                continue  # уже добавили все чанки для этого файла
            already_expanded.add(filename)
            # Загружаем ВСЕ чанки файла
            all_data = vector_db.get(
                where={"filename": filename},
                include=["documents", "metadatas"],
            )
            all_ids = all_data.get("ids", [])
            all_texts = all_data.get("documents", [])
            all_metadatas = all_data.get("metadatas", [])
            # Сортируем по номеру чанка
            sorted_indices = sorted(
                range(len(all_ids)),
                key=lambda i: _get_chunk_index(all_ids[i]),
            )
            for idx in sorted_indices:
                expanded_docs.append(Document(
                    page_content=all_texts[idx],
                    metadata=all_metadatas[idx],
                ))
        else:
            expanded_docs.append(doc)

    logger.info(f"RAG expansion: итого {len(expanded_docs)} чанков "
                f"(из них {sum(1 for f in expand_filenames for _ in [1])} документов расширено)")
    return expanded_docs


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


def reset_rag_chain():
    """Сбрасывает кэш LCEL-цепочки. Вызывать после изменений в векторной БД."""
    global _rag_chain, _rag_chain_params, _rag_chain_llm, _rag_chain_prompt_id
    _rag_chain = None
    _rag_chain_params = None
    _rag_chain_llm = None
    _rag_chain_prompt_id = None
    logger.info("RAG: кэш цепочки сброшен (reset_rag_chain)")


def _get_rag_chain(
    lc_llm: BaseChatModel,
    rag_prompt: ChatPromptTemplate,
):
    """
    Возвращает синглтон LCEL-цепочки для RAG (только промпт + LLM).
    Ретривер не встроен — контекст передаётся снаружи через ask_rag.
    """
    global _rag_chain, _rag_chain_llm, _rag_chain_params, _rag_chain_prompt_id

    prompt_id = id(rag_prompt)
    current_params = (id(lc_llm), prompt_id)

    if _rag_chain is None or _rag_chain_params != current_params:
        _rag_chain = rag_prompt | lc_llm | StrOutputParser()
        _rag_chain_llm = lc_llm
        _rag_chain_params = current_params
        _rag_chain_prompt_id = prompt_id
        logger.info("RAG: цепочка создана/пересоздана")

    return _rag_chain


async def ask_rag(
    user_text: str,
    lc_llm: BaseChatModel,
    top_k: int = 8,
    fetch_k: int = 80,
    lambda_mult: float = 0.9,
) -> str:
    """
    Поиск ответа в ChromaDB + генерация через LangChain-совместимый LLM.
    Поиск: MMR + Document Expansion для маленьких документов.
    Запрос поиска дополняется контекстом техникума.
    Промпт загружается из базы данных.
    """
    try:
        _start_memory_tracking()

        rag_prompt = await _load_rag_prompt()
        chain = _get_rag_chain(lc_llm, rag_prompt)

        # ── Поиск с расширением контекста техникума ──
        from .load_from_file import check_vector_db
        embeddings = get_giga_embeddings()
        vector_db = check_vector_db(
            persist_dir=GUEST_RAG_DIR,
            embeddings=embeddings,
        )
        all_chunks = vector_db.get(include=["metadatas"])
        logger.info(
            f"RAG диагностика: в БД {len(all_chunks.get('ids', []))} чанков"
        )

        docs = await _retrieve_with_expansion(
            vector_db, user_text, top_k, fetch_k, lambda_mult,
        )

        context = _format_docs(docs)

        answer: str = await chain.ainvoke(
            {"context": context, "question": user_text}
        )
        logger.info(f"RAG ответ: {len(answer)} символов. Текст: {answer[:300]}")

        gc.collect()
        _log_memory_usage("RAG: ")

        if "не найден" in answer.lower() or "информация не найдена" in answer.lower():
            logger.warning(
                f"RAG: модель не нашла информацию. Запрос: '{user_text}'"
            )

        return answer

    except Exception as e:
        logger.error(f"Ошибка RAG-цепочки: {e}", exc_info=True)
        return "Извините, не удалось найти ответ в базе документов."
