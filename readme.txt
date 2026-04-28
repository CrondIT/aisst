uvicorn main:app --reload
uvicorn bot:app --reload

# Из любого модуля
      2 from gigachat import gigachat
      3
      4 # Простой запрос
      5 answer = await gigachat.ask("Что такое Python?")
      6
      7 # С системной инструкцией
      8 answer = await gigachat.ask("Объясни код", system_prompt="Ты — учитель программирования")
      9
     10 # С историей диалога
     11 messages = [
     12     {"role": "user", "content": "Привет!"},
     13     {"role": "assistant", "content": "Здравствуйте!"},
     14     {"role": "user", "content": "Как дела?"},
     15 ]
     16 answer = await gigachat.ask_with_history(messages)


     https://github.com/zhanymkanov/fastapi-best-practices


     Скопируй на сервер и активируй:

     1 # Копируем юнит
     2 scp aisst.service root@cv6438947:/etc/systemd/system/aisst.service
     3
     4 # На сервере:
     5 systemctl daemon-reload
     6 systemctl enable aisst
     7 systemctl start aisst
     8 systemctl status aisst

    Теперь сервис будет запускаться автоматически при перезагрузке и рестартовать при падении.

    gunicorn -w 1 -k uvicorn.workers.UvicornWorker main:app --bind unix:/tmp/fastapi.sock --umask 000

Согласно документации MAX API, существуют следующие типы обновлений:
Основные типы (из подписки):
В вашем коде max_api.py:188 подписка включает только два:
- message_created — получено новое сообщение
- message_callback — нажатие на инлайн-кнопку
Полный список доступных типов:
Из документации и TypeScript-определений MAX API:
Сообщения:
- message_created — создание нового сообщения
- message_edited — редактирование сообщения
- message_removed — удаление сообщения
- message_callback — нажатие на callback-кнопку
- message_construction_request — запрос на создание сообщения
- message_constructed — сообщение создано
Участники:
- user_added — добавление пользователя в чат
- user_removed — удаление пользователя из чата
Бот:
- bot_added — бот добавлен в чат/группу
- bot_removed — бот удален из чата/группы
- bot_started — бот запущен (первое взаимодействие)
Чат:
- chat_title_changed — изменение названия чата
- message_chat_created — создание нового чата
- dialog_cleared — история диалога очищена
- dialog_removed — диалог полностью удален
Текущая реализация
В process_update (max_api.py:226) обрабатываются только:
- message_callback (line 234)
- message_created (line 248)

10. Обработка текстовых команд (line 341-354)
Если текст начинается с /:
- /start — показывает инлайн-кнопки через send_inline_message() (если permission != 1)
- Остальные команды — bot_logic.handle_command()
11. Обработка обычных сообщений (line 356-375)
reply_text = await bot_logic.handle_message(request, user_text, sender)
- Если ответ пустой — отправляет "Извините, не смог сформировать ответ."
- Если ответ > 4000 символов — обрезает
- Отправляет через send_message()
- При исключении — отправляет текст ошибки
Ключевые зависимости
- bot_logic.handle_command() — обработка команд
- bot_logic.handle_message() — обработка обычных сообщений
- db — работа с базой пользователей
- _process_file_async() — асинхронная обработка файлов
- send_message() / send_inline_message() — отправка ответов
    