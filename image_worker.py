"""
Image Worker - обработчик очереди задач для генерации/редактирования изображений.
Запускается как отдельный процесс для асинхронной обработки долгих запросов к OpenAI.
"""

import asyncio
import logging
import os
import sys
from dotenv import load_dotenv
from loguru import logger as loguru_logger

# Загружаем переменные окружения
load_dotenv()

# Инициализируем стандартное логирование
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("image_worker.log", encoding="utf-8", mode="a"),
    ],
)
logger = logging.getLogger("image_worker")

# Настраиваем loguru (используется ai_models.py и другими модулями)
loguru_logger.remove()
loguru_logger.add(
    sys.stdout,
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {name}:{function}:{line} - {message}",
    level="INFO",
)
loguru_logger.add(
    "image_worker.log",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {name}:{function}:{line} - {message}",
    level="INFO",
    encoding="utf-8",
    enqueue=True,
)

# Импорт после настройки логирования
from redis_utils import RedisQueue, RedisQueueError
from ai_models import OpenAIClient
import max_api
import db as db_module
from global_state import (
    OPENAI_API_KEY_IMAGE,
    MAX_CONCURRENT_IMAGES,
    TEMP_DIR,
    get_user_edit_data,
    get_user_edit_queue,
    set_user_edit_data,
    set_user_edit_queue,
    clear_user_pending_delete,
)


# Глобальный клиент OpenAI (инициализируется один раз)
openai_client = None


def init_openai_client():
    """Инициализирует OpenAI клиент для генерации изображений."""
    global openai_client
    if not OPENAI_API_KEY_IMAGE:
        logger.error("❌ OPENAI_API_KEY_IMAGE не задан в .env")
        raise RuntimeError("OPENAI_API_KEY_IMAGE отсутствует")
    
    openai_client = OpenAIClient(api_key=OPENAI_API_KEY_IMAGE)
    logger.info("✅ OpenAI клиент инициализирован")


async def process_image_task(task_data: dict) -> dict:
    """
    Обрабатывает одну задачу генерации/редактирования изображения.

    Args:
        task_data: Словарь с полями:
            - user_id: ID пользователя
            - prompt: текстовый запрос
            - model: модель (gpt-image-2)
            - size: размер (1024x1024)
            - quality: качество (standard, low, medium, high)
            - image_paths: список путей к файлам (для редактирования)
            - operation: "генерация" или "редактирование"

    Returns:
        Словарь с результатом:
            - status: 'completed' или 'failed'
            - user_id: ID пользователя
            - error: текст ошибки (при неудаче)
    """
    user_id = task_data.get("user_id")
    prompt = task_data.get("prompt", "")
    from config import MODELS
    model = task_data.get("model") or MODELS["image"]
    size = task_data.get("size", "1024x1024")
    quality = task_data.get("quality")
    image_paths = task_data.get("image_paths", [])
    operation = task_data.get("operation", "генерация")

    if not user_id or not prompt:
        logger.error(f"Неполные данные задачи: {task_data}")
        return {
            "status": "failed",
            "error": "Недостаточно данных для обработки",
            "user_id": user_id,
        }

    logger.info(
        f"Начало обработки: user_id={user_id}, "
        f"операция={operation}, изображений={len(image_paths)}, "
        f"качество={quality}"
    )

    try:
        # Генерируем/редактируем изображение через OpenAI
        images, text_response = await openai_client.generate_image(
            image_paths=image_paths,
            prompt=prompt,
            model=model,
            n=1,
            size=size,
            quality=quality,
        )

        # Если модель вернула текстовый ответ вместо изображения
        if text_response is not None:
            await max_api.send_message(user_id, text_response)
            _cleanup_old_files(image_paths)
            # Удаляем из очереди только то, что обработано (Bug 2)
            used_set = set(image_paths)
            current_queue = get_user_edit_queue(user_id)
            remaining_queue = [p for p in current_queue if p not in used_set]
            set_user_edit_queue(user_id, remaining_queue)

            return {
                "status": "completed",
                "user_id": user_id,
            }

        # Если получены изображения
        if images is not None and len(images) > 0:
            os.makedirs(TEMP_DIR, exist_ok=True)
            new_file_path = os.path.join(TEMP_DIR, f"img_{user_id}.jpg")
            old_edit_data = get_user_edit_data(user_id)

            # Формируем подпись
            num_refs = len([p for p in image_paths if p])
            if num_refs == 0:
                prefix = "Сгенерировано по запросу: "
            elif num_refs == 1:
                prefix = "Отредактировано по запросу: "
            else:
                prefix = f"Объединено из {num_refs} изображений по запросу: "
            base_caption = prefix + prompt[:500]

            # Отправляем каждое изображение
            all_sent = True
            for i, img_bytes in enumerate(images):
                caption = base_caption if len(images) == 1 else (
                    f"{base_caption} ({i + 1}/{len(images)})"
                )
                result = await max_api.send_generated_image(
                    user_id=user_id,
                    image_bytes=img_bytes,
                    caption=caption,
                )
                if result != 200:
                    logger.error(f"Ошибка отправки изображения {i + 1}: status={result}")
                    all_sent = False

            if not all_sent:
                await max_api.send_message(
                    user_id,
                    "⚠️ Некоторые изображения созданы, но не отправлены.",
                )

            # Сохраняем последнее изображение для истории редактирования
            with open(new_file_path, "wb") as f:
                f.write(images[-1])
            last_edited = old_edit_data.get("last_image")
            set_user_edit_data(user_id, {"last_image": new_file_path})

            # Удаляем старый файл
            if last_edited and os.path.exists(last_edited):
                try:
                    os.remove(last_edited)
                except OSError:
                    pass

            # Очищаем очередь и временные файлы исходных изображений
            _cleanup_old_files(image_paths)
            # Удаляем из очереди только те пути, что были обработаны (Bug 2)
            used_set = set(image_paths)
            current_queue = get_user_edit_queue(user_id)
            remaining_queue = [p for p in current_queue if p not in used_set]
            set_user_edit_queue(user_id, remaining_queue)
            clear_user_pending_delete(user_id)

            # Логируем биллинг
            await db_module.add_billing(user_id, "image", prompt, 0, 10)

            logger.info(f"✅ Задача выполнена для user_id={user_id}, изображений={len(images)}")
            return {
                "status": "completed",
                "user_id": user_id,
            }

        # Если ни изображений, ни текста не получено
        error_msg = "Не удалось получить результат от модели"
        await max_api.send_message(user_id, f"⚠️ {error_msg}")
        return {
            "status": "failed",
            "error": error_msg,
            "user_id": user_id,
        }

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Ошибка обработки задачи: {e}", exc_info=True)

        await max_api.send_message(
            user_id,
            f"⚠️ Ошибка при генерации изображения:\n{error_msg[:200]}"
        )

        return {
            "status": "failed",
            "error": error_msg,
            "user_id": user_id,
        }


