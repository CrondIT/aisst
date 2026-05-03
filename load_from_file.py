from pathlib import Path
from langchain_community.document_loaders import (
    PyPDFLoader,
    Docx2txtLoader,
    TextLoader,
)
import os
from langchain_text_splitters import RecursiveCharacterTextSplitter

import hashlib

from langchain_chroma import Chroma
from global_state import GUEST_RAG_DIR
from rag_embeddings import get_giga_embeddings
from utils import logger


# ── Настройки сплиттера ─────────────────────────────────────────────────────
# GigaChat Embeddings: лимит 514 токенов на чанк.
# ~1000 символов ≈ 400-450 токенов для обычного текста — безопасный размер.
# При 413 от API уменьшаем chunk_size вдвое (см. _split_with_retry).
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 150         # уменьшен с 200: меньше дублирования токенов
CHUNK_SIZE_MIN = 200        # нижняя граница — меньше нет смысла дробить
CHUNK_SIZE_DIVISOR = 2      # коэффициент уменьшения при retry


def get_file_hash(file_path):
    hash_sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_sha256.update(chunk)
    return hash_sha256.hexdigest()


def _extract_name_from_filename(filename: str) -> str:
    """
    Извлекает часть {name} из имени файла формата
    f"{default_name}_{user_id}_{name}.{ext}".
    
    Args:
        filename: Полное имя файла (например, "rag_12345_ustav.pdf")
    
    Returns:
        Часть {name} (например, "ustav"). Если формат не совпадает,
        возвращает имя файла без расширения.
    """
    base_without_ext = os.path.splitext(filename)[0]
    parts = base_without_ext.split('_')
    if len(parts) < 3:
        return base_without_ext
    # Первые 2 части: default_name и user_id, остальное — искомое {name}
    return '_'.join(parts[2:])


def check_vector_db(persist_dir: str, embeddings):
    """
    Загружает или создает векторную базу.
    При ошибке загрузки пробрасывает исключение.
    """
    os.makedirs(persist_dir, exist_ok=True)
    try:
        db = Chroma(
            persist_directory=persist_dir,
            embedding_function=embeddings
        )
        logger.info(f"Инициализирована база в {persist_dir}")
        return db
    except Exception as e:
        logger.error(f"Ошибка при инициализации базы {persist_dir}: {e}")
        raise


def is_file_in_vector_db(
        file_path: str, file_id: str, persist_dir: str, embeddings
):
    """
    Проверяет, присутствует ли файл с заданным file_id в векторной базе.

    Возвращает:
        (bool, Chroma): (найден_ли_файл, экземпляр_базы)
    """
    vector_db = check_vector_db(persist_dir, embeddings)
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
        "docx": Docx2txtLoader,
        ".doc": Docx2txtLoader,
        "doc": Docx2txtLoader,
        ".txt": TextLoader,
        "txt": TextLoader,
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


def _is_token_limit_error(exc: Exception) -> bool:
    """Определяет, является ли исключение ошибкой превышения токенов (413)."""
    msg = str(exc)
    return "413" in msg and "Tokens limit exceeded" in msg


def _make_splitter(chunk_size: int) -> RecursiveCharacterTextSplitter:
    """Создаёт сплиттер с заданным chunk_size."""
    # overlap не должен превышать половину chunk_size
    overlap = min(CHUNK_OVERLAP, chunk_size // 4)
    return RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        separators=["\n\n", "\n", " ", ""]
    )


