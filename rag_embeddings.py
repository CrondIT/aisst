"""Модуль синглтона для GigaChat Embeddings."""

from dataclasses import dataclass
from typing import Optional

from langchain_gigachat.embeddings import GigaChatEmbeddings
# from langchain_community.embeddings.gigachat import GigaChatEmbeddings
from global_state import (
    GIGACHAT_API_KEY,
    GIGACHAT_SCOPE,
    RUS_TRUSTED_ROOT_CA_PEM
    )

_giga_embeddings = None


@dataclass
class SearchSource:
    text: str
    source: Optional[str]
    score: Optional[float] = None


@dataclass
class SearchResult:
    context: str
    sources: list[SearchSource]


def get_giga_embeddings(model_name: str = "Embeddings") -> GigaChatEmbeddings:
    """Ленивая инициализация GigaChatEmbeddings (синглтон)."""
    global _giga_embeddings
    if _giga_embeddings is None:
        _giga_embeddings = GigaChatEmbeddings(
            credentials=GIGACHAT_API_KEY,
            scope=GIGACHAT_SCOPE,
            model=model_name,
            ca_bundle_file=RUS_TRUSTED_ROOT_CA_PEM,
        )
    return _giga_embeddings


def search_vector_db(
        prompt: str,
        vector_db,
        top_k: int = 3,
        max_context_chars: int = 2000
) -> SearchResult:
    """Поиск в векторной базе с оценкой релевантности."""
    # embeddings = get_giga_embeddings()
    # uery_vector = embeddings.embed_query(prompt)

    # results = vector_db.similarity_search_with_score(
    #    query_vector,
    #    k=top_k
    # )
    results = vector_db.similarity_search_with_relevance_scores(
        prompt, k=top_k
    )
    sources = []
    total_chars = 0

    for doc in results:
        if total_chars >= max_context_chars:
            break

        text = doc.page_content
        source = doc.metadata.get("source", "unknown")

        sources.append(SearchSource(
            text=text,
            source=source,
        ))
        total_chars += len(text)

    context = "\n".join(s.text for s in sources)

    return SearchResult(context=context, sources=sources)


def format_sources(sources: list[SearchSource]) -> str:
    """Форматирование источников для ответа."""
    if not sources:
        return "Источники не найдены."

    lines = ["📚 Источники:"]
    for i, s in enumerate(sources, 1):
        preview = s.text[:100] + "..." if len(s.text) > 100 else s.text
        lines.append(
            f"{i}. {s.source} (релевантность: {s.score:.2f})\n"
            f"   {preview}"
        )
    return "\n".join(lines)
