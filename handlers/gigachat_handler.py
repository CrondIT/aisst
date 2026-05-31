"""Обработчик режима gigachat (RAG-поиск по документам колледжа)."""
from fastapi import Request

import db
from rag_chain import ask_rag
from handlers.base import ModeHandler


class GigachatHandler(ModeHandler):
    """Обработка режима gigachat — RAG-поиск."""

    async def handle(
        self,
        request: Request,
        user_text: str,
        sender: dict,
    ) -> str | None:
        user_id = int(sender.get("user_id"))
        lc_llm = request.app.state.giga_lc_client
        answer = await ask_rag(user_text=user_text, lc_llm=lc_llm)
        await db.add_billing(user_id, "gigachat", user_text, 0, 2)
        return answer
