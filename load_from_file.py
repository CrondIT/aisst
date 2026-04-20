from pathlib import Path
from langchain_community.document_loaders import (
    PyPDFLoader,
    Docx2txtLoader,
    TextLoader,
)
import os
from langchain_text_splitters import RecursiveCharacterTextSplitter

from langchain_community.vectorstores import FAISS
from global_state import GUEST_RAG_DIR
from rag_embeddings import get_giga_embeddings


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
        print(f"Формат {ext} не поддерживается")
        return None


async def save_to_vector_db(file_path, model_name: str = "GigaChat"):
    # 1. Загружаем документ
    loader = await get_loader_for_file(file_path)
    if loader is None:
        print(f"Формат не поддерживается: {file_path}")
        return
    documents = loader.load()

    # 2. Нарезаем на чанки (ограниченный размер для GigaChat)
    MAX_TOTAL_CHARS = 1000_000
    total_chars = sum(len(doc.page_content) for doc in documents)
    if total_chars > MAX_TOTAL_CHARS:
        print(
            f"Файл слишком большой: {total_chars} символов."
            f" Максимум: {MAX_TOTAL_CHARS}"
        )
        return

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200
    )
    chunks = text_splitter.split_documents(documents)

    # 3. Инициализируем эмбеддинги
    embeddings = get_giga_embeddings(model_name)
    # 4. Создаем или обновляем базу
    if os.path.exists(GUEST_RAG_DIR) and os.path.exists(
        os.path.join(GUEST_RAG_DIR, "index.faiss")
    ):
        # Загружаем существующую базу
        vector_db = FAISS.load_local(
            GUEST_RAG_DIR, embeddings, allow_dangerous_deserialization=True
        )
        # Добавляем новые документы
        vector_db.add_documents(chunks)
        print(f"Добавлено {len(chunks)} фрагментов в существующую базу.")
    else:
        # Создаем новую базу
        os.makedirs(GUEST_RAG_DIR, exist_ok=True)
        vector_db = FAISS.from_documents(chunks, embeddings)
        print(f"Создана новая база с {len(chunks)} фрагментами.")

    # 5. Сохраняем локально на диск
    vector_db.save_local(GUEST_RAG_DIR)
    print(f"База успешно сохранена в папку: {GUEST_RAG_DIR}")
