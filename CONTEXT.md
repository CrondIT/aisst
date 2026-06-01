# ТЕХНИЧЕСКОЕ ЗАДАНИЕ
## Добавление контекстного диалога в режимы gigachatpro, chatgpt, gemini

---

## 1. ОБЩАЯ ИНФОРМАЦИЯ

**Цель:** Реализовать сохранение и передачу истории диалога (user_context) в режимах `gigachatpro`, `chatgpt`, `gemini` для обеспечения контекстных бесед с AI-моделями.

**Текущее состояние:** 
- В проекте уже существует инфраструктура для работы с контекстом через `get_user_context()` и `set_user_context()` в `global_state.py`
- Контекст хранится в Redis (при `USE_REDIS=true`) или in-memory (при `USE_REDIS=false`)
- Контекст используется в `prompt_builder.py` для режимов с файлами, но **не используется** в прямых LLM-режимах

---

## 2. ТРЕБОВАНИЯ

### 2.1. Функциональные требования

#### 2.1.1. Хранение контекста
- **Изоляция по режимам:** каждый режим (`gigachat`, `gigachatpro`, `chatgpt`, `gemini`) должен иметь **отдельный** контекст для каждого пользователя
- **Формат ключа в Redis/памяти:** `context_{mode}` (например, `context_gigachat`, `context_chatgpt`)
- **Объём истории:** не более `MAX_CONTEXT_MESSAGES` (текущее значение = 5 пар сообщений user+assistant)
- **Двухуровневое хранение:**
  - **Redis (кэш, первый уровень):** быстрый доступ к активным контекстам, TTL = 1 час (настраиваемый)
  - **База данных (постоянное хранилище, второй уровень):** долгосрочное хранение всех контекстов
- **Персистентность:** контекст **НЕ очищается** при истечении TTL в Redis — автоматически восстанавливается из БД
- **Логика работы с кэшем:**
  1. При чтении контекста: сначала проверяется Redis, если не найден — загружается из БД и кэшируется в Redis
  2. При записи контекста: сохраняется одновременно в Redis (с TTL) и в БД (постоянно)
  3. При истечении TTL: контекст удаляется из Redis, но остаётся в БД
  4. При следующем обращении: контекст восстанавливается из БД в Redis

#### 2.1.2. Формат контекста
Контекст должен представлять собой список сообщений в формате LangChain/OpenAI:
```python
[
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Вопрос пользователя"},
    {"role": "assistant", "content": "Ответ модели"},
    {"role": "user", "content": "Следующий вопрос"},
    # ...
]
```

#### 2.1.3. Логика обработки сообщений

**При получении нового сообщения от пользователя:**
1. Загрузить существующий контекст для `(user_id, mode)` через `get_user_context(user_id, mode)`
   - Функция `get_user_context()` автоматически проверяет Redis → если не найден, загружает из БД → кэширует в Redis
2. Если контекст пустой — инициализировать системным промптом из `SYSTEM_PROMPTS` (если есть для данного режима)
3. Добавить сообщение пользователя: `{"role": "user", "content": user_text}`
4. **Если прикреплён файл:** включить `extracted_text` в промпт согласно текущей логике `full_prompt()`
5. Обрезать контекст по лимиту токенов через `token_utils.truncate_messages_for_token_limit()`
6. Обрезать по количеству сообщений: оставить последние `MAX_CONTEXT_MESSAGES` элементов (user+assistant пары)
7. Отправить контекст в AI-модель
8. Получить ответ модели
9. Добавить ответ в контекст: `{"role": "assistant", "content": answer}`
10. Сохранить обновлённый контекст через `set_user_context(user_id, mode, context)`
    - Функция `set_user_context()` автоматически сохраняет в Redis (с TTL) **И** в БД (постоянно)

**При переключении режима (команда `/gigachat`, `/chatgpt` и т.д.):**
- Контекст **НЕ очищается** — каждый режим имеет свой изолированный контекст
- При возврате к режиму пользователь продолжает диалог с того места, где остановился

**Команда очистки контекста:**
- Предусмотреть команду `/clear` или `/reset` для очистки контекста текущего режима
- При выполнении: удалить `context_{mode}` из Redis/памяти **И** из БД для данного `user_id`
- Удаление должно быть полным (из обоих хранилищ)