async def _add_batch_with_retry(
    vector_db: Chroma,
    batch_docs: list,
    batch_start_idx: int,
    chunk_size: int,
    file_id: str,
) -> tuple[bool, int]:
    """
    Пытается добавить батч документов в векторную базу.
    При ошибке 413 (превышение токенов) дробит чанки и повторяет.

    Args:
        vector_db:       Экземпляр ChromaDB.
        batch_docs:      Список документов для добавления.
        batch_start_idx: Глобальный индекс первого документа батча
                         (для генерации уникальных ID).
        chunk_size:      Текущий размер чанка в символах.
        file_id:         Уникальный идентификатор файла (хеш) для ID чанков.

    Returns:
        (success: bool, actual_chunk_size: int)
        actual_chunk_size — финальный chunk_size после всех retry.
    """
    current_chunk_size = chunk_size
    current_docs = batch_docs

    while current_chunk_size >= CHUNK_SIZE_MIN:
        try:
            ids = [
                f"{file_id}_chunk_{batch_start_idx + j}"
                for j in range(len(current_docs))
            ]
            vector_db.add_documents(documents=current_docs, ids=ids)
            return True, current_chunk_size

        except Exception as e:
            if not _is_token_limit_error(e):
                # Не 413 — неизвестная ошибка, пробрасываем выше
                raise

            # 413: уменьшаем chunk_size и пересплитываем этот батч
            new_chunk_size = current_chunk_size // CHUNK_SIZE_DIVISOR
            if new_chunk_size < CHUNK_SIZE_MIN:
                logger.error(
                    f"Батч с индекса {batch_start_idx}: не удалось уложиться "
                    f"в лимит токенов при chunk_size={current_chunk_size}. "
                    f"Пропускаем батч."
                )
                return False, current_chunk_size

            logger.warning(
                f"413 на батче {batch_start_idx}: "
                f"chunk_size {current_chunk_size} → {new_chunk_size}, "
                f"пересплитываем {len(current_docs)} документов"
            )

            splitter = _make_splitter(new_chunk_size)
            current_docs = splitter.split_documents(current_docs)
            current_chunk_size = new_chunk_size

            logger.info(
                f"После пересплиттинга: {len(current_docs)} чанков "
                f"(chunk_size={new_chunk_size})"
            )

    return False, current_chunk_size


async def save_to_vector_db(
        file_path,
        sender: dict,
        model_name: str = "Embeddings",
        persist_dir: str = GUEST_RAG_DIR,
):
    """
    Загружает файл, разбивает на чанки и сохраняет в векторную базу ChromaDB.

    При ошибке 413 (превышение лимита токенов GigaChat Embeddings)
    автоматически уменьшает chunk_size вдвое и повторяет для проблемного батча.
    Минимальный chunk_size = CHUNK_SIZE_MIN (200 символов).
    """
    # ── 1. Проверяем наличие файла в базе по хешу ───────────────────────────
    embeddings = get_giga_embeddings(model_name)
    file_id = get_file_hash(file_path)

    is_file_found, vector_db = is_file_in_vector_db(
        file_path, file_id, persist_dir, embeddings
    )
    if is_file_found:
        logger.info(f"Файл уже есть в базе: {file_path}")
        return f"Файл уже загружен: {file_path}"

    # ── 2. Загружаем документ ───────────────────────────────────────────────
    loader = await get_loader_for_file(file_path)
    if loader is None:
        return f"Формат не поддерживается: {file_path}"

    try:
        documents = loader.load()
        # Добавляем имя файла и file_id в метаданные каждого документа
        filename = _extract_name_from_filename(os.path.basename(file_path))
        for doc in documents:
            doc.metadata["filename"] = filename
            doc.metadata["file_id"] = file_id
    except Exception as e:
        logger.error(f"Не удалось извлечь текст из {file_path}: {e}")
        return f"Не удалось извлечь текст из {file_path}: {e}"

    total_chars = sum(len(doc.page_content) for doc in documents)
    logger.info(
        f"Загружен документ: {os.path.basename(file_path)}, "
        f"страниц: {len(documents)}, символов: {total_chars}"
    )

    # ── 3. Разбиваем на чанки ───────────────────────────────────────────────
    splitter = _make_splitter(CHUNK_SIZE)
    chunks = splitter.split_documents(documents)
    logger.info(
        f"Разбито на {len(chunks)} чанков "
        f"(chunk_size={CHUNK_SIZE}, "
        f"overlap={min(CHUNK_OVERLAP, CHUNK_SIZE // 4)})"
    )

    # ── 4. Добавляем батчами с retry при 413 ────────────────────────────────
    batch_size = 10
    failed_batches = 0
    current_chunk_size = CHUNK_SIZE   # отслеживаем актуальный размер чанка

    for i in range(0, len(chunks), batch_size):
        batch_docs = chunks[i:i + batch_size]

        success, current_chunk_size = await _add_batch_with_retry(
            vector_db=vector_db,
            batch_docs=batch_docs,
            batch_start_idx=i,
            chunk_size=current_chunk_size,
            file_id=file_id,
        )

        if success:
            logger.info(
                f"Батч {i // batch_size + 1}: "
                f"загружено {i + len(batch_docs)} / {len(chunks)} чанков"
            )
        else:
            failed_batches += 1
            logger.error(
                f"Батч с индекса {i} пропущен после всех попыток retry"
            )

    # ── 5. Итоговый отчёт ──────────────────────────────────────────────────
    if failed_batches:
        return (
            f"Файл добавлен частично ({failed_batches} батч(ей) пропущено). "
            f"Символов: {total_chars}, фрагментов: {len(chunks)}"
        )

    return (
        f"Файл добавлен в базу. "
        f"Символов: {total_chars}, фрагментов: {len(chunks)}"
    )


