#!/bin/bash
# Скрипт запуска AISST в tmux-сессии с отдельными окнами для каждого процесса

SESSION_NAME="aisst"

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

# Активация виртуального окружения
source .venv/bin/activate

# Создаём новую сессию с первым окном (rag_worker)
echo "Создание tmux-сессии '$SESSION_NAME'..."
tmux new-session -d -s "$SESSION_NAME" -n "rag_worker" \
    "python -m rag_chain.rag_worker"

# Добавляем окно для redis_listener
tmux new-window -t "$SESSION_NAME" -n "redis_listener" \
    "python -m redis_utils.redis_listener"

# Добавляем окно для gunicorn
tmux new-window -t "$SESSION_NAME" -n "gunicorn" \
    "gunicorn main:app \
        --workers 2 \
        --worker-class uvicorn.workers.UvicornWorker \
        --bind 0.0.0.0:8000 \
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
echo "  2. redis_listener  — слушатель результатов"
echo "  3. gunicorn        — веб-сервер"
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
