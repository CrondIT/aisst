# Техническое задание: Декомпозиция `bot_logic.py` (God Object)

**Проект:** `aisst` / FastAPI + LangChain + MAX Messenger  
**Приоритет:** Средний (maintainability, расширяемость)  
**Оценка трудоёмкости:** 3–5 часов

---

## 1. Контекст и проблема

`bot_logic.py` (432 строки) является God Object — единственным файлом, который одновременно:

- содержит карту команд (`mode_map`)
- является диспетчером команд (`handle_command`)
- является диспетчером сообщений (`handle_message`) с монолитным `match/case` на все режимы
- содержит инлайн-логику RAG-режима (~50 строк внутри `case "rag":`)
- содержит обёртку над `prompt_edit.py` (`_handle_edit_mode`)
- содержит вспомогательные функции (`_get_file_extracted_text`, `_check_and_send_formatted`)
- обрабатывает файлы и изображения (`handle_file`, `handle_image`)

При добавлении нового режима или изменении существующего разработчик вынужден редактировать один файл, в котором нет изоляции между режимами. `case "gigachatpro"`, `case "chatgpt"` и `case "gemini"` содержат идентичный код с отличием только в клиенте и модели.

---

## 2. Текущая структура (as-is)

```
bot_logic.py (432 строки)
├── mode_map: dict                        # карта команд
├── handle_command(user_text, sender)     # диспетчер /команд
├── handle_message(request, user_text, sender)
│   └── match user_mode:
│       ├── case "gigachat"               # RAG, ~5 строк
│       ├── case "gigachatpro"            # прямой LLM, ~15 строк
│       ├── case "chatgpt"                # прямой LLM, ~15 строк (дубль)
│       ├── case "gemini"                 # прямой LLM, ~15 строк (дубль)
│       ├── case "mentor"                 # делегация mentor_logic.py
│       ├── case "edit"                   # делегация _handle_edit_mode()
│       └── case "rag"                    # инлайн-логика, ~50 строк
├── handle_image(request, image_path, sender)
├── handle_file(file_name, sender)
├── _get_file_extracted_text(user_id)     # вспомогательная
├── _check_and_send_formatted(...)        # вспомогательная
└── _handle_edit_mode(user_text, sender)  # обёртка над prompt_edit.py

prompt_edit.py (345 строк)              # частично вынесено, но точка входа
mentor/mentor_logic.py                  # вынесено корректно
rag_chain/                              # вынесено корректно
```

---

## 3. Целевая структура (to-be)

```
handlers/
├── __init__.py                           # экспорт всех хэндлеров
├── base.py                               # протокол/интерфейс ModeHandler
├── gigachat_handler.py                   # режим gigachat (RAG)
├── llm_handler.py                        # режимы gigachatpro / chatgpt / gemini
├── rag_handler.py                        # режим rag (управление документами)
└── edit_handler.py                       # режим edit (управление промптами)

bot_logic.py (рефакторинг, ~120 строк)
├── mode_map: dict
├── handle_command(user_text, sender)     # без изменений
├── handle_message(request, user_text, sender)  # тонкий диспетчер
├── handle_image(request, image_path, sender)   # без изменений
└── handle_file(file_name, sender)              # без изменений

shared/
└── message_utils.py                      # _get_file_extracted_text,
                                          # _check_and_send_formatted
```

---

## 4. Требования к реализации

### 4.1. Протокол хэндлера (`handlers/base.py`)

Определить `Protocol` для единого интерфейса всех хэндлеров режимов:

```python
# Псевдокод
from typing import Protocol
from fastapi import Request

class ModeHandler(Protocol):
    async def handle(
        self,
        request: Request,
        user_text: str,
        sender: dict,
    ) -> str | None:
        ...
```

Все хэндлеры должны реализовывать этот протокол. Это позволит `handle_message()` работать с ними через единый вызов `handler.handle(request, user_text, sender)`.

### 4.2. `handlers/gigachat_handler.py` — режим `gigachat`

