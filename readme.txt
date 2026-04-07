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