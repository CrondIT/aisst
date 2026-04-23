from pathlib import Path
from langchain_community.document_loaders import (
    PyPDFLoader,
    Docx2txtLoader,
    TextLoader,
)
import os
from langchain_text_splitters import RecursiveCharacterTextSplitter

import hashlib

# from langchain_community.vectorstores import FAISS
from langchain_chroma import Chroma
from global_state import GUEST_RAG_DIR
from rag_embeddings import get_giga_embeddings
from utils import logger


def get_file_hash(file_path):
    hash_sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        # Читаем частями, чтобы не забить RAM (важно для слабого сервера)
        for chunk in iter(lambda: f.read(4096), b""):
            hash_sha256.update(chunk)
    return hash_sha256.hexdigest()


def check_in_vector_db(file_id, collection):
    """
    Проверяет наличие файла по его уникальному хешу (file_id).
    collection — это объект коллекции ChromaDB.
    """
    # Ищем запись по ID. Параметр include=[] отключает загрузку
    # документов и эмбеддингов, что экономит RAM.
    existing = collection.get(
        ids=[file_id],
        include=[]
    )

    # Если список IDs не пуст — файл уже в базе
    return len(existing['ids']) > 0


async def get_loader_for_file(file_path: str):
    path = Path(file_path)
    ext = path.suffix.lower()
    loaders = {
        ".pdf": PyPDFLoader,
        "pdf": PyPDFLoader,
        ".docx": Docx2txtLoader,
        ".doc": Docx2txtLoader,
        ".txt": TextLoader,
    }
    if ext not in loaders:
        logger.warning(f"Формат {ext} не поддерживается")
        return None
    loader_class = loaders[ext]
    try:
        return loader_class(file_path)
    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e).lower()

        if "encrypt" in error_msg or "password" in error_msg:
            logger.error(f"PDF защищён паролем: {file_path}")
        elif "pdf" in error_type.lower():
            logger.error(f"PDF повреждён или ошибка чтения: {file_path}")
        elif "docx" in error_type.lower():
            logger.error(f"DOCX повреждён: {file_path}")
        else:
            logger.error(f"Ошибка загрузки {file_path}: {error_type}")
        return None


async def save_to_vector_db(
        file_path,
        sender: dict,
        model_name: str = "GigaChat"
):
    user_id = sender.get("user_id")
    file_name = os.path.basename(file_path)

    from utils import send_message_from_file

    async def send_progress(current: int, total: int):
        percent = int(current / total * 100)
        text = (
            f"📄 {file_name}\n\n"
            f"⏳ Прогресс: {current}/{total} ({percent}%)\n\n"
            f"Обработка продолжается..."
        )
        await send_message_from_file(user_id, text)

    file_id = get_file_hash(file_path)
    embeddings = get_giga_embeddings(model_name)
    vector_db = None
    if os.path.exists(GUEST_RAG_DIR):
        vector_db = Chroma(
            persist_directory=GUEST_RAG_DIR,
            embedding_function=embeddings
        )
        if check_in_vector_db(file_id, vector_db._collection):
            logger.info(f"Файл уже есть в базе: {file_path}")
            return f"Файл уже есть в базе: {file_path}"
    loader = await get_loader_for_file(file_path)
    if loader is None:
        logger.warning(f"Формат не поддерживается: {file_path}")
        return f"Формат не поддерживается: {file_path}"
    try:
        documents = loader.load()
    except Exception as e:
        logger.error(f"Не удалось извлечь текст из {file_path}: {e}")
        return f"Не удалось извлечь текст из {file_path}: {e}"

    total_chars = sum(len(doc.page_content) for doc in documents)
    # 5. Разбиение на чанки
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=800,
        chunk_overlap=100
    )
    chunks = text_splitter.split_documents(documents)

    # 6. Получаем эмбеддинги батчами
    all_embeddings = []
    batch_size = 50
    progress_interval = 200
    total_chunks = len(chunks)

    for i in range(0, total_chunks, batch_size):
        batch = chunks[i:i + batch_size]
        texts = [chunk.page_content for chunk in batch]
        embeddings_list = embeddings.embed_documents(texts)
        all_embeddings.extend(embeddings_list)

        processed = min(i + batch_size, total_chunks)
        if processed % progress_interval == 0:
            # logger.info(f"Обработано {processed}/{total_chunks} чанков")
            await send_progress(processed, total_chunks)

    logger.info(f"Получено {len(all_embeddings)} эмбеддингов")
    # 7. Сохранение в ChromaDB
    # Если база уже есть — добавляются новые документы
    # Если нет — создаётся новая база с первыми документами
    if os.path.exists(GUEST_RAG_DIR) and os.listdir(GUEST_RAG_DIR):
        vector_db = Chroma(
            persist_directory=GUEST_RAG_DIR,
            embedding_function=embeddings
        )
        vector_db.add_documents(documents=chunks)
        logger.info(
            f"Добавлено {len(chunks)} фрагментов в существующую базу."
        )
    else:
        os.makedirs(GUEST_RAG_DIR, exist_ok=True)
        vector_db = Chroma.from_documents(
            documents=chunks,
            embedding=embeddings,
            persist_directory=GUEST_RAG_DIR
        )
        logger.info(f"Создана новая база с {len(chunks)} фрагментами.")
    # 8. Сообщение о количестве обработанных символов и фрагментов.
    return (
        f"Файл добавлен в базу. Символов: {total_chars}, "
        f"фрагментов: {len(chunks)} "
    )