---

### 2.2. Технические требования

#### 2.2.0. Схема базы данных для хранения контекста

**Новая таблица: `user_contexts`**

```sql
CREATE TABLE user_contexts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id BIGINT NOT NULL,
    mode VARCHAR(50) NOT NULL,
    context_data TEXT NOT NULL,  -- JSON-сериализованный список сообщений
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    message_count INTEGER DEFAULT 0,  -- Количество сообщений в контексте
    UNIQUE(user_id, mode),  -- Один контекст на пару (user_id, mode)
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX idx_user_contexts_user_mode ON user_contexts(user_id, mode);
CREATE INDEX idx_user_contexts_updated ON user_contexts(updated_at);
```

**SQLAlchemy модель (добавить в `db.py`):**

```python
class UserContext(Base):
    """Таблица для хранения контекстов диалогов пользователей."""
    __tablename__ = "user_contexts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(50), nullable=False)
    context_data: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    
    # Составной уникальный индекс
    __table_args__ = (
        UniqueConstraint('user_id', 'mode', name='uq_user_mode'),
        Index('idx_user_contexts_user_mode', 'user_id', 'mode'),
        Index('idx_user_contexts_updated', 'updated_at'),
    )
```

**CRUD-функции (добавить в `db.py`):**

```python
async def save_user_context(user_id: int, mode: str, context: list[dict]) -> bool:
    """
    Сохраняет контекст пользователя в БД.
    Обновляет запись, если она существует, иначе создаёт новую.
    """
    import json
    
    try:
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select
            
            result = await db.execute(
                select(UserContext).where(
                    UserContext.user_id == user_id,
                    UserContext.mode == mode
                )
            )
            context_record = result.scalar_one_or_none()
            
            context_json = json.dumps(context, ensure_ascii=False)
            message_count = len([m for m in context if m.get("role") in ("user", "assistant")])
            
            if context_record:
                # Обновляем существующий
                context_record.context_data = context_json
                context_record.updated_at = datetime.now()
                context_record.message_count = message_count
            else:
                # Создаём новый
                new_context = UserContext(
                    user_id=user_id,
                    mode=mode,
                    context_data=context_json,
                    message_count=message_count
                )
                db.add(new_context)
            
            await db.commit()
            return True
    except Exception as e:
        logger.error(f"Ошибка сохранения контекста в БД: {e}")
        return False


async def load_user_context(user_id: int, mode: str) -> list[dict] | None:
    """
    Загружает контекст пользователя из БД.
    Возвращает список сообщений или None, если контекст не найден.
    """
    import json
    
    try:
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select
            
            result = await db.execute(
                select(UserContext).where(
                    UserContext.user_id == user_id,
                    UserContext.mode == mode
                )
            )
            context_record = result.scalar_one_or_none()
            
            if context_record:
                return json.loads(context_record.context_data)
            return None
    except Exception as e:
        logger.error(f"Ошибка загрузки контекста из БД: {e}")
        return None


async def delete_user_context(user_id: int, mode: str) -> bool:
    """
    Удаляет контекст пользователя из БД для указанного режима.
    """
    try:
        async with AsyncSessionLocal() as db:
            from sqlalchemy import delete
            
            await db.execute(
                delete(UserContext).where(
                    UserContext.user_id == user_id,
                    UserContext.mode == mode
                )
            )
            await db.commit()
            return True
    except Exception as e:
        logger.error(f"Ошибка удаления контекста из БД: {e}")
        return False
```

#### 2.2.1. Модификация обработчиков

**Файл: `handlers/llm_handler.py`**
- Класс `LlmDirectHandler` (используется для `gigachatpro`, `chatgpt`, `gemini`)
- **Изменения в методе `handle()`:**
  1. Загрузить контекст через `get_user_context(user_id, mode_name)`
  2. Если контекст пустой — инициализировать системным промптом (если есть)
  3. Добавить сообщение пользователя в контекст
  4. Если есть `extracted_text` (прикреплённый файл) — включить через `full_prompt()`
  5. Обрезать контекст по токенам через `token_utils.truncate_messages_for_token_limit()`
  6. Обрезать по количеству сообщений (последние `MAX_CONTEXT_MESSAGES`)
  7. Передать контекст в `client.chat(messages=context, model=...)`
  8. Добавить ответ модели в контекст
  9. Сохранить контекст через `set_user_context(user_id, mode_name, context)`
  10. Обновить billing (уже есть)
  11. Вернуть ответ (с форматированием, если нужно)

