"""Обработчик режима aiagent (RAG-поиск по документам колледжа)."""
from fastapi import Request

import token_utils
import db
from rag_chain import ask_rag
from handlers.base import ModeHandler
from global_state import (
    get_user_context_async,
    set_user_context_async,
    MAX_CONTEXT_MESSAGES,
    MODELS,
)
from utils import logger


class GigachatHandler(ModeHandler):
    """Обработка режима aiagent — RAG-поиск с историей диалога."""

    async def handle(
        self,
        request: Request,
        user_text: str,
        sender: dict,
    ) -> str | None:
        user_id = int(sender.get("user_id"))

        # 1. Загружаем контекст
        context = await get_user_context_async(user_id, "aiagent")

        # 2. Добавляем сообщение пользователя
        context.append({"role": "user", "content": user_text})

        # 3. Получаем RAG ответ (поиск по текущему вопросу)
        lc_llm = request.app.state.giga_lc_client
        answer = await ask_rag(user_text=user_text, lc_llm=lc_llm)

        # 4. Добавляем ответ в контекст
        context.append({"role": "assistant", "content": answer})

        # 5. Обрезаем контекст по лимиту токенов модели
        model_name = MODELS.get("aiagent")
        context = token_utils.truncate_messages_for_token_limit(
            context,
            model=model_name,
            reserve_tokens=2500,
        )

        # 6. Обрезаем контекст: system + последние MAX_CONTEXT_MESSAGES пар
        system_msgs = [m for m in context if m.get("role") == "system"]
        non_system = [m for m in context if m.get("role") != "system"]
        if len(non_system) > MAX_CONTEXT_MESSAGES * 2:
            non_system = non_system[-(MAX_CONTEXT_MESSAGES * 2):]
        context = system_msgs + non_system

        # 7. Сохраняем контекст
        await set_user_context_async(user_id, "aiagent", context)

        logger.info(
            f"aiagent: user_id={user_id}, "
            f"сообщений в контексте={len(non_system) // 2}"
        )

        # 8. Биллинг
        from cost_tracker import calculate_cost
        cost = calculate_cost(usage=None, model=MODELS.get("aiagent"), mode="aiagent")
        await db.add_billing(user_id, "aiagent", user_text, 0, cost)

        return answer