Инкапсулирует вызов `ask_rag()` и биллинг.

**Входные зависимости:**
- `request.app.state.giga_lc_client`
- `ask_rag()` из `rag_chain`
- `db.add_billing()`

**Логика:** перенести `case "gigachat":` из `bot_logic.py:157–163` без изменений.

### 4.3. `handlers/llm_handler.py` — режимы `gigachatpro`, `chatgpt`, `gemini`

Три `case`-ветки в текущем `handle_message()` (строки 165–229) являются идентичными по структуре и отличаются только:
- именем атрибута клиента в `request.app.state` (`giga_client` / `openai_client` / `gemini_client`)
- строкой модели (`"GigaChat"` / `"gpt-5.2-chat-latest"` / `"gemini-2.5-pro"`)
- сообщением об ошибке при отсутствии клиента

Создать единый параметризованный хэндлер `LlmDirectHandler` с конфигурацией через `__init__`:

```python
# Псевдокод
class LlmDirectHandler:
    def __init__(
        self,
        client_attr: str,    # имя атрибута в app.state
        model_name: str,     # строка модели
        mode_name: str,      # для биллинга
        error_msg: str,      # если клиент не настроен
    ): ...

    async def handle(self, request, user_text, sender) -> str | None:
        # единая логика: full_prompt → client.chat → add_billing
        # → _check_and_send_formatted
        ...
```

Зарегистрировать три экземпляра в диспетчере:

```python
HANDLERS: dict[str, ModeHandler] = {
    "gigachatpro": LlmDirectHandler("giga_client",   "GigaChat",              "gigachatpro", "..."),
    "chatgpt":     LlmDirectHandler("openai_client",  "gpt-5.2-chat-latest",  "chatgpt",     "..."),
    "gemini":      LlmDirectHandler("gemini_client",  "gemini-2.5-pro",       "gemini",      "..."),
}
```

### 4.4. `handlers/rag_handler.py` — режим `rag`

Перенести инлайн-логику из `case "rag":` (`bot_logic.py:237–282`).

**Логика (без изменений):**
- Проверка `get_user_pending_delete()` → подтверждение/отмена удаления
- `"ls"` → `get_all_filenames_from_vector_db()`
- Поиск по имени → `set_user_pending_delete()` + запрос подтверждения

**Входные зависимости:**
- `get_user_pending_delete`, `set_user_pending_delete`, `clear_user_pending_delete`
- `get_all_filenames_from_vector_db`, `delete_file_from_vector_db`
- `db.add_billing()`

### 4.5. `handlers/edit_handler.py` — режим `edit`

Перенести `_handle_edit_mode()` из `bot_logic.py:377–431` и всю делегацию в `prompt_edit.py`.

Точка входа остаётся прежней, но размещается в изолированном модуле.

**Файл `prompt_edit.py` не трогать** — он останется хранилищем state-machine функций. `edit_handler.py` является их оркестратором (текущая роль `_handle_edit_mode`).

### 4.6. `shared/message_utils.py` — общие утилиты

Перенести из `bot_logic.py`:
- `_get_file_extracted_text(user_id)` → `get_file_extracted_text(user_id)`
- `_check_and_send_formatted(user_text, user_id, answer)` → `check_and_send_formatted(...)`

Убрать префикс `_` (функции становятся публичным API пакета `shared`). Импортировать в `llm_handler.py` и `gigachat_handler.py`.

> Директория `shared/` может уже существовать или создаётся как новый пакет (`__init__.py`).

### 4.7. Рефакторинг `bot_logic.py`

После выноса всех хэндлеров `bot_logic.py` сводится к тонкому диспетчеру (~120 строк):