def get_all_filenames_from_vector_db(
        persist_dir: str = GUEST_RAG_DIR,
        model_name: str = "Embeddings",
        search_text: str | None = None
) -> str:
    """
    Возвращает список уникальных имён файлов из векторной базы.
    Если передан search_text, ищет первое совпадение по именам файлов
    и возвращает имя найденного файла.
    """
    embeddings = get_giga_embeddings(model_name)
    vector_db = check_vector_db(persist_dir, embeddings)
    
    # Диагностика: сколько всего чанков в базе
    all_data = vector_db.get(include=["metadatas"])
    total_chunks = len(all_data.get("ids", []))
    logger.info(f"get_all_filenames: в базе всего чанков: {total_chunks}")
    
    filenames = set()
    metadatas = all_data.get("metadatas", [])
    
    for metadata in metadatas:
        filename = metadata.get("filename")
        if not filename:
            # Для старых документов извлекаем из source
            source = metadata.get("source", "")
            filename = os.path.basename(source) if source else None
        if filename:
            filenames.add(filename)
            
    logger.info(
        f"get_all_filenames: найдено уникальных имен: {len(filenames)} -"
        f" {filenames}"
    )
    
    sorted_names = sorted(filenames)
    if not sorted_names:
        return "База знаний пуста. Загрузите документы.\n"

    # Поиск по имени файла, если передан search_text
    if search_text:
        search_lower = search_text.lower()
        for name in sorted_names:
            if search_lower in name.lower():
                return name
        return "Документ с таким названием не найден."

    # Возврат полного списка
    lines = [f"{i+1}. {name}" for i, name in enumerate(sorted_names)]
    return "\n".join(lines) + "\n"


def delete_file_from_vector_db(
        file_name: str,
        persist_dir: str = GUEST_RAG_DIR,
        model_name: str = "Embeddings"
) -> str:
    """
    Удаляет документ из векторной базы по точному имени файла.
    Использует стандартный метод delete с фильтром where.
    """
    embeddings = get_giga_embeddings(model_name)
    vector_db = check_vector_db(persist_dir, embeddings)
    try:
        # Проверяем, есть ли такой файл в базе
        result = vector_db.get(
            where={"filename": file_name}, include=["metadatas"]
        )
        if not result or not result.get("ids"):
            logger.warning(
                f"Файл '{file_name}' не найден в базе для удаления."
            )
            return f"Файл '{file_name}' не найден в базе."

        logger.info(
            f"Удаление файла '{file_name}': "
            f"найдено {len(result['ids'])} чанков."
            )
        
        # Используем стандартный метод delete
        vector_db.delete(where={"filename": file_name})
        
        # Проверяем, что удаление прошло успешно
        verify = vector_db.get(where={"filename": file_name}, include=[])
        if verify and verify.get("ids"):
            logger.error(
                f"Файл '{file_name}' не был удален полностью. "
                f"Осталось {len(verify['ids'])} чанков."
            )
            return f"Ошибка: файл '{file_name}' не был полностью удален."
        
        logger.info(f"Файл '{file_name}' успешно удален. Проверка пройдена.")
        return f"Файл '{file_name}' успешно удален из базы."
    except Exception as e:
        logger.error(f"Ошибка при удалении файла '{file_name}': {e}")
        return f"Ошибка при удалении файла '{file_name}': {e}"