**Файл: `handlers/gigachat_handler.py`**
- Класс `GigachatHandler` (используется для режима `gigachat` с RAG)
- **Текущее поведение:** режим `gigachat` делает RAG-поиск через `ask_rag()` **без сохранения контекста**
- **Новое поведение (на усмотрение):**
  - **Вариант А (простой):** оставить `gigachat` без контекста (только RAG-ответы на отдельные вопросы)

  
#### 2.2.2. Модификация `global_state.py`

**Обновление функций `get_user_context()` и `set_user_context()`:**

```python
def get_user_context(user_id: int, mode: str) -> list:
    """
    Получает контекст пользователя для указанного режима.
    
    Логика:
    1. Проверяет Redis (если USE_REDIS=true) или память (если false)
    2. Если не найден в кэше — загружает из БД через load_user_context()
    3. Если найден в БД — кэширует в Redis/память
    4. Возвращает контекст или дефолтный (с system-промптом)
    """
    if _use_redis:
        q = _get_queue()
        if q:
            context = q.get_user_state(user_id, f"context_{mode}")
            if context is not None:
                return context
        # Redis не вернул — пробуем БД
        import asyncio
        from db import load_user_context
        context = asyncio.run(load_user_context(user_id, mode))
        if context:
            # Кэшируем в Redis для следующих обращений
            if q:
                q.set_user_state(user_id, f"context_{mode}", context)
            return context
    else:
        # Fallback к памяти (только для одиночного процесса)
        if user_id in user_contexts and mode in user_contexts[user_id]:
            return user_contexts[user_id][mode]
        # Память не вернула — пробуем БД
        import asyncio
        from db import load_user_context
        context = asyncio.run(load_user_context(user_id, mode))
        if context:
            # Кэшируем в памяти
            if user_id not in user_contexts:
                user_contexts[user_id] = {}
            user_contexts[user_id][mode] = context
            return context

    # Возвращаем дефолтный контекст
    system_message = SYSTEM_PROMPTS.get(mode, "You are a helpful assistant.")
    return [{"role": "system", "content": system_message}]


def set_user_context(user_id: int, mode: str, context: list):
    """
    Сохраняет контекст пользователя для указанного режима.
    
    Логика:
    1. Сохраняет в Redis/память (кэш с TTL)
    2. Сохраняет в БД (постоянно) через save_user_context()
    """
    if _use_redis:
        # Сохраняем в Redis
        q = _get_queue()
        if q:
            q.set_user_state(user_id, f"context_{mode}", context)
    else:
        # Сохраняем в память (только для одиночного процесса)
        if user_id not in user_contexts:
            user_contexts[user_id] = {}
        user_contexts[user_id][mode] = context
    
    # Сохраняем в БД (всегда, независимо от Redis)
    import asyncio
    from db import save_user_context
    asyncio.run(save_user_context(user_id, mode, context))
```

**Добавить функцию для очистки контекста:**

```python
def clear_user_context(user_id: int, mode: str):
    """
    Очищает контекст пользователя для указанного режима.
    Удаляет из Redis/памяти И из БД.
    """
    if _use_redis:
        q = _get_queue()
        if q:
            q.delete_user_state(user_id, f"context_{mode}")
    else:
        if user_id in user_contexts and mode in user_contexts[user_id]:
            del user_contexts[user_id][mode]
    
    # Удаляем из БД
    import asyncio
    from db import delete_user_context
    asyncio.run(delete_user_context(user_id, mode))
```

**Примечание:** использование `asyncio.run()` внутри синхронных функций не идеально, но допустимо для совместимости с текущей архитектурой. В будущем можно рефакторить `get_user_context()` / `set_user_context()` в асинхронные версии.

#### 2.2.3. Модификация `prompt_builder.py`

