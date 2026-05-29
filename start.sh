#!/bin/bash
# Скрипт запуска всех компонентов AISST

set -e

echo "=========================================="
echo "Запуск AISST"
echo "=========================================="

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

# Проверка Redis
echo "Проверка Redis..."
redis-cli ping > /dev/null 2>&1 && echo "✓ Redis подключён" || echo "✗ Redis недоступен"

# Запуск RAG Worker (фоновый процесс)
echo "Запуск RAG Worker..."
python -m rag_chain.rag_worker &
RAG_PID=$!
echo "RAG Worker запущен (PID: $RAG_PID)"

# Запуск Redis Listener (для уведомлений)
echo "Запуск Redis Listener..."
python -m redis_utils.redis_listener &
LISTENER_PID=$!
echo "Redis Listener запущен (PID: $LISTENER_PID)"

# Запуск Gunicorn с увеличенным timeout
echo "Запуск Gunicorn..."
gunicorn main:app \
    --workers 2 \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8000 \
    --timeout 300 \
    --keep-alive 60 \
    --access-logfile - \
    --error-logfile - &

GUNICORN_PID=$!
echo "Gunicorn запущен (PID: $GUNICORN_PID)"

echo ""
echo "=========================================="
echo "Все компоненты запущены:"
echo "  - RAG Worker:      $RAG_PID"
echo "  - Redis Listener:  $LISTENER_PID"
echo "  - Gunicorn:        $GUNICORN_PID"
echo "=========================================="
echo ""
echo "Для остановки всех процессов:"
echo "  kill $RAG_PID $LISTENER_PID $GUNICORN_PID"
echo ""

# Ожидание сигнала завершения
wait