Контекст проекта:
Я разрабатываю ассистента для колледжа.
Стек: Python (FastAPI), библиотека LangChain.
LLM и Эмбеддинги: Исключительно GigaChat (через langchain_community.chat_models и GigaChatEmbeddings).
Векторное хранилище: ChromaDB.
Платформа: Мессенджер "MAX" (Россия).
Есть API ключи для GigaChat, GigaChatEmbeddings, SaluteSpeech
Текущее состояние:
Основной каркас приложения на FastAPI уже создан. Мне нужна помощь в реализации конкретных модулей, логики RAG и интеграции компонентов.
Мои технические требования:
LangChain-ориентированность: Используй современные цепочки (chains) или LCEL (LangChain Expression Language).
RAG-логика: Реализуй поиск через Chroma в связке с GigaChatEmbeddings. При ответах на вопросы о колледже строго придерживайся контекста из найденных документов.
Интеграция в FastAPI: Пиши код так, чтобы его было легко вставить в существующие роуты (используй Dependency Injection через Depends, если это уместно).
Стиль кода: Асинхронный Python (async/await), типизация (Type Hints), Pydantic v2.
Мессенджер MAX: Если код касается отправки сообщений, используй структуру API мессенджера MAX.
Формат взаимодействия:
Код с комментариями на русском.
Минимум лишней теории, больше конкретных реализаций для моего стека.
Если предлагаешь изменения, учитывай, что проект уже запущен.
Не удаляй существующие коментарии, еслди не согласен с комментарием то выше напиши свой
Справочные материалы:
https://developers.sber.ru/docs/ru/gigachat/models/main
https://developers.sber.ru/docs/ru/gigachat/models/gigachat-2-max
https://developers.sber.ru/docs/ru/gigachat/models/gigachat-2-pro
https://developers.sber.ru/docs/ru/gigachat/models/gigachat-2-lite
https://developers.sber.ru/docs/ru/gigachat/models/embeddings
https://developers.sber.ru/docs/ru/gigachat/models/embeddings-2
https://developers.sber.ru/docs/ru/gigachat/guides/working-with-files

https://developers.sber.ru/docs/ru/salutespeech/api/authentication
https://developers.sber.ru/docs/ru/salutespeech/rest/post-token
https://developers.sber.ru/docs/ru/salutespeech/rest/async-general
https://developers.sber.ru/docs/ru/salutespeech/rest/post-data-upload
https://developers.sber.ru/docs/ru/salutespeech/guides/recognition/recognition-ways
https://developers.sber.ru/docs/ru/salutespeech/api/grpc/recognition-stream-2

---

## Команды запуска

- **Dev (один процесс):** `uvicorn main:app --reload`
- **Prod (Gunicorn):** `gunicorn -w 2 -k uvicorn.workers.UvicornWorker main:app --bind 0.0.0.0:8000 --timeout 300`
- **Полный запуск (все компоненты):** `bash start.sh` — запускает RAG Worker, Redis Listener и Gunicorn
- **Systemd:** `systemctl start aisst` (файл `aisst.service`, WorkingDirectory=/root/aisst)

## Дополнительные процессы (при USE_REDIS=true)

- **RAG Worker:** `python rag_worker.py` — отдельный процесс, читает задачи из Redis очереди `rag`
- **Redis Listener:** `python -m redis_utils.redis_listener` — слушает Pub/Sub результаты задач и отправляет уведомления пользователям

## Архитектура и точки входа

- **Точка входа FastAPI:** `main.py` → `create_app()` → `app = create_app()`. Роуты подключены через `routers.py`.
- **Lifespan (`lifespan.py`):** инициализация БД, промптов по умолчанию, GigaChat клиентов (native + LangChain), webhook подписки.
- **Webhook:** `POST /webhook` → `max_update_handler.handle_webhook()` → `process_update()`. Обрабатываются только `message_created` и `message_callback`.
- **Бизнес-логика:** `bot_logic.py` — `handle_command()` и `handle_message()`. Режимы: `gigachat`, `gigachatpro`, `mentor`, `edit`, `rag`.
- **RAG:** `rag_chain.py` → `ask_rag()` — LCEL цепочка: Chroma retriever (MMR) → промпт из БД → GigaChat (LangChain) → ответ.
- **MAX API:** `max_api.py` — `send_message()`, `send_inline_message()`, `send_document()`, `send_image()`, `subscribe_webhook()`.

## Хранилище состояний (Redis vs in-memory)

- Переменная `USE_REDIS` в `.env` переключает режим. При `true` все состояния (режимы, контексты, mentor_state, prompt_edit_state) хранятся в Redis для синхронизации между Gunicorn воркерами.
- При `false` — in-memory словари в `global_state.py` (работает только с одним воркером).
- Функции-обёртки в `global_state.py` автоматически выбирают источник.

## База данных

- **SQLite (async):** `MAX_DB_PATH` в `.env`. SQLAlchemy async, модели в `db.py`.
- **Таблицы:** `users`, `billings`, `prompts`, `prompt_versions`.
- **Промпты:** загружаются из БД (`PromptRepository`), кэшируются в памяти. Кэш сбрасывается через `invalidate_rag_prompt_cache()` / `invalidate_prompt_cache()` после редактирования.
- **Системный пользователь:** `id=0` создаётся автоматически при старте.

## Режимы бота (команды)

| Команда | Режим | Описание |
|---------|-------|----------|
| `/start` | — | Инлайн-кнопки (если permission != 1) |
| `/gigachat` | gigachat | RAG-поиск по документам колледжа |
| `/gigachatpro` | gigachatpro | Прямой чат с GigaChat + прикреплённый файл |
| `/mentor` | mentor | Проверка знаний (тема/документ, вопросы/ответы) |
| `/edit` | edit | Редактирование промптов (только admin, permission=0) |
| `/rag` | rag | Управление векторной базой (загрузка/удаление документов) |
| `/billing` | — | Баланс пользователя |

## RAG и ChromaDB

- **Эмбеддинги:** `rag_embeddings.py` → `get_giga_embeddings(model_name="Embeddings")`.
- **Векторная БД:** ChromaDB, persist в `GUEST_RAG_DIR` (по умолчанию `rag/guest`).
- **Retriever:** MMR, параметры по умолчанию: `top_k=5`, `fetch_k=20`, `lambda_mult=0.9`.
- **Загрузка файлов:** `load_from_file.py` → `save_to_vector_db()`. Поддерживаемые форматы: pdf, txt, docx, doc, xlsx, xls.
- **При USE_REDIS=true:** загрузка файла ставится в Redis очередь, пользователь получает уведомление по завершении.

## Важные зависимости и quirks

- `.env` загружается через `load_dotenv(override=True)` в `global_state.py`.
- GigaChat SDK синхронный — вызывается через `ThreadPoolExecutor` в `/giga-chat` роуте.
- LangChain-клиент (`app.state.giga_lc_client`) используется в RAG и mentor режимах.
- Сообщения > 4000 символов автоматически разбиваются (`split_long_message()`).
- `db.sqlite3` и `.env` в `.gitignore` — не коммить.
- `pyproject.tombl` (с ошибкой в имени) — возможно, не используется; основной файл зависимостей — `requirements.txt`.

## Деплой на сервер

```bash
scp aisst.service root@<server>:/etc/systemd/system/aisst.service
systemctl daemon-reload && systemctl enable aisst && systemctl start aisst
```

## Справочные ссылки MAX API

Типы обновлений в подписке: `message_created`, `message_callback` (остальные доступны но не подписаны).
Webhook secret проверяется через заголовок `X-Max-Bot-Api-Secret`.