**Функция `full_prompt()`:**
- Текущая версия формирует промпт с файлом, но **НЕ учитывает** историю диалога для режимов без файлов (строки 183-204)
- **Требуется рефакторинг:**
  1. Принимать дополнительный параметр `context: list[dict] | None`
  2. Если контекст передан — использовать его как основу
  3. Если есть файл — добавлять `extracted_text` в последнее сообщение пользователя (или в system)
  4. Если запрошен формат (docx/pdf/excel) — добавлять схему в system-сообщение (как сейчас)
  5. Применять обрезку по токенам и по количеству сообщений
  6. Возвращать готовый контекст для отправки в модель

**Альтернативный подход (предпочтительный):**
- Логика работы с контекстом полностью переносится в `LlmDirectHandler`
- `full_prompt()` используется только для специальной обработки файлов и форматов
- `LlmDirectHandler` сам собирает контекст, вызывает `full_prompt()` для расширения последнего сообщения (если есть файл), затем обрезает и сохраняет

#### 2.2.4. Модификация `bot_logic.py`

**Добавить команду очистки контекста:**
```python
if command == "/clear" or command == "/reset":
    from global_state import clear_user_context
    user_mode = get_user_mode(user_id)
    # Очищаем контекст текущего режима (из Redis/памяти И из БД)
    clear_user_context(user_id, user_mode)
    return f"История диалога в режиме '{user_mode}' очищена."
```

**Документировать в `mode_map`:**
```python
mode_map = {
    # ...
    "/clear": (None, "Очистить историю диалога в текущем режиме"),
}
```

#### 2.2.5. Системные промпты

**Файл: `global_state.py`**
- Добавить системные промпты для новых режимов в `SYSTEM_PROMPTS`:
```python
SYSTEM_PROMPTS = {
    # ...существующие...
    "gigachatpro": (
        "Ты — продвинутый AI-ассистент на базе GigaChat Pro. "
        "Помогаешь пользователям с любыми вопросами, "
        "предоставляя подробные и точные ответы."
    ),
    "chatgpt": (
        "You are a helpful assistant powered by ChatGPT. "
        "Provide clear, accurate, and detailed responses."
    ),
    "gemini": (
        "You are a helpful assistant powered by Google Gemini. "
        "Provide clear, accurate, and helpful responses."
    ),
    "gigachat": (
        "Ты — ассистент колледжа. Отвечаешь на вопросы студентов "
        "на основе документов из базы знаний. Если информации нет в документах — "
        "честно говоришь об этом."
    ),
}
```

---

### 2.3. Нефункциональные требования

#### 2.3.1. Производительность
- При работе с Redis: минимизировать количество обращений к хранилищу (загрузка контекста — 1 раз в начале, сохранение — 1 раз в конце)
- Обрезка по токенам должна выполняться эффективно (используется существующий `token_utils`)

#### 2.3.2. Надёжность
- При ошибке загрузки контекста из Redis — использовать пустой контекст (не падать)
- При ошибке сохранения контекста — логировать, но не прерывать отправку ответа пользователю
- Валидация: контекст должен содержать только валидные роли (`system`, `user`, `assistant`)

#### 2.3.3. Совместимость
- Код должен работать как с `USE_REDIS=true` (prod), так и с `USE_REDIS=false` (dev)
- Не ломать существующие режимы (`mentor`, `edit`, `rag`, `image`)
- Обратная совместимость: старые пользователи без контекста начинают с чистого листа

---

## 3. ПЛАН РЕАЛИЗАЦИИ

### Этап 1: Подготовка и схема БД (15% работ)
- [ ] Создать модель `UserContext` в `db.py` (таблица `user_contexts`)
- [ ] Добавить индексы и ограничения для `user_contexts`
- [ ] Добавить CRUD-функции в `db.py`: `save_user_context()`, `load_user_context()`, `delete_user_context()`
- [ ] Запустить миграцию БД (создать таблицу `user_contexts`)
- [ ] Добавить системные промпты для `gigachatpro`, `chatgpt`, `gemini` в `global_state.py`
- [ ] Обновить `get_user_context()` в `global_state.py` для загрузки из БД при отсутствии в кэше
- [ ] Обновить `set_user_context()` в `global_state.py` для сохранения в БД + кэш
- [ ] Добавить функцию `clear_user_context()` в `global_state.py`
- [ ] Добавить команду `/clear` в `bot_logic.py`
- [ ] Обновить `mode_map` с описанием команды `/clear`

