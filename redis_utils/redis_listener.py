"""
Слушатель результатов от воркеров.
Получает выполненные задачи из Redis
и отправляет ответы пользователям в Telegram.
"""

import asyncio
import logging
import signal
import sys
import os
from typing import Optional, Dict, Any

from telegram import Bot
from telegram.error import TelegramError

from redis_queue import RedisQueue, RedisQueueError
from redis_config import REDIS_PREFIX
import dbbot
from message_utils import truncate_caption

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

    def __init__(self, bot_token: str):
        """
        Инициализация слушателя.

        Args:
            bot_token: Токен Telegram бота
        """
        self.bot_token = bot_token
        self.bot: Optional[Bot] = None
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
            # Инициализация бота
            self.bot = Bot(token=self.bot_token)
            await self.bot.get_me()  # Проверяем токен
            logger.info("✅ Telegram бот инициализирован")

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

                        if task_id and user_id:
                            logger.info(
                                f"📬 Получено уведомление: "
                                f"задача {task_id} для пользователя {user_id}"
                            )
                            await self._process_task_result(task_id, user_id)
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

                # Отправляем ответ пользователю
                await self._send_response(user_id, result)

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
                await self._send_error(user_id, error or "Неизвестная ошибка")

                self.tasks_failed += 1
                logger.error(f"❌ Задача {task_id} не выполнена: {error}")

            elif status == RedisQueue.STATUS_TIMEOUT:
                await self._send_error(
                    user_id, "Превышено время ожидания ответа"
                )
                self.tasks_failed += 1
                logger.warning(f"⏱️ Задача {task_id} превысила время ожидания")

        except Exception as e:
            logger.exception(
                f"Ошибка обработки результата задачи {task_id}: {e}"
            )

    async def _send_response(self, user_id: int, result: Dict[str, Any]):
        """
        Отправляет ответ пользователю в Telegram.

        Args:
            user_id: ID пользователя
            result: Результат выполнения задачи
        """
        if not result:
            await self._send_error(user_id, "Пустой результат")
            return

        response_text = result.get("response")
        image_url = result.get("image_url")
        edited_image_path = result.get("edited_image_path")
        processing_time = result.get("processing_time", 0)

        try:
            if image_url:
                # Отправляем изображение
                caption = truncate_caption(
                    f"Готово за {processing_time:.1f}с",
                    prefix="✅ "
                )
                await self.bot.send_photo(
                    chat_id=user_id,
                    photo=image_url,
                    caption=caption,
                )
            elif edited_image_path and os.path.exists(edited_image_path):
                # Отправляем отредактированное изображение
                caption = truncate_caption(
                    f"Готово за {processing_time:.1f}с",
                    prefix="✅ "
                )
                with open(edited_image_path, "rb") as f:
                    await self.bot.send_photo(
                        chat_id=user_id,
                        photo=f,
                        caption=caption,
                    )
            elif response_text:
                # Отправляем текст
                from message_utils import send_long_message

                # Создаём фейковый update для send_long_message
                class FakeMessage:
                    async def reply_text(self, text, parse_mode=None):
                        await self.bot.send_message(
                            chat_id=user_id, text=text, parse_mode=parse_mode
                        )

                class FakeUpdate:
                    def __init__(self):
                        self.message = FakeMessage()

                await send_long_message(FakeUpdate(), response_text)
            else:
                # Неизвестный формат результата
                await self.bot.send_message(
                    chat_id=user_id,
                    text=f"✅ Задача выполнена за {processing_time:.1f}с",
                )

        except TelegramError as e:
            logger.error(f"Ошибка отправки ответа пользователю {user_id}: {e}")

            # Логируем в базу данных
            dbbot.log_action(
                user_id,
                "system",
                f"Ошибка отправки ответа: {e}",
                0,
                0,
                "error",
                "redis_listener>_send_response",
            )

    async def _send_error(self, user_id: int, error_message: str):
        """
        Отправляет сообщение об ошибке пользователю.

        Args:
            user_id: ID пользователя
            error_message: Текст ошибки
        """
        try:
            # Сокращаем сообщение об ошибке
            if len(error_message) > 500:
                error_message = error_message[:500] + "..."

            await self.bot.send_message(
                chat_id=user_id,
                text=f"❌ Ошибка при обработке запроса:\n{error_message}",
            )

            # Логируем в базу данных
            dbbot.log_action(
                user_id,
                "system",
                f"Ошибка обработки задачи: {error_message}",
                0,
                0,
                "error",
                "redis_listener>_send_error",
            )

        except TelegramError as e:
            logger.error(f"Ошибка отправки ошибки пользователю {user_id}: {e}")

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

        if self.bot:
            await self.bot.session.close()

        # Логируем статистику
        logger.info(
            f"📊 Статистика: обработано={self.tasks_processed}, "
            f"ошибок={self.tasks_failed}"
        )

        logger.info("👋 Слушатель остановлен")


async def run_listener(bot_token: str):
    """Точка входа для запуска слушателя"""
    listener = RedisListener(bot_token)
    await listener.start()


def main():
    """Основная функция для запуска из командной строки"""
    import argparse
    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Слушатель результатов от воркеров"
    )
    parser.add_argument(
        "--token",
        type=str,
        default=os.getenv("TELEGRAM_TOKEN2"),
        help="Токен Telegram бота",
    )

    args = parser.parse_args()

    if not args.token:
        logger.error("Не указан токен Telegram бота")
        sys.exit(1)

    try:
        asyncio.run(run_listener(args.token))
    except KeyboardInterrupt:
        logger.info("Получен сигнал завершения")


if __name__ == "__main__":
    main()
