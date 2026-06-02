# Инструкция по развёртыванию Image Worker

## ✅ Что было сделано

Реализована Redis-очередь для генерации изображений для устранения проблемы дублирующихся webhook'ов от MAX API.

### Изменённые файлы:
- `redis_utils/redis_config.py` — добавлены очереди `image_gen` и `image_edit`
- `redis_utils/redis_queue.py` — параметризован `task_type` в `publish_result()`
- `handlers/image_handler.py` — ставит задачу в очередь вместо прямого вызова OpenAI
- `redis_utils/redis_listener.py` — обработка уведомлений `task_type="image"`
- `start.sh` — добавлен запуск Image Worker
- `aisst.service` — добавлен Image Worker в systemd

### Новые файлы:
- `image_worker.py` — воркер для асинхронной генерации изображений

---

## 🚀 Развёртывание на сервере

### Шаг 1: Загрузить изменения на сервер

```bash
# На локальной машине (если используете git)
git add .
git commit -m "Добавлена Redis-очередь для генерации изображений"
git push

# На сервере
cd /root/aisst
git pull
```

Или через scp:
```bash
scp image_worker.py root@<server>:/root/aisst/
scp handlers/image_handler.py root@<server>:/root/aisst/handlers/
scp redis_utils/redis_config.py root@<server>:/root/aisst/redis_utils/
scp redis_utils/redis_queue.py root@<server>:/root/aisst/redis_utils/
scp redis_utils/redis_listener.py root@<server>:/root/aisst/redis_utils/
scp start.sh root@<server>:/root/aisst/
scp aisst.service root@<server>:/root/aisst/
```

### Шаг 2: Обновить systemd (если используете)

```bash
sudo cp /root/aisst/aisst.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart aisst
```

### Шаг 3: Или запустить через start.sh

```bash
cd /root/aisst
bash start.sh
```

---

## 🔍 Проверка работы

### 1. Проверить, что все процессы запущены

```bash
ps aux | grep -E "rag_worker|image_worker|redis_listener|gunicorn"
```

Должны быть видны:
- ✅ `python -m rag_chain.rag_worker`
- ✅ `python image_worker.py`
- ✅ `python -m redis_utils.redis_listener`
- ✅ `gunicorn main:app`

### 2. Проверить логи Image Worker

```bash
tail -f /root/aisst/image_worker.log
```

Ожидается:
```
2026-06-02 16:45:00 | INFO | image_worker | ==================================================
2026-06-02 16:45:00 | INFO | image_worker | Запуск Image Worker
2026-06-02 16:45:00 | INFO | image_worker | ==================================================
2026-06-02 16:45:00 | INFO | image_worker | ✅ OpenAI клиент инициализирован
2026-06-02 16:45:00 | INFO | redis_queue | Подкл. к Redis: 127.0.0.1:6379
2026-06-02 16:45:00 | INFO | image_worker | Image Worker запущен, слушаю очереди image_gen и image_edit...
```

### 3. Проверить размеры очередей

```bash
redis-cli
> LLEN aisst:queue:image_gen
> LLEN aisst:queue:image_edit
> exit
```

### 4. Проверить webhook ответы

Отправьте в бот:
```
/image
нарисуй тестовое изображение
```

Проверьте логи основного приложения:
```bash
journalctl -u aisst -f
```

Ожидается:
```
INFO | image_handler: ... операция=генерация, входных_изображений=0
INFO | Задача abc12345... поставлена в очередь image_gen
INFO | uvicorn ... "POST /webhook HTTP/1.0" 200
```

**Время ответа webhook должно быть ~100мс** (было 60+ секунд).

### 5. Проверить генерацию изображения

В логах `image_worker.log`:
```bash
tail -f image_worker.log
```

Ожидается:
```
INFO | 📥 Получена задача abc12345... (тип: image_gen)
INFO | Начало обработки: user_id=4827597, операция=генерация, изображений=0
INFO | OpenAI.generate_image: модель=gpt-image-2, входных_изображений=0, генерируемых_изображений=1
INFO | OpenAI.generate_image: изображение (420000 байт)
INFO | Успешно получен token для image
INFO | ✅ Задача выполнена для user_id=4827597
INFO | ✅ Задача abc12345... выполнена для user_id=4827597
```

