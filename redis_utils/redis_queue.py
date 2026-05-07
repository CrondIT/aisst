"""
Модуль для работы с очередями задач в Redis.
Обеспечивает надёжное хранение и передачу задач между ботом и воркерами.
"""

import redis
import json
import uuid
import logging
from typing import Optional, Dict, Any, List
from redis_config import (
    REDIS_CONFIG,
    REDIS_PREFIX,
    REDIS_TTL,
    QUEUE_CONFIG,
    REDIS_RETRY_ON_TIMEOUT,
    REDIS_MAX_RETRIES,
    REDIS_SOCKET_TIMEOUT,
    REDIS_SOCKET_CONNECT_TIMEOUT,
)

logger = logging.getLogger(__name__)


class RedisQueueError(Exception):
    """Исключение для ошибок Redis очереди"""

    pass


class RedisQueue:
    """
    Класс для управления очередями задач в Redis.

    Поддерживает:
    - Несколько очередей по типам задач
    - Приоритетные очереди (high, normal, low)
    - Отслеживание статуса задач
    - Хранение состояния пользователей
    - Rate limiting
    """

    # Статусы задач
    STATUS_PENDING = "pending"
    STATUS_PROCESSING = "processing"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_TIMEOUT = "timeout"

    def __init__(self, config: Optional[Dict] = None):
        """
        Инициализация подключения к Redis.

        Args:
            config: Опциональная конфигурация для переопределения настроек
        """
        self.config = {**REDIS_CONFIG, **(config or {})}
        self.prefix = REDIS_PREFIX

        # Создаём пул подключений с настройками retry
        retry_config = {}
        if REDIS_RETRY_ON_TIMEOUT:
            from redis.retry import Retry
            from redis.backoff import ExponentialBackoff

            retry_config["retry"] = Retry(
                ExponentialBackoff(), REDIS_MAX_RETRIES
            )
            retry_config["retry_on_timeout"] = True

        try:
            # Пробуем подключение с указанной базой
            self.redis = redis.Redis(
                host=self.config["host"],
                port=self.config["port"],
                db=self.config["db"],
                password=self.config["password"],
                ssl=self.config["ssl"],
                decode_responses=True,
                socket_timeout=REDIS_SOCKET_TIMEOUT,
                socket_connect_timeout=REDIS_SOCKET_CONNECT_TIMEOUT,
                **retry_config,
            )
            # Проверяем подключение
            self.redis.ping()
            logger.info(
                f"Подкл. к Redis: {self.config['host']}:{self.config['port']}"
            )
        except redis.exceptions.ResponseError as e:
            # Обрабатываем ошибку "DB index is out of range"
            error_msg = str(e)
            if "db index is out of range" in error_msg.lower():
                logger.warning(
                    f"⚠️ Redis не поддерживает выбор базы (DB={self.config['db']}). "
                    "Пробуем без указания db..."
                )
                # Пробуем без указания db (по умолчанию 0)
                self.redis = redis.Redis(
                    host=self.config["host"],
                    port=self.config["port"],
                    password=self.config["password"],
                    ssl=self.config["ssl"],
                    decode_responses=True,
                    socket_timeout=REDIS_SOCKET_TIMEOUT,
                    socket_connect_timeout=REDIS_SOCKET_CONNECT_TIMEOUT,
                    **retry_config,
                )
                self.redis.ping()
                logger.info(
                    f"Подкл. к Redis: {self.config['host']}:{self.config['port']} (db=0 по умолчанию)"
                )
            else:
                logger.error(f"❌ Ошибка подключения к Redis: {e}")
                raise RedisQueueError(f"Ошибка инициализации Redis: {e}")
        except redis.ConnectionError as e:
            logger.error(f"❌ Ошибка подключения к Redis: {e}")
            raise RedisQueueError(f"Не удалось подключиться к Redis: {e}")
        except Exception as e:
            logger.error(f"❌ Неожиданная ошибка при подключении к Redis: {e}")
            raise RedisQueueError(f"Ошибка инициализации Redis: {e}")

    def _make_key(self, *parts: str) -> str:
        """Создаёт полный ключ Redis с префиксом"""
        return f"{self.prefix}:{':'.join(str(p) for p in parts)}"

    # ==================== Работа с очередями ====================

    def enqueue(
        self,
        queue_type: str,
        task_data: Dict[str, Any],
        priority: str = "normal",
    ) -> str:
        """
        Добавляет задачу в очередь.

        Args:
            queue_type: Тип очереди (chat, file, image:gen, image:edit)
            task_data: Данные задачи
            priority: Приоритет (high, normal, low)

        Returns:
            task_id: Уникальный идентификатор задачи
        """
        task_id = str(uuid.uuid4())

        task = {
            "id": task_id,
            "type": queue_type,
            "data": task_data,
            "priority": priority,
            "created_at": self._get_timestamp(),
        }

        task_json = json.dumps(task, ensure_ascii=False)

        # Определяем имя очереди в зависимости от приоритета
        if priority == "high":
            queue_name = self._make_key("queue", QUEUE_CONFIG["high_priority"])
        elif priority == "low":
            queue_name = self._make_key("queue", QUEUE_CONFIG["low_priority"])
        else:
            queue_config_key = queue_type.replace(":", "_")
            queue_name = self._make_key(
                "queue", QUEUE_CONFIG.get(queue_config_key, queue_type)
            )

        # Добавляем задачу в очередь (LPUSH для LIFO)
        self.redis.lpush(queue_name, task_json)

        # Инициализируем статус задачи
        self._set_task_status(task_id, self.STATUS_PENDING)

        # Увеличиваем счётчик задач в очереди
        self.redis.incr(self._make_key("stats", "total_tasks"))

        logger.debug(f"📝 Задача {task_id} добавлена в очередь {queue_name}")
        return task_id

    def dequeue(
        self,
        queue_types: Optional[List[str]] = None,
        timeout: int = 0,
        priority_aware: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """
        Получает задачу из очереди.

        Args:
            queue_types: Список типов очередей для опроса
            timeout: Таймаут блокировки в секундах (0 = без блокировки)
            priority_aware: Учитывать ли приоритетные очереди

        Returns:
            task: Данные задачи или None если очередь пуста
        """
        if queue_types is None:
            queue_types = ["chat", "file", "image:gen", "image:edit"]

        # Формируем список очередей для опроса
        queues = []

        # Сначала проверяем высокоприоритетную очередь
        if priority_aware:
            queues.append(
                self._make_key("queue", QUEUE_CONFIG["high_priority"])
            )

        # Добавляем обычные очереди
        for q_type in queue_types:
            queue_config_key = q_type.replace(":", "_")
            queues.append(
                self._make_key(
                    "queue", QUEUE_CONFIG.get(queue_config_key, q_type)
                )
            )

        # Добавляем низкоприоритетную очередь в конце
        if priority_aware:
            queues.append(
                self._make_key("queue", QUEUE_CONFIG["low_priority"])
            )

        try:
            if timeout > 0:
                # Блокирующее получение (BLPOP)
                result = self.redis.blpop(queues, timeout)
            else:
                # Неблокирующее получение из первой непустой очереди
                result = None
                for queue in queues:
                    result = self.redis.rpop(queue)
                    if result:
                        result = (queue, result)
                        break

            if result:
                _, task_json = result
                task = json.loads(task_json)

                # Обновляем статус задачи
                self._set_task_status(task["id"], self.STATUS_PROCESSING)
                self.redis.set(
                    self._make_key("task", task["id"], "worker_started_at"),
                    self._get_timestamp(),
                )

                logger.debug(f"⚙️ Задача {task['id']} получена из очереди")
                return task

            return None

        except redis.RedisError as e:
            logger.error(f"❌ Ошибка получения задачи из очереди: {e}")
            return None

    def get_queue_size(self, queue_type: str) -> int:
        """Возвращает размер очереди"""
        queue_config_key = queue_type.replace(":", "_")
        queue_name = self._make_key(
            "queue", QUEUE_CONFIG.get(queue_config_key, queue_type)
        )
        return self.redis.llen(queue_name)

    def get_all_queue_sizes(self) -> Dict[str, int]:
        """Возвращает размеры всех очередей"""
        sizes = {}
        for queue_type in [
            "chat",
            "file",
            "image:gen",
            "image:edit",
            "high",
            "low",
        ]:
            sizes[queue_type] = self.get_queue_size(queue_type)
        return sizes

    # ==================== Статус задач ====================

    def _set_task_status(self, task_id: str, status: str):
        """Внутренний метод установки статуса задачи"""
        key = self._make_key("task", task_id, "status")
        self.redis.set(key, status)
        self.redis.expire(key, REDIS_TTL["task_result"])

    def set_task_completed(self, task_id: str, result: Any):
        """
        Отмечает задачу как выполненную с результатом.

        Args:
            task_id: Идентификатор задачи
            result: Результат выполнения (сериализуемый в JSON)
        """
        pipe = self.redis.pipeline()

        # Устанавливаем статус
        pipe.set(
            self._make_key("task", task_id, "status"), self.STATUS_COMPLETED
        )

        # Сохраняем результат
        if result is not None:
            try:
                result_json = json.dumps(
                    result, ensure_ascii=False, default=str
                )
                pipe.set(
                    self._make_key("task", task_id, "result"), result_json
                )
            except (TypeError, ValueError) as e:
                logger.error(
                    f"Ошибка сериализации результата задачи {task_id}: {e}"
                )
                pipe.set(
                    self._make_key("task", task_id, "result"),
                    json.dumps(
                        {
                            "error": "Result serialization failed",
                            "raw": str(result),
                        }
                    ),
                )

        # Устанавливаем TTL
        pipe.expire(
            self._make_key("task", task_id, "status"), REDIS_TTL["task_result"]
        )
        pipe.expire(
            self._make_key("task", task_id, "result"), REDIS_TTL["task_result"]
        )

        pipe.execute()
        logger.debug(f"✅ Задача {task_id} помечена как выполненная")

    def set_task_failed(
        self, task_id: str, error: str, exc_info: bool = False
    ):
        """
        Отмечает задачу как неудачную.

        Args:
            task_id: Идентификатор задачи
            error: Сообщение об ошибке
            exc_info: Логировать ли traceback
        """
        pipe = self.redis.pipeline()

        pipe.set(self._make_key("task", task_id, "status"), self.STATUS_FAILED)
        pipe.set(self._make_key("task", task_id, "error"), error)
        pipe.expire(
            self._make_key("task", task_id, "status"), REDIS_TTL["task_result"]
        )
        pipe.expire(
            self._make_key("task", task_id, "error"), REDIS_TTL["task_result"]
        )

        pipe.execute()

        if exc_info:
            logger.exception(f"❌ Задача {task_id} не выполнена: {error}")
        else:
            logger.error(f"❌ Задача {task_id} не выполнена: {error}")

    def get_task_status(self, task_id: str) -> Optional[str]:
        """Получает статус задачи"""
        status = self.redis.get(self._make_key("task", task_id, "status"))
        return status

    def get_task_result(
        self, task_id: str, wait: bool = False, timeout: int = 30
    ) -> Optional[Any]:
        """
        Получает результат выполнения задачи.

        Args:
            task_id: Идентификатор задачи
            wait: Ждать ли завершения задачи
            timeout: Максимальное время ожидания в секундах

        Returns:
            Результат задачи или None
        """
        import time

        start_time = time.time()

        while True:
            status = self.get_task_status(task_id)

            if status == self.STATUS_COMPLETED:
                result_json = self.redis.get(
                    self._make_key("task", task_id, "result")
                )
                if result_json:
                    return json.loads(result_json)
                return None

            elif status == self.STATUS_FAILED:
                error = self.redis.get(
                    self._make_key("task", task_id, "error")
                )
                raise RedisQueueError(f"Задача не выполнена: {error}")

            elif status == self.STATUS_TIMEOUT:
                raise RedisQueueError(
                    f"Задача {task_id} превысила время ожидания"
                )

            if not wait:
                return None

            # Проверяем таймаут ожидания
            if time.time() - start_time > timeout:
                # Помечаем задачу как timeout
                self.redis.set(
                    self._make_key("task", task_id, "status"),
                    self.STATUS_TIMEOUT,
                )
                raise RedisQueueError(
                    f"Превышено время ожидания результата ({timeout}с)"
                )

            time.sleep(0.5)

    # ==================== Состояние пользователей ====================

    def set_user_state(
        self, user_id: int, key: str, value: Any, ttl: Optional[int] = None
    ):
        """
        Сохраняет состояние пользователя.

        Args:
            user_id: ID пользователя
            key: Ключ состояния (mode, context, files, edit)
            value: Значение (сериализуемое в JSON)
            ttl: Время жизни в секундах (по умолчанию из конфигурации)
        """
        if ttl is None:
            ttl = REDIS_TTL.get(f"user_{key}", REDIS_TTL["user_context"])

        try:
            value_json = json.dumps(value, ensure_ascii=False, default=str)
            self.redis.setex(
                self._make_key("user", str(user_id), key), ttl, value_json
            )
        except (TypeError, ValueError) as e:
            logger.error(
                f"Ошибка сериализации состояния .pthf {user_id}/{key}: {e}"
            )
            raise RedisQueueError(f"Ошибка сохранения состояния: {e}")

    def get_user_state(self, user_id: int, key: str) -> Optional[Any]:
        """
        Получает состояние пользователя.

        Args:
            user_id: ID пользователя
            key: Ключ состояния

        Returns:
            Значение состояния или None
        """
        data = self.redis.get(self._make_key("user", str(user_id), key))
        if data:
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                logger.error(
                    f"Ошибка десериализации состояния юзера {user_id}/{key}"
                )
                return None
        return None

    def delete_user_state(self, user_id: int, *keys: str):
        """Удаляет состояние пользователя по ключам"""
        if not keys:
            # Удаляем все ключи пользователя
            pattern = self._make_key("user", str(user_id), "*")
            pipe = self.redis.pipeline()
            for key in self.redis.scan_iter(match=pattern):
                pipe.delete(key)
            pipe.execute()
        else:
            pipe = self.redis.pipeline()
            for key in keys:
                pipe.delete(self._make_key("user", str(user_id), key))
            pipe.execute()

    def get_all_user_states(self, user_id: int) -> Dict[str, Any]:
        """Получает все состояния пользователя"""
        pattern = self._make_key("user", str(user_id), "*")
        states = {}

        for key in self.redis.scan_iter(match=pattern):
            # Извлекаем подключ после user:{user_id}:
            short_key = key.split(":", 2)[-1]
            data = self.redis.get(key)
            if data:
                try:
                    states[short_key] = json.loads(data)
                except json.JSONDecodeError:
                    states[short_key] = data

        return states

    # ==================== Rate Limiting ====================

    def check_rate_limit(
        self,
        user_id: int,
        action: str,
        max_requests: int,
        window_seconds: int = 60,
    ) -> bool:
        """
        Проверяет, не превышен ли лимит запросов.

        Args:
            user_id: ID пользователя
            action: Тип действия (chat, image, etc.)
            max_requests: Максимальное количество запросов в окно
            window_seconds: Размер окна в секундах

        Returns:
            True если запрос разрешён, False если превышен лимит
        """
        key = self._make_key("ratelimit", str(user_id), action)

        current = self.redis.incr(key)

        if current == 1:
            # Первый запрос, устанавливаем TTL
            self.redis.expire(key, window_seconds)

        return current <= max_requests

    def get_rate_limit_remaining(
        self,
        user_id: int,
        action: str,
        max_requests: int,
        window_seconds: int = 60,
    ) -> int:
        """Возвращает количество оставшихся запросов"""
        key = self._make_key("ratelimit", str(user_id), action)
        current = self.redis.get(key)

        if current is None:
            return max_requests

        return max(0, max_requests - int(current))

    # ==================== Утилиты ====================

    def _get_timestamp(self) -> str:
        """Возвращает текущую метку времени в формате ISO"""
        from datetime import datetime

        return datetime.utcnow().isoformat()

    def clear_queue(self, queue_type: str):
        """Очищает очередь полностью"""
        queue_config_key = queue_type.replace(":", "_")
        queue_name = self._make_key(
            "queue", QUEUE_CONFIG.get(queue_config_key, queue_type)
        )
        self.redis.delete(queue_name)
        logger.info(f"🗑️ Очередь {queue_type} очищена")

    def get_stats(self) -> Dict[str, Any]:
        """
        Получает статистику по очередям и задачам.

        Returns:
            Dict со статистикой
        """
        stats = {
            "queues": self.get_all_queue_sizes(),
            "total_tasks": int(
                self.redis.get(self._make_key("stats", "total_tasks")) or 0
            ),
            "redis_info": {},
        }

        try:
            info = self.redis.info("stats")
            stats["redis_info"] = {
                "connected_clients": info.get("connected_clients"),
                "used_memory_human": info.get("used_memory_human"),
                "total_commands_processed": info.get(
                    "total_commands_processed"
                ),
            }
        except Exception as e:
            logger.warning(f"Не удалось получить статистику Redis: {e}")

        return stats

    def close(self):
        """Закрывает подключение к Redis"""
        if self.redis:
            self.redis.close()
            logger.info("🔌 Подключение к Redis закрыто")


# ==================== Глобальный экземпляр ====================

_queue_instance: Optional[RedisQueue] = None


def get_queue() -> RedisQueue:
    """
    Получает глобальный экземпляр очереди (singleton).
    Создаёт подключение при первом вызове.
    """
    global _queue_instance
    if _queue_instance is None:
        _queue_instance = RedisQueue()
    return _queue_instance


def close_queue():
    """Закрывает глобальное подключение к Redis"""
    global _queue_instance
    if _queue_instance:
        _queue_instance.close()
        _queue_instance = None
