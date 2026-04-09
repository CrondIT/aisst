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

    