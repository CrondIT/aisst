# Техническое задание: Интеграция Alembic для управления миграциями БД

**Проект:** `aisst` / FastAPI + SQLAlchemy 2.0 async + SQLite  
**Приоритет:** Высокий (prod-безопасность)  
**Оценка трудоёмкости:** 2–3 часа

---

## 1. Контекст и проблема

Текущий код в `lifespan.py:48` вызывает `db.create_database()`, которая внутри (`db.py:122`) выполняет:

```python
await conn.run_sync(Base.metadata.create_all)
```

**Поведение `create_all`:** создаёт таблицы если их нет, но **игнорирует любые изменения в уже существующих таблицах** (добавление колонок, изменение типов, новые индексы). В prod это означает: при деплое новой версии с изменённой моделью база данных молча остаётся в старой схеме, и приложение падает в рантайме.

**Alembic** в связке отсутствует: пакет не значится в `requirements.txt`.

---

## 2. Цель

Внедрить Alembic с поддержкой **async SQLAlchemy** так, чтобы:

- каждое изменение схемы БД применялось через версионированную миграцию;
- деплой на сервер автоматически накатывал новые миграции до старта приложения;
- существующая prod-база (`db.sqlite3`) не была повреждена;
- `create_all()` был удалён из пути запуска приложения.

---

## 3. Текущие модели (зафиксировать как baseline)

| Таблица | Ключевые колонки |
|---|---|
| `users` | `id` (BigInt PK), `name`, `startdate`, `coindate`, `coins`, `giftcoins`, `note`, `permission`, `check` |
| `billings` | `id` (PK AI), `user_id` (FK→users), `date`, `usermode`, `userprompt`, `inccoins`, `deccoins`, `giftcoins`, `balance`, `notes` |
| `prompts` | `id` (PK AI), `prompt_key` (unique), `description`, `current_system_text`, `current_human_text`, `created_at`, `updated_at`, `updated_by` |
| `prompt_versions` | `id` (PK AI), `prompt_id` (FK→prompts CASCADE), `version_number`, `system_text`, `human_text`, `created_at`, `created_by` |

---

## 4. Требования к реализации

### 4.1. Зависимости

Добавить в `requirements.txt`:

```
alembic==1.14.1
```

> Alembic 1.14.x поддерживает `async_engine_from_config` и `asyncio`-режим нативно.

### 4.2. Структура файлов после внедрения

```
aisst/
├── alembic/
│   ├── env.py              # настройка подключения (async-режим)
│   ├── script.py.mako      # шаблон для генерации файлов миграций
│   └── versions/
│       └── 0001_initial_schema.py   # baseline по текущей схеме
├── alembic.ini             # конфиг: путь к БД через переменную окружения
└── ... (существующие файлы)
```

### 4.3. Конфигурация `alembic.ini`

- `sqlalchemy.url` должен читаться из переменной окружения `MAX_DB_PATH` (не хардкодить путь).
- Директория `script_location` = `alembic`.
- Включить `compare_type = true` и `compare_server_default = true` для автодетекта изменений.

### 4.4. Конфигурация `alembic/env.py`

- Использовать **async-режим**: `run_async_migrations()` через `asyncio.run()`.
- Импортировать `Base` и все модели из `db.py` для автогенерации (`target_metadata = Base.metadata`).
- URL подключения брать из `global_state.MAX_DB_PATH` (уже загружается через `load_dotenv`).
- Поддержать оба режима: `online` (прямое подключение) и `offline` (генерация SQL-скриптов).

### 4.5. Первоначальная миграция (baseline)

Создать миграцию `0001_initial_schema.py` с флагом `--autogenerate` **после** подключения существующей prod-базы.

Логика `upgrade()` в baseline-миграции должна применяться только если таблиц нет (чтобы не падать на живой базе):

```python
# Псевдокод upgrade():
if not inspector.has_table("users"):
    op.create_table("users", ...)
    op.create_table("billings", ...)
    op.create_table("prompts", ...)
    op.create_table("prompt_versions", ...)
```

Затем пометить текущую prod-базу как уже прошедшую эту миграцию командой:

```bash
alembic stamp head
```

### 4.6. Изменения в `lifespan.py`

Убрать вызов `db.create_database()` как инициализатора схемы. Оставить только создание системного пользователя (он не касается схемы).

Добавить перед `yield` вызов программного запуска миграций:

```python
# Псевдокод (async):
from alembic import command
from alembic.config import Config

def run_migrations():
    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")

await asyncio.to_thread(run_migrations)
```

> `command.upgrade` — синхронный API Alembic, поэтому выносится в `to_thread`.

### 4.7. Изменения в `db.py`

- Функция `create_database()` — убрать вызов `Base.metadata.create_all`. Оставить только создание системного пользователя, переименовать в `ensure_system_user()` для ясности.
- Модели (`User`, `Billing`, `Prompt`, `PromptVersion`) — не трогать.

### 4.8. Обновление `start.sh` и `aisst.service`

Перед запуском Gunicorn добавить шаг накатки миграций:

```bash
# В start.sh перед строкой запуска gunicorn:
echo "[INFO] Running DB migrations..."
.venv/bin/alembic upgrade head
```

В `aisst.service` аналогично в `ExecStartPre`:

```ini
ExecStartPre=/root/aisst/.venv/bin/alembic -c /root/aisst/alembic.ini upgrade head
```

---

## 5. Workflow для будущих изменений схемы

После внедрения стандартный процесс изменения модели:

```bash
# 1. Внести изменения в модели (db.py)
# 2. Сгенерировать миграцию:
alembic revision --autogenerate -m "add_column_users_department"
# 3. Проверить сгенерированный файл в alembic/versions/
# 4. Применить локально:
alembic upgrade head
# 5. Задеплоить — миграция применится автоматически при старте
```

---

## 6. Риски и меры безопасности

| Риск | Мера |
|---|---|
| Повреждение prod-базы при baseline | Перед `alembic stamp head` сделать резервную копию `db.sqlite3` |
| Конфликт `create_all` и Alembic на чистом деплое | После внедрения `create_all` убирается — Alembic становится единственным источником схемы |
| SQLite не поддерживает `ALTER COLUMN` | Alembic для SQLite использует `batch_alter_table` — указать в `env.py` `render_as_batch=True` |
| Параллельный запуск миграций (2 Gunicorn worker'а) | Миграции запускать в `ExecStartPre` (до старта worker'ов), не внутри `lifespan` каждого процесса |

---

## 7. Критерий готовности

- [ ] `alembic upgrade head` выполняется без ошибок на чистой и на существующей базе
- [ ] `alembic history` показывает `0001_initial_schema → head`
- [ ] `lifespan.py` не вызывает `create_all` нигде в цепочке
- [ ] После добавления тестовой колонки в модель: `alembic revision --autogenerate` её детектирует корректно
- [ ] `start.sh` и `aisst.service` запускают миграции до старта воркеров
