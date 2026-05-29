#!/bin/bash
# Остановка всех компонентов AISST

echo "Остановка AISST..."

# Остановка tmux-сессии (если запущено через tmux_start.sh)
SESSION_NAME="aisst"
if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    echo "⚠️  Остановка tmux-сессии '$SESSION_NAME'..."
    tmux kill-session -t "$SESSION_NAME"
fi

# Остановка Gunicorn
pkill -f "gunicorn main:app" && echo "✓ Gunicorn остановлен" || true

# Остановка RAG Worker (общий паттерн — ловит любой формат запуска)
pkill -f "rag_worker" && echo "✓ RAG Worker остановлен" || true

# Остановка Redis Listener (общий паттерн — ловит любой формат запуска)
pkill -f "redis_listener" && echo "✓ Redis Listener остановлен" || true

echo "Готово"