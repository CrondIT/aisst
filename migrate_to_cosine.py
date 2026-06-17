"""
Скрипт миграции ChromaDB: l2 → cosine distance.

Совместим с ChromaDB 1.x (Rust-бэкенд).

Процесс:
  1. Читает текст и метаданные из старой коллекции (l2)
     (без эмбеддингов — Rust-бэкенд их не отдаёт через get).
  2. Создаёт новую коллекцию с метрикой cosine.
  3. Добавляет чанки через add_texts — GigaChat Embeddings API
     вызывается автоматически для ре-эмбеддинга.

Запускать на рабочем сервере (бот может быть включён, миграция read-only).

После завершения:
  1. Останови бота
  2. mv rag/guest rag/guest_backup_l2
  3. mv rag/guest_cosine rag/guest
  4. Запусти бота
  5. Удали rag/guest_backup_l2 после проверки
"""

import os
import sys
import shutil
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from global_state import GUEST_RAG_DIR
from rag_chain.rag_embeddings import get_giga_embeddings
from langchain_chroma import Chroma
from utils import logger


COSINE_DIR = "rag/guest_cosine"

# GigaChat Embeddings: ~300-500 чанков/мин.
BATCH_SIZE = 50
RETRY_SLEEP = 10  # секунд между retry при ошибке API
MAX_RETRIES = 3


def migrate():
    logger.info("=" * 60)
    logger.info("Начало миграции ChromaDB: l2 → cosine")
    logger.info(f"  Источник: {GUEST_RAG_DIR}")
    logger.info(f"  Цель:     {COSINE_DIR}")
    logger.info("=" * 60)

    # ── 1. Проверяем, что исходная директория существует ──────────────
    if not os.path.isdir(GUEST_RAG_DIR):
        logger.error(f"Исходная директория {GUEST_RAG_DIR} не найдена. Прерывание.")
        sys.exit(1)

    # ── 2. Проверяем, не существует ли уже целевая директория ──────────
    if os.path.isdir(COSINE_DIR):
        logger.warning(f"Целевая директория {COSINE_DIR} уже существует.")
        answer = input(f"Удалить {COSINE_DIR} и продолжить? (y/N): ").strip().lower()
        if answer != "y":
            logger.info("Отменено пользователем.")
            return
        shutil.rmtree(COSINE_DIR)
        logger.info(f"Удалена {COSINE_DIR}")

    # ── 3. Открываем старую коллекцию (l2) ─────────────────────────────
    logger.info("Открытие старой коллекции (l2)...")
    embeddings = get_giga_embeddings()
    old_db = Chroma(
        persist_directory=GUEST_RAG_DIR,
        embedding_function=embeddings,
    )

    old_collection = old_db._collection
    logger.info(f"Старая коллекция: '{old_collection.name}', "
                f"метрика: {old_collection.metadata}")

    # ── 4. Читаем ВСЕ данные (без эмбеддингов — ChromaDB 1.x не отдаёт) ─
    logger.info("Чтение всех данных из старой коллекции...")
    # Читаем ID-шники отдельно, чтобы знать общее количество
    id_data = old_collection.get(include=[])
    all_ids = id_data.get("ids", [])
    total = len(all_ids)
    logger.info(f"Всего чанков: {total}")

    if total == 0:
        logger.warning("Коллекция пуста. Создаём пустую cosine-коллекцию.")

    # Читаем порциями, чтобы не перегрузить память (документы могут быть большими)
    documents = []
    metadatas = []
    ids = []
    fetch_batch = 500
    for i in range(0, total, fetch_batch):
        batch_ids = all_ids[i : i + fetch_batch]
        batch_data = old_collection.get(
            ids=batch_ids,
            include=["documents", "metadatas"],
        )
        ids.extend(batch_data.get("ids", []))
        documents.extend(batch_data.get("documents", []))
        metadatas.extend(batch_data.get("metadatas", []))
        logger.info(f"  Прочитано {len(ids)} / {total} чанков")

    if len(ids) != total:
        logger.error(f"Не удалось прочитать все чанки: {len(ids)} / {total}")
        sys.exit(1)

    # ── 5. Создаём новую коллекцию (cosine) ────────────────────────────
    logger.info("Создание новой коллекции с метрикой cosine...")
    new_db = Chroma(
        persist_directory=COSINE_DIR,
        embedding_function=embeddings,
        collection_metadata={"hnsw:space": "cosine"},
    )

    new_collection = new_db._collection
    logger.info(f"Новая коллекция: '{new_collection.name}', "
                f"метрика: {new_collection.metadata}")

    # ── 6. Добавляем чанки через add_texts (→ GigaChat Embeddings API) ──
    logger.info(f"Начало ре-эмбеддинга через GigaChat (батчи по {BATCH_SIZE})...")
    inserted = 0

    for i in range(0, total, BATCH_SIZE):
        batch_end = min(i + BATCH_SIZE, total)
        batch_ids = ids[i:batch_end]
        batch_docs = documents[i:batch_end]
        batch_metadatas = metadatas[i:batch_end]

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                new_db.add_texts(
                    texts=batch_docs,
                    metadatas=batch_metadatas,
                    ids=batch_ids,
                )
                inserted += len(batch_ids)
                logger.info(
                    f"  Батч {i // BATCH_SIZE + 1}: "
                    f"загружено {inserted} / {total} чанков "
                    f"({batch_ids[0]}...)"
                )
                break
            except Exception as e:
                logger.warning(
                    f"  Ошибка батча {i // BATCH_SIZE + 1} "
                    f"(попытка {attempt}/{MAX_RETRIES}): {e}"
                )
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_SLEEP)
                else:
                    logger.error(
                        f"  Батч {i // BATCH_SIZE + 1} пропущен "
                        f"после {MAX_RETRIES} попыток"
                    )

    # ── 7. Проверка ────────────────────────────────────────────────────
    verify_data = new_collection.get(include=[])
    verify_count = len(verify_data.get("ids", []))
    logger.info("=" * 60)
    if verify_count == total:
        logger.info(f"МИГРАЦИЯ УСПЕШНА: {verify_count} чанков перенесено "
                    f"в {COSINE_DIR}")
    else:
        logger.warning(
            f"МИГРАЦИЯ ЗАВЕРШЕНА ЧАСТИЧНО: "
            f"{verify_count} / {total} чанков"
        )

    logger.info("")
    logger.info("Дальнейшие действия:")
    logger.info(f"  1. systemctl stop aisst")
    logger.info(f"  2. mv {GUEST_RAG_DIR} {GUEST_RAG_DIR}_backup_l2")
    logger.info(f"  3. mv {COSINE_DIR} {GUEST_RAG_DIR}")
    logger.info(f"  4. systemctl start aisst")
    logger.info(f"  5. rm -rf {GUEST_RAG_DIR}_backup_l2  (после проверки)")
    logger.info("")


if __name__ == "__main__":
    migrate()
