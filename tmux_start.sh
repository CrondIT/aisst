#!/bin/bash
# Скрипт запуска AISST в tmux-сессии с отдельными окнами для каждого процесса

SESSION_NAME="aisst"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Пути к исполняемым файлам из venv
PYTHON="$SCRIPT_DIR/.venv/bin/python"
GUNICORN="$SCRIPT_DIR/.venv/bin/gunicorn"

# Если сессия уже существует — убиваем её
if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    echo "⚠️  Сессия '$SESSION_NAME' уже существует, удаляю..."
    tmux kill-session -t "$SESSION_NAME"
    sleep 1
fi

# Очистка старых процессов перед запуском
echo "Проверка запущенных процессов..."
if pgrep -f "rag_worker" > /dev/null 2>&1; then
    echo "⚠️  Остановка старых RAG Worker..."
    pkill -f "rag_worker" || true
    sleep 1
fi
if pgrep -f "llm_worker" > /dev/null 2>&1; then
    echo "⚠️  Остановка старых LLM Worker..."
    pkill -f "llm_worker" || true
    sleep 1
fi
if pgrep -f "image_worker" > /dev/null 2>&1; then
    echo "⚠️  Остановка старых Image Worker..."
    pkill -f "image_worker" || true
    sleep 1
fi
if pgrep -f "redis_listener" > /dev/null 2>&1; then
    echo "⚠️  Остановка старых Redis Listener..."
    pkill -f "redis_listener" || true
    sleep 1
fi
if pgrep -f "gunicorn main:app" > /dev/null 2>&1; then
    echo "⚠️  Остановка старого Gunicorn..."
    pkill -f "gunicorn main:app" || true
    sleep 1
fi

# Проверка venv
if [ ! -f "$PYTHON" ]; then
    echo "❌ Виртуальное окружение не найдено: $PYTHON"
    echo "Создайте: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

# Создаём новую сессию с первым окном (rag_worker)
echo "Создание tmux-сессии '$SESSION_NAME'..."
tmux new-session -d -s "$SESSION_NAME" -n "rag_worker" \
    "$PYTHON -m rag_chain.rag_worker"

# Добавляем окно для llm_worker
tmux new-window -t "$SESSION_NAME" -n "llm_worker" \
    "$PYTHON llm_worker.py"

# Добавляем окно для image_worker
tmux new-window -t "$SESSION_NAME" -n "image_worker" \
    "$PYTHON image_worker.py"

# Добавляем окно для redis_listener
tmux new-window -t "$SESSION_NAME" -n "redis_listener" \
    "$PYTHON -m redis_utils.redis_listener"

# Добавляем окно для gunicorn
tmux new-window -t "$SESSION_NAME" -n "gunicorn" \
    "$GUNICORN main:app \
        --workers 2 \
        --worker-class uvicorn.workers.UvicornWorker \
        --bind unix:/tmp/fastapi.sock \
        --umask 000 \
        --timeout 300 \
        --keep-alive 60 \
        --access-logfile - \
        --error-logfile -"

# Переключаемся на первое окно
tmux select-window -t "$SESSION_NAME:0"

echo ""
echo "=========================================="
echo "AISST запущен в tmux-сессии '$SESSION_NAME'"
echo "=========================================="
echo ""
echo "Окна:"
echo "  1. rag_worker      — обработка RAG задач"
echo "  2. llm_worker      — LLM запросы (chat, gigachat, gemini)"
echo "  3. image_worker    — генерация изображений"
echo "  4. redis_listener  — слушатель результатов"
echo "  5. gunicorn        — веб-сервер"
echo ""
echo "Управление:"
echo "  tmux attach -t $SESSION_NAME     — подключиться"
echo "  tmux kill-session -t $SESSION_NAME — остановить всё"
echo "  Ctrl+B, N                         — переключить окно"
echo "  Ctrl+B, W                         — список окон"
echo ""

# Если мы не в tmux — подключаемся к сессии
if [ -z "$TMUX" ]; then
    tmux attach -t "$SESSION_NAME"
fi