```python
# Псевдокод handle_message после рефакторинга
HANDLERS: dict[str, ModeHandler] = {
    "gigachat":    GigachatHandler(),
    "gigachatpro": LlmDirectHandler(...),
    "chatgpt":     LlmDirectHandler(...),
    "gemini":      LlmDirectHandler(...),
    "mentor":      MentorHandler(),   # обёртка над mentor_logic.handle_mentor_mode
    "edit":        EditHandler(),
    "rag":         RagHandler(),
}

async def handle_message(request, user_text, sender) -> str | None:
    user_id = int(sender.get("user_id"))
    user_mode = get_user_mode(user_id) or "gigachat"
    set_user_mode(user_id, user_mode)

    logger.info(f"handle_message: user_id={user_id}, mode={user_mode}")

    handler = HANDLERS.get(user_mode)
    if handler is None:
        return "Используйте /gigachat для начала общения с ИИ."

    return await handler.handle(request, user_text, sender)
```

Функции `handle_command`, `handle_file`, `handle_image` и `mode_map` остаются в `bot_logic.py` без изменений.

### 4.8. `handlers/__init__.py`

Экспортировать все хэндлеры и словарь `HANDLERS` для возможного использования в тестах:

```python
from .gigachat_handler import GigachatHandler
from .llm_handler import LlmDirectHandler
from .rag_handler import RagHandler
from .edit_handler import EditHandler
```

---

## 5. Что не трогать

| Файл | Причина |
|---|---|
| `prompt_edit.py` | Уже изолирован, содержит state-machine; `edit_handler.py` просто делегирует в него |
| `mentor/mentor_logic.py` | Уже корректно вынесен; добавить тонкую обёртку `MentorHandler` по желанию |
| `rag_chain/` | Уже изолирован; хэндлеры только импортируют из него |
| `max_update_handler.py` | Вызывает `handle_command`, `handle_message`, `handle_file` — сигнатуры не меняются |
| `routers.py` | Не импортирует `bot_logic.py` напрямую через хэндлеры |

---

## 6. Порядок выполнения (чтобы не сломать работающий код)

1. Создать директорию `handlers/` с пустым `__init__.py`
2. Создать `shared/message_utils.py`, перенести `_get_file_extracted_text` и `_check_and_send_formatted`; добавить реэкспорт старых имён в `bot_logic.py` для обратной совместимости на время рефакторинга
3. Создать `handlers/rag_handler.py` — самый изолированный, нет зависимостей от `request.app.state`
4. Создать `handlers/edit_handler.py` — перенести `_handle_edit_mode` как есть
5. Создать `handlers/llm_handler.py` — объединить три идентичных `case`
6. Создать `handlers/gigachat_handler.py`
7. Обновить `bot_logic.py` — заменить `match/case` на словарь `HANDLERS`
8. Удалить реэкспорты из шага 2, убедиться что импорты не сломаны
9. Прогнать ручное тестирование всех режимов

---

## 7. Риски

| Риск | Мера |
|---|---|
| Сломать `max_update_handler.py` | Сигнатуры `handle_command`, `handle_message`, `handle_file` не меняются — только внутренняя реализация |
| Циклические импорты | Хэндлеры импортируют из `rag_chain`, `db`, `global_state` — не из `bot_logic`. Круговых зависимостей нет |
| Потеря состояния `user_mode` | Логика `get_user_mode` / `set_user_mode` остаётся в `bot_logic.handle_message` до делегации в хэндлер |
| `LlmDirectHandler` — слишком общий | Если у режимов появится diverging логика — разбить обратно на отдельные файлы без изменения интерфейса |

---

## 8. Критерий готовности

- [ ] `bot_logic.py` не содержит инлайн-логики конкретных режимов — только диспетчер
- [ ] Каждый режим (`gigachat`, `gigachatpro`, `chatgpt`, `gemini`, `rag`, `edit`) живёт в отдельном файле в `handlers/`
- [ ] `match/case` в `handle_message` заменён на словарный `HANDLERS.get(user_mode)`
- [ ] Сигнатуры `handle_command`, `handle_message`, `handle_file` не изменились
- [ ] Все режимы работают корректно после рефакторинга (ручное тестирование)
- [ ] Добавление нового режима требует: создать файл в `handlers/`, добавить запись в `HANDLERS` — и только это
