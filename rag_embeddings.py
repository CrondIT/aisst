"""Модуль синглтона для GigaChat Embeddings."""

from langchain_gigachat.embeddings import GigaChatEmbeddings
from global_state import GIGACHAT_API_KEY, GIGACHAT_SCOPE

_giga_embeddings = None


def get_giga_embeddings() -> GigaChatEmbeddings:
    """Ленивая инициализация GigaChatEmbeddings (синглтон)."""
    global _giga_embeddings
    if _giga_embeddings is None:
        _giga_embeddings = GigaChatEmbeddings(
            credentials=GIGACHAT_API_KEY,
            scope=GIGACHAT_SCOPE,
            ca_bundle_file="russian_trusted_root_ca_pem.crt",
        )
    return _giga_embeddings