### Этап 2: Модификация `LlmDirectHandler` (45% работ)
- [ ] Импортировать необходимые функции: `get_user_context`, `set_user_context`, `MAX_CONTEXT_MESSAGES`, `SYSTEM_PROMPTS`, `token_utils`
- [ ] В начале `handle()`: загрузить контекст через `get_user_context(user_id, self.mode_name)`
- [ ] Инициализировать системным промптом, если контекст пустой
- [ ] Добавить сообщение пользователя в контекст
- [ ] Интегрировать логику `full_prompt()` для обработки файлов (если `extracted_text` не пустой)
- [ ] Применить обрезку по токенам через `token_utils.truncate_messages_for_token_limit()`
- [ ] Применить обрезку по количеству сообщений (`MAX_CONTEXT_MESSAGES`)
- [ ] Передать контекст в `client.chat(messages=context, model=...)`
- [ ] Добавить ответ модели в контекст
- [ ] Сохранить контекст через `set_user_context(user_id, self.mode_name, context)`
- [ ] Обеспечить логирование для отладки

### Этап 3: Тестирование (30% работ)

#### 3.1. Тесты базовой функциональности
- [ ] Тест 1: Простой диалог в режиме `gigachatpro` без файлов (3-5 сообщений)
- [ ] Тест 2: Диалог с прикреплённым файлом в режиме `chatgpt`
- [ ] Тест 3: Переключение между режимами (`gigachatpro` → `chatgpt` → `gigachatpro`) — контекст изолирован
- [ ] Тест 4: Команда `/clear` очищает контекст текущего режима (из Redis/памяти И из БД)
- [ ] Тест 5: Диалог превышает `MAX_CONTEXT_MESSAGES` — старые сообщения удаляются
- [ ] Тест 6: Диалог превышает лимит токенов — применяется обрезка
- [ ] Тест 7: Формат вывода (docx/pdf) работает с контекстом

#### 3.2. Тесты персистентности (БД + Redis)
- [ ] Тест 8: Контекст сохраняется в БД при каждом сообщении
- [ ] Тест 9: После перезагрузки Gunicorn (при `USE_REDIS=true`) контекст восстанавливается из БД
- [ ] Тест 10: Истечение TTL в Redis → контекст автоматически загружается из БД при следующем обращении
- [ ] Тест 11: При `USE_REDIS=false` контекст всё равно сохраняется в БД
- [ ] Тест 12: Ручная проверка БД: `SELECT * FROM user_contexts` показывает все активные контексты
- [ ] Тест 13: Удаление контекста через `/clear` удаляет запись из БД

#### 3.3. Тесты отказоустойчивости
- [ ] Тест 14: Ошибка Redis — бот загружает контекст из БД
- [ ] Тест 15: Ошибка БД при чтении — бот использует пустой контекст (не падает)
- [ ] Тест 16: Ошибка БД при записи — бот продолжает работать (контекст в Redis сохраняется)

### Этап 4: Документация и финализация (10% работ)
- [ ] Обновить `AGENTS.md` с описанием нового поведения режимов
- [ ] Обновить комментарии в коде (русский язык)
- [ ] Проверить, что не удалены существующие комментарии (или добавлены новые выше)
- [ ] Финальный code review

---

## 4. КРИТЕРИИ ПРИЁМКИ

### 4.1. Обязательные
✅ Контекст сохраняется и передаётся в режимах `gigachat`, `gigachatpro`, `chatgpt`, `gemini`  
✅ Контекст изолирован по режимам (отдельная история для каждого режима)  
✅ Контекст ограничен `MAX_CONTEXT_MESSAGES` сообщениями  
✅ Контекст ограничен лимитом токенов модели  
✅ **Контекст сохраняется в БД (таблица `user_contexts`)**  
✅ **Контекст НЕ очищается при истечении TTL Redis — восстанавливается из БД**  
✅ Контекст персистентен при `USE_REDIS=true` (переживает перезагрузку)  
✅ Команда `/clear` очищает контекст из Redis/памяти **И** из БД  
✅ Прикреплённые файлы работают с контекстом  
✅ Форматированный вывод (docx/pdf/excel/rtf) работает с контекстом  
✅ Существующие режимы не сломаны  
✅ Код работает при `USE_REDIS=true` и `USE_REDIS=false` (в обоих случаях используется БД)  

