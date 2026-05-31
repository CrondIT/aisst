"""Базовый протокол для обработчиков режимов."""
from typing import Protocol
from fastapi import Request


class ModeHandler(Protocol):
    """Протокол для единообразной обработки сообщений в разных режимах."""

    async def handle(
        self,
        request: Request,
        user_text: str,
        sender: dict,
    ) -> str | None:
        ...
