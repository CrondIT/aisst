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