---

## 🐛 Возможные проблемы и решения

### Проблема 1: Image Worker не запускается

**Симптомы:**
```bash
ps aux | grep image_worker
# Нет процесса
```

**Решение:**
```bash
# Проверить логи
cat /root/aisst/image_worker.log

# Запустить вручную для отладки
cd /root/aisst
python image_worker.py
```

**Возможные причины:**
- Не установлен `OPENAI_API_KEY_IMAGE` в `.env`
- Проблема с Redis (проверьте `redis-cli ping`)
- Ошибка импорта (проверьте зависимости)

### Проблема 2: Задачи не обрабатываются

**Симптомы:**
- Пользователь получает "Генерация запущена", но изображение не приходит
- Очередь растёт: `redis-cli LLEN aisst:queue:image_gen` возвращает большое число

**Решение:**
```bash
# Проверить, что Image Worker работает
ps aux | grep image_worker

# Проверить логи воркера
tail -50 image_worker.log

# Проверить, что воркер может подключиться к OpenAI
# (должен быть лог "✅ OpenAI клиент инициализирован")
```

### Проблема 3: Дубликаты всё ещё появляются

**Симптомы:**
- Одно изображение генерируется дважды

**Решение:**
- Убедитесь, что дедупликация по `mid` работает (должны быть логи "Пропущено дублирующееся сообщение")
- Проверьте, что Redis работает: `redis-cli ping` → `PONG`
- Проверьте ключи дедупликации: `redis-cli KEYS "aisst:processed_mid:*" | head -5`

### Проблема 4: "Ошибка постановки задачи в очередь"

**Симптомы:**
- Пользователь получает "⚠️ Ошибка: не удалось поставить задачу в очередь"

**Решение:**
```bash
# Проверить подключение к Redis
redis-cli ping

# Проверить USE_REDIS в .env
grep USE_REDIS /root/aisst/.env
# Должно быть: USE_REDIS=true

# Перезапустить Redis
systemctl restart redis
```

---

## 📊 Мониторинг

### Проверка загрузки очередей

```bash
redis-cli
> LLEN aisst:queue:image_gen
> LLEN aisst:queue:image_edit
> LLEN aisst:queue:rag
> KEYS "aisst:task:*:status"
> GET aisst:stats:total_tasks
```

### Проверка производительности

```bash
# Логи последних 100 задач Image Worker
grep "Задача.*выполнена" image_worker.log | tail -100

# Время обработки (должно быть 30-60 сек на задачу)
grep "Начало обработки" image_worker.log | tail -1
grep "Задача.*выполнена" image_worker.log | tail -1
```

---

## 🎯 Результаты

### До внедрения:
- ❌ Webhook → 60+ секунд ответ
- ❌ MAX API retry через 40 сек
- ❌ Дублирующиеся генерации
- ❌ Двойная оплата OpenAI
- ❌ Блокировка Gunicorn workers

### После внедрения:
- ✅ Webhook → 100мс ответ
- ✅ Нет retry от MAX API
- ✅ Одна генерация на запрос
- ✅ Экономия бюджета OpenAI
- ✅ Gunicorn workers свободны
- ✅ Масштабируемость (можно запустить несколько Image Workers)

---

## 📝 Дополнительная настройка (опционально)

### Масштабирование: запуск нескольких Image Workers

```bash
# В отдельных терминалах или через supervisor/systemd
python image_worker.py &
python image_worker.py &
python image_worker.py &
```

Несколько воркеров будут обрабатывать задачи параллельно из одной очереди.

### Rate limiting для image режима

В `handlers/image_handler.py` можно добавить проверку:

```python
from global_state import check_rate_limit

# В начале метода handle()
if not check_rate_limit(user_id, "image", max_requests=10, window_seconds=60):
    return "⏰ Превышен лимит генерации изображений. Попробуйте через минуту."
```

---

## 🔗 Связанные файлы

- Основная архитектура: `AGENTS.md`
- Конфигурация Redis: `redis_utils/redis_config.py`
- RAG Worker (аналогичная реализация): `rag_chain/rag_worker.py`
- Обработка webhook'ов: `max_update_handler.py`

---

**Автор:** OpenCode AI  
**Дата:** 2026-06-02  
**Версия:** 1.0
