# Организация очереди сообщений через Redis

## Обзор архитектуры

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Telegram Bot  │───▶│   Redis Queue    │────▶│    Worker(s)    │
│   (bot.py)      │     │  (redis_queue.py)│     │   (worker.py)   │
└─────────────────┘     └──────────────────┘     └─────────────────┘
         ▲                                              │
         │                                              ▼
         │                                      ┌─────────────────┐
         │                                      │   AI APIs       │
         │                                      │ (OpenAI/Gemini) │
         │                                      └─────────────────┘
         │
         │     ┌──────────────────┐
         └─────│  Redis Listener  │
               │(redis_listener.py)│
               └──────────────────┘
```

## Компоненты

### 1. `redis_config.py`
Конфигурация подключения к Redis и настройки очередей.

### 2. `redis_queue.py`
Основной модуль для работы с очередями:
- `RedisQueue.enqueue()` — добавление задачи
- `RedisQueue.dequeue()` — получение задачи
- `RedisQueue.set_user_state()` — хранение состояния пользователей
- `RedisQueue.check_rate_limit()` — ограничение частоты запросов

### 3. `worker.py`
Воркер для обработки задач из очередей:
- Обработка задач чата (`chat`)
- Обработка задач анализа файлов (`file`)
- Генерация изображений (`image:gen`)
- Редактирование изображений (`image:edit`)

### 4. `redis_listener.py`
Слушатель результатов от воркеров:
- Получение уведомлений о завершении задач
- Отправка ответов пользователям в Telegram

### 5. `global_state.py` (обновлён)
Функции-обёртки для прозрачной работы с Redis:
- `get_user_context()`, `set_user_context()`
- `get_user_mode()`, `set_user_mode()`
- `enqueue_task()`, `get_task_result()`

## Установка

### 1. Установите зависимости

```bash
pip install -r requirements.txt
```

### 2. Настройте переменные окружения

Скопируйте `.env.example` в `.env` и настройте:

```env
# Включить Redis очереди
USE_REDIS=true

# Адрес Redis сервера (на отдельном сервере)
REDIS_HOST=192.168.1.100
REDIS_PORT=6379
REDIS_PASSWORD=your_redis_password
REDIS_SSL=false

# Префикс для ключей
REDIS_PREFIX=bot

# TTL для данных (секунды)
REDIS_TTL_USER_CONTEXT=3600
REDIS_TTL_TASK_RESULT=300
```

## Запуск

### Вариант 1: Без Redis (локальный режим)

```bash
# В .env установите:
USE_REDIS=false

# Запустите бота
python bot.py
```

### Вариант 2: С Redis (распределённый режим)

#### Шаг 1: Запустите воркеры

```bash
# Один воркер
python worker.py --id 0

# Несколько воркеров (например, 4)
python worker.py --workers 4

# Или в фоновом режиме (Linux)
nohup python worker.py --id 0 > worker0.log 2>&1 &
nohup python worker.py --id 1 > worker1.log 2>&1 &
```

#### Шаг 2: Запустите слушатель результатов

```bash
python redis_listener.py
```

#### Шаг 3: Запустите бота

```bash
python bot.py
```

## Структура очередей в Redis

```
bot:queue:chat           # Очередь задач чата
bot:queue:file           # Очередь анализа файлов
bot:queue:image:gen      # Очередь генерации изображений
bot:queue:image:edit     # Очередь редактирования изображений
bot:queue:high           # Высокоприоритетные задачи
bot:queue:low            # Низкоприоритетные задачи

bot:task:{id}:status     # Статус задачи
bot:task:{id}:result     # Результат задачи
bot:task:{id}:error      # Ошибка задачи

bot:user:{id}:mode       # Режим пользователя
bot:user:{id}:context_chat    # Контекст чата
bot:user:{id}:context_file    # Контекст анализа файлов
bot:user:{id}:files      # Данные о файлах
bot:user:{id}:edit       # Данные редактирования

bot:ratelimit:{id}:chat  # Rate limiting
```

## Пример использования в коде

### Отправка задачи в очередь

```python
from global_state import enqueue_task, get_task_result