### 4.2. Опциональные (nice-to-have)
- Команда `/history` для просмотра текущего контекста
- Настраиваемый `MAX_CONTEXT_MESSAGES` для каждого режима (сейчас общий)
- Экспорт истории диалога в файл
- Аналитика: статистика использования контекста по пользователям/режимам
- CRON-задача для периодической очистки старых контекстов (> 30 дней)

---

## 5. РИСКИ И ОГРАНИЧЕНИЯ

### 5.1. Риски
- **Превышение лимита токенов:** даже после обрезки, длинные сообщения могут превысить лимит модели  
  → *Решение:* дополнительная проверка и обрезка последнего сообщения пользователя (уже есть в `prompt_builder.py`)

- **Redis недоступен:** если Redis упал при `USE_REDIS=true`, контекст всё равно доступен из БД  
  → *Решение:* `get_user_context()` автоматически загружает из БД при отсутствии в Redis

- **БД недоступна:** если БД недоступна, контекст не сохраняется, но бот продолжает работать  
  → *Решение:* логирование ошибок БД, контекст временно хранится в Redis/памяти

- **Конфликт контекста при параллельных запросах:** если пользователь отправляет 2 сообщения подряд  
  → *Решение:* БД обеспечивает атомарность через UNIQUE(user_id, mode); последняя запись перезаписывает предыдущую

- **Производительность БД:** синхронные вызовы `asyncio.run()` внутри `get_user_context()` / `set_user_context()` могут замедлить обработку  
  → *Решение:* в будущем рефакторить на полностью асинхронные функции; пока приемлемо для нагрузки бота

- **Размер контекста в БД:** длинные диалоги могут занимать много места в БД  
  → *Решение:* ограничение `MAX_CONTEXT_MESSAGES` (5 пар = ~10 записей) + периодическая очистка старых контекстов (опционально)

### 5.2. Ограничения
- **Мультимодальность:** контекст поддерживает только текстовые сообщения (изображения в режиме `image` не сохраняются)
- **Миграция старых пользователей:** пользователи, которые уже общались с ботом, начнут с пустого контекста после обновления (таблица `user_contexts` создаётся пустой)
- **TTL в Redis:** контекст удаляется из Redis через 1 час неактивности, но **остаётся в БД** и восстанавливается при следующем обращении
- **Размер БД:** каждый активный пользователь × 4 режима = 4 записи в `user_contexts` (при среднем размере ~2 КБ на запись = ~8 КБ на пользователя)

---

## 6. ПРИМЕРЫ ИСПОЛЬЗОВАНИЯ

### Пример 1: Простой диалог
```
Пользователь: /gigachatpro
Бот: Режим: GigaChat Pro

Пользователь: Привет! Как тебя зовут?
Бот: Привет! Я — AI-ассистент на базе GigaChat Pro. Чем могу помочь?

Пользователь: А какая у тебя версия?
Бот: Я работаю на модели GigaChat. Моя задача — помогать вам с вопросами.

Пользователь: /chatgpt
Бот: Режим: ChatGPT

Пользователь: Как тебя зовут?
Бот: I'm ChatGPT, an AI assistant. How can I help you?

Пользователь: /gigachatpro
Бот: Режим: GigaChat Pro

Пользователь: Напомни, о чём мы говорили?
Бот: Мы говорили о том, как меня зовут и какая у меня версия. Я представился как AI-ассистент на базе GigaChat Pro.
```

### Пример 2: Диалог с файлом
```
Пользователь: /chatgpt
Бот: Режим: ChatGPT

Пользователь: [прикрепляет файл договор.pdf]
Бот: Файл получен

Пользователь: Что это за документ?
Бот: Это договор аренды помещения между компанией А и компанией Б, подписанный 01.01.2024.

Пользователь: Какой срок действия?
Бот: Согласно документу, срок действия договора — 12 месяцев с момента подписания.
```

### Пример 3: Очистка контекста
```
Пользователь: /gigachatpro
Бот: Режим: GigaChat Pro

Пользователь: Запомни число 42
Бот: Хорошо, запомнил число 42.

Пользователь: Какое число я назвал?
Бот: Вы назвали число 42.

Пользователь: /clear
Бот: История диалога в режиме 'gigachatpro' очищена.

Пользователь: Какое число я назвал?
Бот: Вы не называли никакого числа в нашем диалоге.
```

