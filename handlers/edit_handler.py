"""Обработчик режима редактирования промптов."""
from fastapi import Request

import db
from global_state import get_prompt_edit_state
from prompt_edit import (
    _edit_mode_idle,
    _edit_mode_list,
    _edit_mode_view,
    _edit_mode_edit_system,
    _edit_mode_edit_human,
    _edit_mode_confirm,
)
from handlers.base import ModeHandler


class EditHandler(ModeHandler):
    """Обработка режима edit — редактирование промптов."""

    async def handle(
        self,
        request: Request,
        user_text: str,
        sender: dict,
    ) -> str | None:
        user_id = int(sender.get("user_id"))
        user_data = await db.get_user(user_id)

        if user_data is None or user_data.get("permission") != 0:
            return "⛔ Редактирование промптов доступно только администраторам."

        edit_state = get_prompt_edit_state(user_id)
        user_text = user_text.strip()

        if edit_state is None:
            return await _edit_mode_idle(user_text, user_id)

        stage = edit_state.get("stage")

        if stage == "idle":
            return await _edit_mode_idle(user_text, user_id)

        if stage == "list":
            return await _edit_mode_list(user_text, user_id, edit_state)

        if stage == "view":
            result = await _edit_mode_view(user_text, user_id, edit_state)
            # Перезапрашиваем состояние для обработки результата
            new_state = get_prompt_edit_state(user_id)
            if new_state:
                edit_state = new_state
                stage = new_state.get("stage", "")
            return result

        if stage == "edit_system":
            return await _edit_mode_edit_system(user_text, user_id, edit_state)

        if stage == "edit_human":
            return await _edit_mode_edit_human(user_text, user_id, edit_state)

        if stage == "confirm":
            return await _edit_mode_confirm(user_text, user_id, edit_state)

        return "Произошла ошибка. Напишите 'отмена' для сброса."
