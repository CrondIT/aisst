"""
RAG Worker - обработчик очереди задач для загрузки файлов в векторную базу.
Запускается как отдельный процесс для асинхронной обработки больших файлов.
"""

import asyncio
import logging
import os
import sys
from dotenv import load_dotenv

# Добавляем путь к проекту для импортов (rag_chain/../ = корень проекта)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

# Инициализируем логирование
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("rag_worker.log", encoding="utf-8", mode="a"),
    ],
)
logger = logging.getLogger("rag_worker")

# Импорт после настройки логирования
from redis_utils import RedisQueue, RedisQueueError
from .load_from_file import save_to_vector_db
import db as db_module


async def process_rag_task(task_data: dict) -> dict:
    """
    Обрабатывает одну RAG задачу.

    Args:
        task_data: Словарь с полями:
            - file_path: путь к файлу
            - user_id: ID пользователя
            - sender: информация об отправителе

    Returns:
        Словарь с результатом:
            - status: 'completed' или 'failed'
            - result: текст результата (при успехе)
            - error: текст ошибки (при неудаче)
            - user_id: ID пользователя
    """
    file_path = task_data.get("file_path")
    user_id = task_data.get("user_id")
    sender = task_data.get("sender", {})

    if not file_path or not user_id:
        logger.error(f"Неполные данные задачи: {task_data}")
        return {
            "status": "failed",
            "error": "Недостаточно данных для обработки",
            "user_id": user_id,
        }

    logger.info(f"Начало обработки: файл={file_path}, user_id={user_id}")

    try:
        result = await save_to_vector_db(
            file_path=file_path,
            sender=sender,
            model_name="Embeddings"
        )
        logger.info(f"Задача выполнена: {result}")
        return {
            "status": "completed",
            "result": result,
            "user_id": user_id,
        }

    except Exception as e:
        logger.error(f"Ошибка обработки задачи: {e}", exc_info=True)
        return {
            "status": "failed",
            "error": str(e),
            "user_id": user_id,
        }


async def run_worker():
    """Основной цикл воркера."""
    try:
        queue = RedisQueue()
        logger.info("RAG Worker запущен, слушаю очередь...")
        logger.info(f"Очередь RAG: {queue._make_key('queue', 'rag')}")

        # Инициализируем БД для логирования
        await db_module.create_database()

        while True:
            try:
                # Получаем задачу из RAG очереди (блокирующий вызов)
                task = queue.dequeue(
                    queue_types=["rag"],
                    timeout=5,
                    priority_aware=True
                )

                if task:
                    task_id = task.get("id", "unknown")
                    logger.info(f"Получена задача {task_id[:8]}...")

                    try:
                        result = await process_rag_task(task.get("data", {}))
                        logger.info(f"process_rag_task result type: {type(result)}, value: {result}")
                        queue.publish_result(task_id, result)

                        if isinstance(result, dict) and "status" in result and result["status"] == "completed":
                            logger.info(
                                f"Задача {task_id[:8]}... выполнена "
                                f"для user_id={result.get('user_id')}"
                            )
                        else:
                            logger.error(
                                f"Задача {task_id[:8]}... провалена: "
                                f"{result.get('error')}"
                            )

                    except Exception as e:
                        logger.exception(
                            f"Ошибка обработки задачи {task_id[:8]}...: {e}"
                        )
                        # Публикуем ошибку
                        queue.publish_result(task_id, {
                            "status": "failed",
                            "error": str(e),
                            "user_id": task.get("data", {}).get("user_id"),
                        })

            except RedisQueueError as e:
                logger.error(f"Ошибка Redis: {e}")
                await asyncio.sleep(5)

            except Exception as e:
                logger.exception(f"Неожиданная ошибка в цикле: {e}")
                await asyncio.sleep(1)

    except KeyboardInterrupt:
        logger.info("Получен сигнал завершения, останавливаюсь...")
    except Exception as e:
        logger.exception(f"Критическая ошибка воркера: {e}")
        raise
    finally:
        logger.info("RAG Worker остановлен")


if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("Запуск RAG Worker")
    logger.info("=" * 50)
    asyncio.run(run_worker())