---

## 7. СПРАВОЧНЫЕ МАТЕРИАЛЫ

### 7.1. Существующий код
- `global_state.py:366-404` — функции `get_user_context()` / `set_user_context()` (требуют обновления)
- `handlers/llm_handler.py:32-60` — текущая реализация `LlmDirectHandler.handle()`
- `prompt_builder.py:18-204` — функция `full_prompt()`
- `token_utils.py` — функции обрезки по токенам (используется в проекте)
- `redis_utils/redis_queue.py:444-522` — работа с состояниями пользователей в Redis
- `db.py:40-80` — модели `User`, `Billing` (примеры для создания `UserContext`)
- `db.py:117-139` — функция `create_database()` (автоматически создаст `user_contexts` при старте)

### 7.2. Внешние документы
- https://developers.sber.ru/docs/ru/gigachat/api/reference/rest/post-chat — GigaChat API (поддержка истории диалога)
- https://platform.openai.com/docs/guides/chat — OpenAI Chat API (формат сообщений)
- https://ai.google.dev/gemini-api/docs/text-generation — Gemini API (multi-turn conversations)

### 7.3. SQL-запросы для мониторинга

**Проверить количество активных контекстов:**
```sql
SELECT COUNT(*) as total_contexts, mode, 
       AVG(message_count) as avg_messages
FROM user_contexts
GROUP BY mode;
```

**Найти самые длинные диалоги:**
```sql
SELECT user_id, mode, message_count, updated_at
FROM user_contexts
ORDER BY message_count DESC
LIMIT 10;
```

**Найти неактивные контексты (старше 7 дней):**
```sql
SELECT COUNT(*) as stale_contexts
FROM user_contexts
WHERE updated_at < datetime('now', '-7 days');
```

**Очистить старые контексты (старше 30 дней):**
```sql
DELETE FROM user_contexts
WHERE updated_at < datetime('now', '-30 days');
```

**Проверить размер контекста конкретного пользователя:**
```sql
SELECT user_id, mode, 
       LENGTH(context_data) as size_bytes,
       message_count,
       updated_at
FROM user_contexts
WHERE user_id = ?;
```

---

## 8. КОНТРОЛЬНЫЕ ВОПРОСЫ

Перед началом реализации уточните:

1. **Нужен ли контекст для режима `gigachat` (RAG)?**  
   → *Решение:* **ДА**, добавить контекст для всех 4 режимов (`gigachat`, `gigachatpro`, `chatgpt`, `gemini`)

2. **Нужна ли команда `/history` для просмотра контекста?**  
   → *Предложение:* добавить в будущем, если потребуется

3. **Нужна ли периодическая очистка старых контекстов из БД?**  
   → *Предложение:* добавить CRON-задачу для удаления контекстов старше N дней (опционально)

4. **Какой TTL для контекста в Redis оптимален?**  
   → *Текущее значение:* 1 час (достаточно для активной сессии, после истечения загружается из БД)

5. **Нужна ли миграция существующих пользователей?**  
   → *Решение:* не требуется (таблица `user_contexts` создаётся пустой, пользователи начинают с нового контекста)

6. **Как обрабатывать `asyncio.run()` в синхронных функциях?**  
   → *Решение:* временно допустимо для совместимости, в будущем рефакторить на полностью асинхронные функции

7. **Нужна ли индексация по `updated_at` для очистки старых контекстов?**  
   → *Решение:* ДА, индекс уже добавлен в схему БД (`idx_user_contexts_updated`)

---

## 9. АРХИТЕКТУРА ХРАНЕНИЯ КОНТЕКСТА