# Добавляем задачу в очередь
task_id = enqueue_task('chat', {
    'user_id': user_id,
    'message': user_message,
    'context': user_contexts[user_id]['chat'],
    'enable_web_search': False,
}, priority='normal')

# Ждём результат (синхронно, с таймаутом 30 секунд)
try:
    result = get_task_result(task_id, wait=True, timeout=30)
    response = result['result']['response']
    await update.message.reply_text(response)
except Exception as e:
    await update.message.reply_text(f"Ошибка: {e}")
```

### Проверка rate limit

```python
from global_state import check_rate_limit

if not check_rate_limit(user_id, 'chat', max_requests=10, window_seconds=60):
    await update.message.reply_text(
        "⚠️ Слишком много запросов. Попробуйте через минуту."
    )
    return
```

### Сохранение состояния пользователя

```python
from global_state import set_user_context, get_user_context

# Сохранение
set_user_context(user_id, 'chat', [
    {"role": "system", "content": "You are helpful."},
    {"role": "user", "content": "Hello"},
    {"role": "assistant", "content": "Hi there!"},
])

# Получение
context = get_user_context(user_id, 'chat')
```

## Мониторинг

### Получение статистики очередей

```python
from global_state import get_queue_stats

stats = get_queue_stats()
print(stats)
# {
#     'queues': {'chat': 5, 'file': 2, 'image:gen': 0, ...},
#     'total_tasks': 1234,
#     'redis_info': {...}
# }
```

### Логирование

Все компоненты логируют события в файлы:
- `worker.log` — логи воркеров
- `redis_listener.log` — логи слушателя
- Стандартный вывод — логи бота

## Масштабирование

### Запуск нескольких воркеров

```bash
# На одном сервере
python worker.py --workers 8

# На нескольких серверах
# Сервер 1
python worker.py --id 0 --workers 4

# Сервер 2
python worker.py --id 4 --workers 4
```

### Приоритизация задач

```python
# Высокий приоритет (платные пользователи)
enqueue_task('chat', data, priority='high')

# Обычный приоритет
enqueue_task('chat', data, priority='normal')

# Низкий приоритет (фоновые задачи)
enqueue_task('chat', data, priority='low')
```

## Отказоустойчивость

### Настройки retry

```env
REDIS_RETRY_ON_TIMEOUT=true
REDIS_MAX_RETRIES=3
REDIS_SOCKET_TIMEOUT=5
```

### Graceful shutdown

Все компоненты поддерживают корректное завершение:
- Обработка сигналов SIGINT/SIGTERM
- Завершение текущих задач
- Закрытие подключений

## Рекомендации по Redis

### Минимальная конфигурация Redis сервера

```bash
# redis.conf
maxmemory 256mb
maxmemory-policy allkeys-lru
appendonly yes
appendfsync everysec
```

### Мониторинг Redis

```bash
# Подключиться к Redis CLI
redis-cli -h <host> -p <port> -a <password>

# Проверить размер базы
redis-cli INFO memory

# Проверить размеры очередей
redis-cli LLEN bot:queue:chat
redis-cli LLEN bot:queue:file

# Посмотреть ключи
redis-cli KEYS "bot:user:*"
```

## Troubleshooting

### Ошибка подключения к Redis

```
RedisQueueError: Не удалось подключиться к Redis
```

**Решение:**
1. Проверьте доступность сервера: `telnet <host> <port>`
2. Проверьте пароль в `.env`
3. Убедитесь, что Redis слушает правильный интерфейс

### Задачи не обрабатываются

**Проверка:**
```bash
# Размер очереди
redis-cli LLEN bot:queue:chat

# Статус задачи
redis-cli GET bot:task:<task_id>:status
```

### Потеря контекста пользователей

**Решение:** Увеличьте TTL в `.env`:
```env
REDIS_TTL_USER_CONTEXT=7200  # 2 часа вместо 1
```

## Миграция с локального хранения

Для постепенной миграции:

1. Установите `USE_REDIS=true`
2. Запустите воркеры и слушатель
3. Бот будет автоматически использовать Redis для новых пользователей
4. Для существующих пользователей произойдёт fallback к памяти

## Безопасность

1. Используйте пароль для Redis
2. Включите SSL для подключения к Redis
3. Ограничьте доступ к Redis по IP
4. Не храните чувствительные данные в очередях
