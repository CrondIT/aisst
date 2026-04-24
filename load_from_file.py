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
import shutil


def get_file_hash(file_path):
    hash_sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        # Читаем частями, чтобы не забить RAM (важно для слабого сервера)
        for chunk in iter(lambda: f.read(4096), b""):
            hash_sha256.update(chunk)
    return hash_sha256.hexdigest()


def check_vector_db(persist_dir: str, embeddings):
    """
    Загружает существующую векторную базу из persist_dir,
    или создаёт новую пустую базу.
    В случае повреждения данных удаляет папку и создаёт чистую базу.

    Возвращает:
        Chroma: экземпляр векторной базы

    Исключения:
        Не выбрасывает, так как все ошибки обрабатываются внутренне.
    """
    # Если папка существует, пробуем загрузить базу
    if os.path.isdir(persist_dir):
        try:
            db = Chroma(
                persist_directory=persist_dir,
                embedding_function=embeddings
            )
            logger.info(f"Загружена существующая база из {persist_dir}")
            return db
        except Exception as e:
            logger.error(
                f"Ошибка при загрузке базы из {persist_dir}: "
                f"{e}. Удаляем повреждённую папку."
                )
            shutil.rmtree(persist_dir, ignore_errors=True)
            # Папка удалена — ниже создадим новую

    # Создаём папку и новую пустую базу
    os.makedirs(persist_dir, exist_ok=True)
    db = Chroma(
        persist_directory=persist_dir,
        embedding_function=embeddings
    )
    logger.info(f"Создана новая пустая векторная база в {persist_dir}")
    return db


def is_file_in_vector_db(
        file_path: str, file_id: str, persist_dir: str, embeddings
):
    """
    Проверяет, присутствует ли файл с заданным file_id в векторной базе.

    Аргументы:
        file_path: путь к файлу (только для логирования)
        file_id: уникальный идентификатор файла (обычно хеш)
        persist_dir: директория хранения Chroma
        embeddings: функция эмбеддингов

    Возвращает:
        (bool, Chroma): (найден_ли_файл, экземпляр_базы)
    """
    # Получаем базу (существующую или новую)
    vector_db = check_vector_db(persist_dir, embeddings)

    # Ищем документы с метаданным file_id (без загрузки содержимого)
    result = vector_db.get(where={"file_id": file_id}, include=[])

    if result['ids']:
        logger.info(f"Файл уже есть в базе: {file_path}")
        return True, vector_db
    return False, vector_db


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
        model_name: str = "Embeddings",
        persist_dir: str = GUEST_RAG_DIR,
):
    # user_id = sender.get("user_id")
    # file_name = os.path.basename(file_path)

    # 1. проверяем есть ли файл в базе
    embeddings = get_giga_embeddings(model_name)
    file_id = get_file_hash(file_path)  # вычисляем хеш нового файла

    is_file_found, vector_db = is_file_in_vector_db(
        file_path, file_id, persist_dir, embeddings
    )
    if is_file_found:
        logger.info(f"Файл уже есть в базе: {file_path}")
        return f"Файл уже загружен: {file_path}"

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

    # 2. Настраиваем сплиттер, chunk_size ~ 1500-1800 символов
    # обычно укладывается в 514 токенов GigaChat
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1200,
        chunk_overlap=200,  # нахлест, чтобы не терять смысл на стыках
        separators=["\n\n", "\n", " ", ""]
    )
    # 3. Разбиение на чанки
    chunks = text_splitter.split_documents(documents)

    # 5. Получаем эмбеддинги батчами
    batch_size = 10
    # progress_interval = 200
    # total_chunks = len(chunks)

    # 6. Получаем эмбеддинги батчами
    for i in range(0, len(chunks), batch_size):
        batch_docs = chunks[i:i + batch_size]
        try:
            vector_db.add_documents(
                documents=batch_docs,
                ids=[f"chunk_{i+j}" for j in range(len(batch_docs))]
            )
            logger.info(f"Загружено: {i + len(batch_docs)} / {len(chunks)}")
        except Exception as e:
            logger.error(f"Ошибка на батче {i}: {e}")
            return f"Ошибка на батче {i}: {e}"

    # 8. Сообщение о количестве обработанных символов и фрагментов.
    return (
        f"Файл добавлен в базу. Символов: {total_chars}, "
        f"фрагментов: {len(chunks)} "
    )
