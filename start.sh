#!/bin/bash
# Скрипт запуска всех компонентов AISST

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Пути к исполняемым файлам из venv
PYTHON="$SCRIPT_DIR/.venv/bin/python"
GUNICORN="$SCRIPT_DIR/.venv/bin/gunicorn"

# Очистка старых процессов перед запуском
echo "Проверка запущенных процессов..."
if pgrep -f "rag_worker" > /dev/null 2>&1; then
    echo "⚠️  Остановка старых RAG Worker..."
    pkill -f "rag_worker" || true
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
    echo "Создайте: python3 -m venv .venv && pip install -r requirements.txt"
    exit 1
fi

# Проверка Redis
echo "Проверка Redis..."
redis-cli ping > /dev/null 2>&1 && echo "✓ Redis подключён" || echo "✗ Redis недоступен"

# Запуск RAG Worker (фоновый процесс)
echo "Запуск RAG Worker..."
$PYTHON -m rag_chain.rag_worker &
RAG_PID=$!
echo "RAG Worker запущен (PID: $RAG_PID)"

# Запуск Image Worker (фоновый процесс)
echo "Запуск Image Worker..."
$PYTHON image_worker.py &
IMAGE_PID=$!
echo "Image Worker запущен (PID: $IMAGE_PID)"

# Запуск Redis Listener (для уведомлений)
echo "Запуск Redis Listener..."
$PYTHON -m redis_utils.redis_listener &
LISTENER_PID=$!
echo "Redis Listener запущен (PID: $LISTENER_PID)"

# Запуск Gunicorn с увеличенным timeout
echo "Запуск Gunicorn..."
$GUNICORN main:app \
    --workers 2 \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind unix:/tmp/fastapi.sock \
    --umask 000 \
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
echo "  - Image Worker:    $IMAGE_PID"
echo "  - Redis Listener:  $LISTENER_PID"
echo "  - Gunicorn:        $GUNICORN_PID"
echo "=========================================="
echo ""
echo "Для остановки всех процессов:"
echo "  kill $RAG_PID $IMAGE_PID $LISTENER_PID $GUNICORN_PID"
echo ""

# Ожидание сигнала завершения
wait