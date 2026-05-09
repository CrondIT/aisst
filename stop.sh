#!/bin/bash
# Остановка всех компонентов AISST

echo "Остановка AISST..."

# Остановка Gunicorn
pkill -f "gunicorn main:app" && echo "✓ Gunicorn остановлен" || true

# Остановка RAG Worker
pkill -f "python rag_worker.py" && echo "✓ RAG Worker остановлен" || true

# Остановка Redis Listener
pkill -f "redis_listener" && echo "✓ Redis Listener остановлен" || true

echo "Готово"