def _cleanup_old_files(image_paths: list[str]) -> None:
    """Удаляет временные файлы исходных изображений."""
    for path in image_paths:
        if path and os.path.exists(path):
            try:
                os.remove(path)
                logger.debug(f"Удалён временный файл: {path}")
            except OSError as e:
                logger.warning(f"Не удалось удалить файл {path}: {e}")


async def _handle_task(
    task: dict,
    semaphore: asyncio.Semaphore,
    queue: RedisQueue,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """
    Обрабатывает одну задачу с ограничением параллелизма (Semaphore).
    
    Запускается как asyncio.Task — не блокирует основной цикл,
    позволяя принимать новые задачи из очереди параллельно.
    """
    task_id = task.get("id", "unknown")
    task_type = task.get("type", "image_gen")

    async with semaphore:
        try:
            result = await process_image_task(task.get("data", {}))

            # publish_result синхронный (Redis) — не должен блокировать event loop
            await loop.run_in_executor(
                None,
                lambda: queue.publish_result(task_id, result, task_type="image"),
            )

            if result.get("status") == "completed":
                logger.info(
                    f"✅ Задача {task_id[:8]}... выполнена "
                    f"для user_id={result.get('user_id')}"
                )
            else:
                logger.error(
                    f"❌ Задача {task_id[:8]}... провалена: "
                    f"{result.get('error')}"
                )

        except Exception as e:
            logger.exception(
                f"Ошибка обработки задачи {task_id[:8]}...: {e}"
            )
            await loop.run_in_executor(
                None,
                lambda: queue.publish_result(
                    task_id,
                    {
                        "status": "failed",
                        "error": str(e),
                        "user_id": task.get("data", {}).get("user_id"),
                    },
                    task_type="image",
                ),
            )


async def run_worker():
    """Основной цикл воркера."""
    try:
        # Инициализация
        init_openai_client()
        queue = RedisQueue()
        logger.info(
            f"Image Worker запущен, слушаю очереди image_gen и image_edit... "
            f"Параллельных задач: {MAX_CONCURRENT_IMAGES}"
        )

        # Инициализируем БД для биллинга
        await db_module.create_database()

        semaphore = asyncio.Semaphore(MAX_CONCURRENT_IMAGES)
        active_tasks: set[asyncio.Task] = set()
        loop = asyncio.get_running_loop()

        while True:
            try:
                # dequeue() синхронный (Redis BLPOP) — выносим в thread pool,
                # чтобы не блокировать event loop во время ожидания.
                task = await loop.run_in_executor(
                    None,
                    lambda: queue.dequeue(
                        queue_types=["image_gen", "image_edit"],
                        timeout=5,
                        priority_aware=True,
                    ),
                )

                if task:
                    task_type = task.get("type", "image_gen")
                    logger.info(
                        f"📥 Получена задача {task.get('id', 'unknown')[:8]}... "
                        f"(тип: {task_type})"
                    )

                    # Запускаем обработку параллельно — не ждём завершения,
                    # сразу идём за следующей задачей.
                    # Semaphore ограничивает количество одновременных вызовов.
                    t = asyncio.create_task(
                        _handle_task(task, semaphore, queue, loop)
                    )
                    active_tasks.add(t)
                    t.add_done_callback(active_tasks.discard)

            except RedisQueueError as e:
                logger.error(f"Ошибка Redis: {e}")
                await asyncio.sleep(5)

            except Exception as e:
                logger.exception(f"Неожиданная ошибка в цикле: {e}")
                await asyncio.sleep(1)

    except KeyboardInterrupt:
        if active_tasks:
            logger.info(
                f"Получен сигнал завершения, "
                f"ожидаю {len(active_tasks)} активных задач..."
            )
            await asyncio.gather(*active_tasks, return_exceptions=True)
        logger.info("Завершение Image Worker...")
    except Exception as e:
        logger.exception(f"Критическая ошибка воркера: {e}")
        raise
    finally:
        if openai_client:
            await openai_client.close()
        logger.info("Image Worker остановлен")


if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("Запуск Image Worker")
    logger.info("=" * 50)
    asyncio.run(run_worker())
