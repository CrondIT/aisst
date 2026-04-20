from pathlib import Path
from langchain_community.document_loaders import (
    PyPDFLoader,
    Docx2txtLoader,
    TextLoader,
)
import os
import json
import hashlib
import asyncio
from langchain_text_splitters import RecursiveCharacterTextSplitter

from langchain_community.vectorstores import FAISS
from global_state import GUEST_RAG_DIR
from rag_embeddings import get_giga_embeddings
from utils import logger

ADDED_FILES_PATH = os.path.join(GUEST_RAG_DIR, "added_files.json")


def _load_added_files() -> dict:
    if os.path.exists(ADDED_FILES_PATH):
        with open(ADDED_FILES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_added_files(files: dict):
    os.makedirs(GUEST_RAG_DIR, exist_ok=True)
    with open(ADDED_FILES_PATH, "w", encoding="utf-8") as f:
        json.dump(files, f, ensure_ascii=False, indent=2)


def _get_file_hash(file_path: str) -> str:
    hasher = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _is_file_already_added(file_path: str) -> bool:
    added_files = _load_added_files()
    file_hash = _get_file_hash(file_path)
    return file_hash in added_files


def _mark_file_as_added(file_path: str):
    added_files = _load_added_files()
    file_hash = _get_file_hash(file_path)
    file_name = os.path.basename(file_path)
    added_files[file_hash] = {"name": file_name}
    _save_added_files(added_files)


async def get_loader_for_file(file_path: str):
    path = Path(file_path)
    # Приводим к нижнему регистру, чтобы .PDF и .pdf работали одинаково
    ext = path.suffix.lower()

    loaders = {
        ".pdf": PyPDFLoader,
        "pdf": PyPDFLoader,
        ".docx": Docx2txtLoader,
        ".doc": Docx2txtLoader,
        ".txt": TextLoader,
    }

    if ext in loaders:
        loader_class = loaders[ext]
        return loader_class(file_path)
    else:
        logger.warning(f"Формат {ext} не поддерживается")
        return f"Формат {ext} не поддерживается"


async def save_to_vector_db(file_path, model_name: str = "GigaChat"):
    # Проверяем, не добавлен ли уже этот файл
    if _is_file_already_added(file_path):
        logger.info(f"Файл уже добавлен: {file_path}")
        return "Этот файл уже был добавлен ранее."

    # 1. Загружаем документ
    loader = await get_loader_for_file(file_path)
    if loader is None:
        logger.warning(f"Формат не поддерживается: {file_path}")
        return f"Формат не поддерживается: {file_path}"
    documents = loader.load()

    # 2. Нарезаем на чанки (ограниченный размер для GigaChat)
    # MAX_TOTAL_CHARS = 1000_000
    total_chars = sum(len(doc.page_content) for doc in documents)
    # if total_chars > MAX_TOTAL_CHARS:
    #    print(
    #        f"Файл слишком большой: {total_chars} символов."
    #        f" Максимум: {MAX_TOTAL_CHARS}"
    #   )
    #    return

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=800,
        chunk_overlap=100
    )
    chunks = text_splitter.split_documents(documents)

    # 3. Инициализируем эмбеддинги
    embeddings = get_giga_embeddings(model_name)

    # 4. Получаем эмбеддинги батчами по 100 чанков
    all_embeddings = []
    batch_size = 100

    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        texts = [chunk.page_content for chunk in batch]
        embeddings_list = embeddings.embed_documents(texts)
        all_embeddings.extend(embeddings_list)
        logger.info(f"Обработано {min(i + batch_size, len(chunks))}/{len(chunks)} чанков")

    logger.info(f"Получено {len(all_embeddings)} эмбеддингов")

    # 5. Создаем или обновляем базу
    if os.path.exists(GUEST_RAG_DIR) and os.path.exists(
        os.path.join(GUEST_RAG_DIR, "index.faiss")
    ):
        vector_db = FAISS.load_local(
            GUEST_RAG_DIR,
            embeddings,
            allow_dangerous_deserialization=True
        )
        vector_db.add_embeddings(list(zip(chunks, all_embeddings)))
        logger.info(f"Добавлено {len(chunks)} фрагментов в существующую базу.")
    else:
        os.makedirs(GUEST_RAG_DIR, exist_ok=True)
        vector_db = FAISS.from_embeddings(chunks, embeddings, all_embeddings)
        logger.info(f"Создана новая база с {len(chunks)} фрагментами.")

    # 5. Сохраняем локально на диск
    vector_db.save_local(GUEST_RAG_DIR)
    logger.info(f"База успешно сохранена в папку: {GUEST_RAG_DIR}")

    # 6. Запоминаем, что файл добавлен
    _mark_file_as_added(file_path)

    return (
        f"Файл добавлен в базу. Символов: {total_chars}, "
        f"фрагментов: {len(chunks)} "
    )
