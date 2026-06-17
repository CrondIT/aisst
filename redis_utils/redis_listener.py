"""
Слушатель результатов от воркеров.
Получает выполненные задачи из Redis
и отправляет ответы пользователям в MAX.
"""

import asyncio
import logging
import signal
import sys
from typing import Optional, Dict, Any

from .redis_queue import RedisQueue, RedisQueueError
from .redis_config import REDIS_PREFIX, LLM_TASK_TIMEOUT


# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("redis_listener.log", encoding="utf-8", mode="a"),
    ],
)
logger = logging.getLogger("redis_listener")


class RedisListener:
    """
    Слушатель результатов от воркеров.

    Мониторит статусы задач в Redis и отправляет ответы пользователям.
    """

    def __init__(self):
        """
        Инициализация слушателя.
        """
        self.queue: Optional[RedisQueue] = None
        self.running = False
        self.tasks_processed = 0
        self.tasks_failed = 0

        # Pub/Sub канал для получения уведомлений
        self.pubsub = None
        self.notification_channel = f"{REDIS_PREFIX}:notifications"

        # Флаг для graceful shutdown
        self._shutdown_requested = False

        # Регистрируем обработчики сигналов
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Обработчик сигналов завершения"""
        logger.info(f"Получен сигнал {signum}, завершаю работу...")
        self._shutdown_requested = True

    async def start(self):
        """Запуск слушателя"""
        logger.info("Инициализация слушателя результатов...")

        try:
            # Инициализация Redis
            self.queue = RedisQueue()
            logger.info("✅ Redis подключён")

            # Подписка на канал уведомлений
            self.pubsub = self.queue.redis.pubsub()
            self.pubsub.subscribe(self.notification_channel)
            logger.info(f"✅ Подписка на канал {self.notification_channel}")

            self.running = True
            logger.info("✅ Слушатель запущен")

            await self._listen_loop()

        except Exception as e:
            logger.exception(f"Критическая ошибка: {e}")
            raise
        finally:
            await self._shutdown()

    async def _listen_loop(self):
        """Основной цикл прослушивания"""
        # Словарь для отслеживания активных задач
        pending_tasks: Dict[str, Dict[str, Any]] = {}

        while self.running and not self._shutdown_requested:
            try:
                # Проверяем сообщения от Redis Pub/Sub
                message = self.pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )

                if message and message["type"] == "message":
                    # Получили уведомление о завершении задачи
                    task_info = message["data"]
                    if isinstance(task_info, bytes):
                        task_info = task_info.decode("utf-8")

                    import json

                    try:
                        data = json.loads(task_info)
                        task_id = data.get("task_id")
                        user_id = data.get("user_id")
                        task_type = data.get("task_type", "default")

                        if task_id and user_id:
                            logger.info(
                                f"📬 Получено уведомление: "
                                f"задача {task_id[:8]}..., тип={task_type}"
                            )
                            await self._process_notification(
                                task_id, user_id, task_type, data
                            )
                    except json.JSONDecodeError:
                        logger.error(
                            f"Ошибка парсинга уведомления: {task_info}"
                        )

                # Периодически проверяем завершённые задачи
                # (на случай потери уведомлений)
                await self._check_completed_tasks(pending_tasks)

                await asyncio.sleep(0.5)

            except RedisQueueError as e:
                logger.error(f"Ошибка очереди: {e}")
                await asyncio.sleep(1)
            except Exception as e:
                logger.exception(f"Неожиданная ошибка: {e}")
                await asyncio.sleep(1)

    async def _process_notification(
        self, task_id: str, user_id: int, task_type: str, data: dict
    ):
        """
        Обрабатывает уведомление в зависимости от типа задачи.

        Args:
            task_id: Идентификатор задачи
            user_id: ID пользователя
            task_type: Тип задачи (rag, image, audio, и т.д.)
            data: Полные данные уведомления
        """
        if task_type == "rag":
            await self._process_rag_result(task_id, user_id, data)
        elif task_type == "image":
            await self._process_image_result(task_id, user_id, data)
        elif task_type == "llm":
            await self._process_llm_result(task_id, user_id, data)
        else:
            # Для остальных задач используем старый метод
            await self._process_task_result(task_id, user_id)

    async def _process_rag_result(
        self, task_id: str, user_id: int, data: dict
    ):
        """
        Обрабатывает результат RAG задачи.

        Args:
            task_id: Идентификатор задачи
            user_id: ID пользователя
            data: Данные уведомления с результатом
        """
        status = data.get("status")
        result = data.get("result")
        error = data.get("error")

        if status == "completed":
            logger.info(
                f"✅ RAG задача {task_id[:8]}... выполнена для user_id={user_id}"
            )
            message = f"✅ Обработка завершена!\n{result}"
            await self._send_max_message(user_id, message)
        elif status == "failed":
            logger.error(
                f"❌ RAG задача {task_id[:8]}... провалена: {error}"
            )
            message = f"❌ Ошибка при обработке файла:\n{error}"
            await self._send_max_message(user_id, message)
        else:
            logger.warning(
                f"⚠️ Неизвестный статус RAG задачи: {status}"
            )

    async def _process_llm_result(
        self, task_id: str, user_id: int, data: dict
    ):
        """
        Обрабатывает результат LLM задачи (chat, gigachatpro, gemini).

        Args:
            task_id: Идентификатор задачи
            user_id: ID пользователя
            data: Данные уведомления с результатом
        """
        status = data.get("status")
        result = data.get("result")
        error = data.get("error")

        if status == "completed":
            logger.info(
                f"✅ LLM задача {task_id[:8]}... выполнена для user_id={user_id}"
            )
            await self._send_max_message(user_id, result)
            self.tasks_processed += 1
        elif status == "failed":
            logger.error(
                f"❌ LLM задача {task_id[:8]}... провалена: {error}"
            )
            message = f"❌ Ошибка обработки запроса:\n{error[:500]}"
            await self._send_max_message(user_id, message)
            self.tasks_failed += 1
        elif status == "timeout":
            await self._send_max_message(
                user_id,
                "⏱️ Превышено время ожидания ответа. Попробуйте ещё раз."
            )
            self.tasks_failed += 1
        else:
            logger.warning(
                f"⚠️ Неизвестный статус LLM задачи: {status}"
            )

    async def _process_image_result(
        self, task_id: str, user_id: int, data: dict
    ):
        """
        Обрабатывает результат задачи генерации изображения.

        Args:
            task_id: Идентификатор задачи
            user_id: ID пользователя
            data: Данные уведомления с результатом
        """
        status = data.get("status")
        error = data.get("error")

        if status == "completed":
            logger.info(
                f"✅ Image задача {task_id[:8]}... выполнена для user_id={user_id}"
            )
            # Изображение уже отправлено воркером через send_generated_image()
            # Дополнительное уведомление не требуется
            # Можно добавить, если нужно:
            # await self._send_max_message(user_id, "✅ Изображение готово!")
        elif status == "failed":
            logger.error(
                f"❌ Image задача {task_id[:8]}... провалена: {error}"
            )
            # Воркер уже отправил сообщение об ошибке пользователю
            # но можем продублировать для надёжности:
            # message = f"❌ Ошибка генерации изображения:\n{error[:200]}"
            # await self._send_max_message(user_id, message)
        else:
            logger.warning(
                f"⚠️ Неизвестный статус Image задачи: {status}"
            )

    async def _send_max_message(self, user_id: int, text: str, format: str | None = None):
        """
        Отправляет сообщение пользователю через MAX API.
        Использует httpx напрямую, без зависимости от max_api модуля.

        Args:
            user_id: ID пользователя
            text: Текст сообщения
            format: Формат сообщения ("markdown", "html" или None для plain text)
        """
        import httpx

        from global_state import MAX_API_TOKEN, MAX_BASE_URL

        url = f"{MAX_BASE_URL}/messages"
        headers = {
            "Authorization": MAX_API_TOKEN,
            "Content-Type": "application/json"
        }
        params = {"user_id": user_id}
        payload = {"text": text}
        if format in ("markdown", "html"):
            payload["format"] = format

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url, headers=headers, params=params, json=payload
                )
                if response.status_code == 200:
                    logger.info(f"Сообщение отправлено user_id={user_id}")
                else:
                    logger.error(
                        f"Ошибка отправки сообщения: "
                        f"{response.status_code} — {response.text}"
                    )
        except Exception as e:
            logger.error(f"Исключение при отправке сообщения: {e}")

    async def _process_task_result(self, task_id: str, user_id: int):
        """
        Обрабатывает результат задачи и отправляет ответ пользователю.

        Args:
            task_id: Идентификатор задачи
            user_id: ID пользователя
        """
        try:
            # Получаем статус задачи
            status = self.queue.get_task_status(task_id)

            if status == RedisQueue.STATUS_COMPLETED:
                # Получаем результат
                result_data = self.queue.get_task_result(task_id)
                result = result_data.get("result") if result_data else None
                result_format = result_data.get("format") if result_data else None

                # Отправляем ответ пользователю
                await self._send_max_message(user_id, result, format=result_format)

                self.tasks_processed += 1
                logger.info(
                    f"✅ Задача {task_id} обработана для юзера {user_id}"
                )

            elif status == RedisQueue.STATUS_FAILED:
                # Получаем ошибку
                error = self.queue.redis.get(
                    f"{self.queue.prefix}:task:{task_id}:error"
                )

                # Уведомляем пользователя об ошибке
                await self._send_max_message(
                    user_id, f"❌ Ошибка: {error or 'Неизвестная ошибка'}"
                )

                self.tasks_failed += 1
                logger.error(f"❌ Задача {task_id} не выполнена: {error}")

            elif status == RedisQueue.STATUS_TIMEOUT:
                await self._send_max_message(
                    user_id, "❌ Превышено время ожидания ответа"
                )
                self.tasks_failed += 1
                logger.warning(f"⏱️ Задача {task_id} превысила время ожидания")

        except Exception as e:
            logger.exception(
                f"Ошибка обработки результата задачи {task_id}: {e}"
            )

    
    async def _check_completed_tasks(self, pending_tasks: dict):
        """
        Периодически проверяет завершённые задачи.

        Args:
            pending_tasks: Словарь активных задач {task_id: user_id}
        """
        # Эта функция может быть расширена для проверки
        # завершённых задач из persistence слоя
        pass

    async def _shutdown(self):
        """Корректное завершение работы"""
        logger.info("Завершение работы слушателя...")
        self.running = False

        if self.pubsub:
            self.pubsub.unsubscribe(self.notification_channel)
            self.pubsub.close()

        if self.queue:
            self.queue.close()

        # Логируем статистику
        logger.info(
            f"📊 Статистика: обработано={self.tasks_processed}, "
            f"ошибок={self.tasks_failed}"
        )

        logger.info("👋 Слушатель остановлен")


async def run_listener():
    """Точка входа для запуска слушателя"""
    listener = RedisListener()
    await listener.start()


def main():
    """Основная функция для запуска из командной строки"""
    try:
        asyncio.run(run_listener())
    except KeyboardInterrupt:
        logger.info("Получен сигнал завершения")


if __name__ == "__main__":
    main()