### 9.1. Диаграмма потоков данных

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Обработка сообщения                          │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
         ┌────────────────────────────────────────────┐
         │  get_user_context(user_id, mode)           │
         └────────────────────────────────────────────┘
                                  │
         ┌────────────────────────┴─────────────────────────┐
         │                                                   │
         ▼                                                   ▼
  ┌─────────────┐                                    ┌─────────────┐
  │   Redis?    │─── НЕТ ────────────────────────────▶│  Память?    │
  └─────────────┘                                    └─────────────┘
         │ ДА                                               │ ДА
         ▼                                                  ▼
  ┌─────────────┐                                    ┌─────────────┐
  │ Есть в кэше?│                                    │ Есть в dict?│
  └─────────────┘                                    └─────────────┘
         │                                                  │
    ДА   │   НЕТ                                      ДА   │   НЕТ
    ┌────┴────┐                                      ┌────┴────┐
    │         │                                      │         │
    ▼         ▼                                      ▼         ▼
 Вернуть  ┌────────────────────────────────┐    Вернуть  ┌──────────┐
 контекст │ load_user_context(user_id, mode)│  контекст │   БД      │
          └────────────────────────────────┘           └──────────┘
                         │                                    │
                         ▼                                    ▼
                  ┌─────────────┐                      ┌─────────────┐
                  │  Есть в БД? │                      │  Есть в БД? │
                  └─────────────┘                      └─────────────┘
                         │                                    │
                    ДА   │   НЕТ                         ДА   │   НЕТ
                    ┌────┴────┐                         ┌────┴────┐
                    │         │                         │         │
                    ▼         ▼                         ▼         ▼
              Кэшировать   Вернуть               Кэшировать   Вернуть
              в Redis      дефолт               в память     дефолт
                    │         │                         │         │
                    └────┬────┘                         └────┬────┘
                         │                                    │
                         └──────────────┬─────────────────────┘
                                        ▼
                              ┌──────────────────┐
                              │ Добавить user msg│
                              └──────────────────┘
                                        │
                                        ▼
                              ┌──────────────────┐
                              │  Обрезка токенов │
                              └──────────────────┘
                                        │
                                        ▼
                              ┌──────────────────┐
                              │  Отправить в LLM │
                              └──────────────────┘
                                        │
                                        ▼
                              ┌──────────────────┐
                              │Добавить assistant│
                              │     response     │
                              └──────────────────┘
                                        │
                                        ▼
         ┌────────────────────────────────────────────┐
         │  set_user_context(user_id, mode, context)  │
         └────────────────────────────────────────────┘
                                  │
         ┌────────────────────────┼─────────────────────────┐
         │                        │                         │
         ▼                        ▼                         ▼
  ┌─────────────┐         ┌─────────────┐          ┌─────────────┐
  │ Сохранить   │         │ Сохранить   │          │ Сохранить   │
  │  в Redis    │         │  в память   │          │    в БД     │
  │  (TTL 1ч)   │         │  (сессия)   │          │ (постоянно) │
  └─────────────┘         └─────────────┘          └─────────────┘
```

### 9.2. Время жизни контекста

| Хранилище   | Время жизни                    | Назначение                          |
|-------------|--------------------------------|-------------------------------------|
| **Redis**   | 1 час (TTL)                    | Быстрый кэш для активных диалогов   |
| **Память**  | До перезапуска (только dev)    | Fallback для одиночного процесса    |
| **БД**      | Бесконечно (до `/clear`)       | Постоянное хранилище                |

### 9.3. Сценарии использования

#### Сценарий 1: Активный пользователь (< 1 час неактивности)
1. Пользователь отправляет сообщение
2. Контекст загружается из Redis (кэш)
3. LLM обрабатывает запрос с историей
4. Контекст сохраняется в Redis (обновляется TTL) + БД

**Производительность:** ~1-2 мс на чтение/запись Redis

#### Сценарий 2: Вернувшийся пользователь (> 1 час неактивности)
1. Пользователь отправляет сообщение
2. Redis возвращает `None` (TTL истёк)
3. Контекст загружается из БД (~5-10 мс)
4. Контекст кэшируется в Redis
5. LLM обрабатывает запрос с восстановленной историей
6. Контекст сохраняется в Redis (новый TTL) + БД

**Производительность:** первый запрос ~10-15 мс, последующие ~1-2 мс

#### Сценарий 3: Новый пользователь
1. Пользователь отправляет первое сообщение
2. Redis и БД возвращают `None`
3. Создаётся новый контекст с system-промптом
4. LLM обрабатывает запрос
5. Контекст сохраняется в Redis + БД

#### Сценарий 4: Перезагрузка Gunicorn
1. Redis очищается (если не persistent)
2. При следующем обращении контекст восстанавливается из БД
3. Диалог продолжается без потерь

---

**Конец технического задания**
