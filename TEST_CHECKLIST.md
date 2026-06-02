# ✅ Чек-лист тестирования Image Worker

## Быстрая проверка перед деплоем

### 1. Файлы на месте
```bash
cd /root/aisst
ls -la image_worker.py                    # ✓ 11KB
grep "image_gen" redis_utils/redis_config.py  # ✓ 2 упоминания
grep "task_type" redis_utils/redis_queue.py | head -1  # ✓ параметр добавлен
grep "enqueue_task" handlers/image_handler.py  # ✓ 2 упоминания
grep "image_worker" start.sh              # ✓ 3 упоминания
```

### 2. Синтаксис Python
```bash
python -m py_compile image_worker.py      # Без ошибок
python -m py_compile handlers/image_handler.py
python -m py_compile redis_utils/redis_queue.py
python -m py_compile redis_utils/redis_listener.py
```

### 3. Зависимости
```bash
grep OPENAI_API_KEY_IMAGE .env            # Проверить наличие
redis-cli ping                             # → PONG
```

---

## Тестирование на сервере

### Шаг 1: Перезапуск
```bash
# Вариант А: через systemd
sudo systemctl restart aisst

# Вариант Б: через start.sh
bash start.sh
```

### Шаг 2: Проверка процессов (через 5 секунд)
```bash
ps aux | grep image_worker
# Ожидается: python image_worker.py
```

### Шаг 3: Проверка логов
```bash
tail -20 image_worker.log
# Ожидается:
# - "Запуск Image Worker"
# - "✅ OpenAI клиент инициализирован"
# - "Image Worker запущен, слушаю очереди"
```

### Шаг 4: Отправка тестового запроса
Отправить в бот MAX:
```
/image
нарисуй красный круг
```

### Шаг 5: Проверка webhook (сразу после отправки)
```bash
journalctl -u aisst -n 20 | grep webhook
# Ожидается время ответа ~100-200мс:
# "POST /webhook HTTP/1.0" 200
```

### Шаг 6: Проверка очереди
```bash
redis-cli LLEN aisst:queue:image_gen
# Ожидается: 1 (или 0 если уже обработано)
```

### Шаг 7: Проверка обработки (через 30-60 сек)
```bash
tail -50 image_worker.log | grep "Задача.*выполнена"
# Ожидается:
# ✅ Задача abc12345... выполнена для user_id=...
```

### Шаг 8: Проверка результата
- Пользователь должен получить изображение
- Проверить в MAX приложении

---

## Критерии успеха

✅ **Webhook отвечает быстро** (~100мс вместо 60+ сек)  
✅ **Нет дубликатов** (одно сообщение → одна генерация)  
✅ **Изображение приходит** (через 30-60 сек после запроса)  
✅ **Нет ошибок в логах** image_worker.log  
✅ **Очередь обрабатывается** (размер очереди → 0)

---

## Если что-то пошло не так

### Image Worker не запускается
```bash
# Запустить вручную для диагностики
cd /root/aisst
python image_worker.py
# Читать ошибки в терминале
```

### Изображение не приходит
```bash
# Проверить, что задача в очереди
redis-cli LLEN aisst:queue:image_gen

# Проверить логи воркера
tail -100 image_worker.log

# Проверить логи listener
tail -100 redis_listener.log
```

### Дубликаты всё ещё есть
```bash
# Проверить дедупликацию
redis-cli KEYS "aisst:processed_mid:*" | wc -l
# Должно быть > 0

# Проверить логи webhook
journalctl -u aisst | grep "Пропущено дублирующееся"
```

---

## Откат (если нужно вернуться к старой версии)

```bash
# Остановить Image Worker
pkill -f image_worker

# Откатить изменения (если есть git)
git checkout HEAD~1 handlers/image_handler.py

# Перезапустить
systemctl restart aisst
```

**Внимание:** После отката дубликаты webhook'ов вернутся!
