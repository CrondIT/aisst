"""
Модуль RAG-цепочки: GigaChat (LangChain) + ChromaDB.
Версия 2: умное извлечение источника — статья/раздел/глава из текста чанка.
"""

import os
import re

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langchain_core.language_models import BaseChatModel

from load_from_file import check_vector_db
from rag_embeddings import get_giga_embeddings
from global_state import GUEST_RAG_DIR
from utils import logger


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

    # Имя файла: берём basename, убираем служебный префикс "rag_12345_"
    raw_source = meta.get("source", "")
    filename = os.path.basename(raw_source)
    filename = re.sub(r"^rag_\d+_", "", filename)

    # Номер страницы (PyPDFLoader считает с 0, показываем с 1)
    page = meta.get("page")
    page_str = f", стр. {page + 1}" if page is not None else ""

    # Заголовок статьи/раздела/главы из текста чанка
    heading = _extract_heading(doc.page_content)
    heading_str = f", {heading}" if heading else ""

    return f"«{filename}»{page_str}{heading_str}"


def _format_docs(docs: list) -> str:
    """
    Склеивает найденные документы в строку контекста.
    Каждый фрагмент помечен меткой [N] с указанием источника.
    Модель увидит эти метки и воспроизведёт их в ответе.
    """
    if not docs:
        return "Документы по вашему вопросу не найдены."

    parts = []
    for i, doc in enumerate(docs, 1):
        label = _make_source_label(doc)
        parts.append(
            f"[{i}] Источник: {label}\n"
            f"{doc.page_content.strip()}"
        )
    return "\n\n".join(parts)


# ── Системный промпт ─────────────────────────────────────────────────────────
_SYSTEM_PROMPT = (
    "Ты — помощник студентов колледжа ССТ.\n"
    "Отвечай ТОЛЬКО на основе предоставленных фрагментов документов.\n"
    "Если в документах нет ответа — прямо скажи об этом.\n"
    "Если в документах несколько фактов приведи их все \n"
    "Отвечай кратко: не более 6–10 предложений.\n"
    "Не придумывай факты.\n\n"
    "ВАЖНО: в конце ответа ОБЯЗАТЕЛЬНО укажи источник в формате:\n"
    "📄 Источник: <название файла>, <стр. N>, "
    "<Статья или Раздел если найдены>\n\n"
    "Фрагменты документов:\n{context}"
)

_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM_PROMPT),
    ("human", "{question}"),
])


async def ask_rag(
    user_text: str,
    lc_llm: BaseChatModel,
    top_k: int = 3,
) -> str:
    """
    Поиск ответа в ChromaDB + генерация через LangChain-совместимый LLM.

    Args:
        user_text: Вопрос пользователя.
        lc_llm:    LangChain-совместимая модель (app.state.giga_lc_client).
        top_k:     Количество фрагментов для поиска.

    Returns:
        Строка с ответом + указание источника.
    """
    try:
        embeddings = get_giga_embeddings(model_name="Embeddings")
        vector_db = check_vector_db(
            persist_dir=GUEST_RAG_DIR, embeddings=embeddings
        )
        retriever = vector_db.as_retriever(search_kwargs={"k": top_k})

        chain = (
            {
                "context": retriever | RunnableLambda(_format_docs),
                "question": RunnablePassthrough(),
            }
            | _PROMPT
            | lc_llm
            | StrOutputParser()
        )

        answer: str = await chain.ainvoke(user_text)
        logger.info(f"RAG ответ: {len(answer)} символов")
        return answer

    except Exception as e:
        logger.error(f"Ошибка RAG-цепочки: {e}", exc_info=True)
        return "Извините, не удалось найти ответ в базе документов."
