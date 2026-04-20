"""Модуль синглтона для GigaChat Embeddings."""

from langchain_gigachat.embeddings import GigaChatEmbeddings
# from langchain_community.embeddings.gigachat import GigaChatEmbeddings
from global_state import (
    GIGACHAT_API_KEY,
    GIGACHAT_SCOPE,
    RUS_TRUSTED_ROOT_CA_PEM
    )

_giga_embeddings = None


def get_giga_embeddings(model_name: str = "GigaChat") -> GigaChatEmbeddings:
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
