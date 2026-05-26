"""Модуль редактирования промптов."""
from sqlalchemy import select, func

from db import AsyncSessionLocal, PromptVersion
from global_state import set_prompt_edit_state, clear_prompt_edit_state
from prompt_repository import PromptRepository
from mentor.mentor_chain import invalidate_prompt_cache
from rag_chain import invalidate_rag_prompt_cache


async def _edit_mode_idle(user_text: str, user_id: int) -> str:
    """Начальное состояние режима редактирования - список команд."""
    user_text_lower = user_text.lower()

    if user_text_lower in ("список", "list", "ls", "промпты"):
        prompts = await PromptRepository.list_prompts()
        if not prompts:
            return "❌ Промпты не найдены в базе."

        lines = ["📝 <b>Список промптов:</b>\n"]
        for i, p in enumerate(prompts, 1):
            lines.append(
                f"{i}. <b>{p['prompt_key']}</b>\n"
                f"   Описание: {p['description']}\n"
                f"   System: {p['system_text'][:80]}...\n"
                f"   Версий: {p['version_count']}"
            )
        lines.append("\nКоманды:\n• 'промпт: X' — выбрать промпт\n• 'отмена' — выход")
        set_prompt_edit_state(user_id, {"stage": "list"})
        return "\n".join(lines)

    if (user_text_lower.startswith("промпт:") or
        user_text_lower.startswith("выбрать:") or
        user_text_lower.startswith("prompt:") or
        user_text_lower.startswith("select:")):
        key = user_text.split(":", 1)[1].strip()
        set_prompt_edit_state(user_id, {"stage": "list"})
        return await _select_prompt(key, user_id)

    if user_text_lower in ("отмена", "cancel", "exit", "выход"):
        clear_prompt_edit_state(user_id)
        return "✅ Режим редактирования завершён."

    return (
        "📝 <b>Режим редактирования промптов</b>\n\n"
        "Команды:\n"
        "• 'список' — показать все промпты\n"
        "• 'промпт: X' — выбрать промпт для редактирования\n"
        "• 'отмена' — выйти из режима"
    )


async def _edit_mode_list(user_text: str, user_id: int, edit_state: dict) -> str:
    """Обработка списка промптов."""
    user_text_lower = user_text.lower()

    if (user_text_lower.startswith("промпт:") or
        user_text_lower.startswith("выбрать:") or
        user_text_lower.startswith("prompt:") or
        user_text_lower.startswith("select:")):
        key = user_text.split(":", 1)[1].strip()
        return await _select_prompt(key, user_id)

    if user_text_lower in ("отмена", "cancel"):
        clear_prompt_edit_state(user_id)
        return "✅ Отмена. Режим редактирования завершён."

    if user_text_lower in ("список", "list", "ls", "промпты"):
        prompts = await PromptRepository.list_prompts()
        if not prompts:
            return "❌ Промпты не найдены."

        lines = ["📝 <b>Список промптов:</b>\n"]
        for i, p in enumerate(prompts, 1):
            lines.append(
                f"{i}. <b>{p['prompt_key']}</b>\n"
                f"   Описание: {p['description']}\n"
                f"   System: {p['system_text'][:80]}...\n"
                f"   Версий: {p['version_count']}"
            )
        return "\n".join(lines)

    return "Неизвестная команда. Напишите 'список' или 'промпт: X'"


async def _select_prompt(key: str, user_id: int) -> str:
    """Выбирает промпт для просмотра/редактирования."""
    prompts = await PromptRepository.list_prompts()

    prompt = None
    for p in prompts:
        if key.lower() == p["prompt_key"].lower():
            prompt = p
            break

    if not prompt:
        available = ", ".join([p["prompt_key"] for p in prompts])
        return f"❌ Промпт '{key}' не найден.\nДоступные: {available}"

    db_prompt = await PromptRepository.get_prompt(key)
    if not db_prompt:
        return f"❌ Ошибка загрузки промпта '{key}'"

    async with AsyncSessionLocal() as db:
        version_count = await db.execute(
            select(func.count(PromptVersion.id))
            .where(PromptVersion.prompt_id == db_prompt.id)
        )
        v_count = version_count.scalar() or 0

    set_prompt_edit_state(user_id, {
        "stage": "view",
        "prompt_key": key,
        "current_system": db_prompt.current_system_text,
        "current_human": db_prompt.current_human_text,
        "new_system": db_prompt.current_system_text,
        "new_human": db_prompt.current_human_text,
        "versions": v_count,
    })

    return (
        f"📄 <b>Промпт: {key}</b>\n\n"
        f"<b>Описание:</b> {prompt['description']}\n\n"
        f"<b>Текущий system prompt:</b>\n{db_prompt.current_system_text}\n\n"
        f"<b>Текущий human prompt:</b>\n{db_prompt.current_human_text or '(пусто)'}\n\n"
        f"<b>Версий в истории:</b> {prompt['version_count']}\n\n"
        "Команды:\n"
        "• 'система:' — редактировать system prompt\n"
        "• 'человек:' — редактировать human prompt\n"
        "• 'сохранить' — сохранить изменения\n"
        "• 'версии' — показать историю версий\n"
        "• 'отмена' — отменить изменения"
    )


async def _edit_mode_view(user_text: str, user_id: int, edit_state: dict) -> str:
    """Просмотр и начало редактирования промпта."""
    user_text_lower = user_text.lower()

    if (user_text_lower.startswith("система:") or
        user_text_lower.startswith("system:")):
        set_prompt_edit_state(user_id, {**edit_state, "stage": "edit_system"})
        return (
            "📝 <b>Редактирование system prompt</b>\n\n"
            f"Текущее значение:\n{edit_state['current_system']}\n\n"
            "Введите новый текст system prompt.\n"
            "Используйте {context}, {topic}, {question}, {answer} как переменные."
        )

    if (user_text_lower.startswith("человек:") or
        user_text_lower.startswith("human:")):
        set_prompt_edit_state(user_id, {**edit_state, "stage": "edit_human"})
        return (
            "📝 <b>Редактирование human prompt</b>\n\n"
            f"Текущее значение:\n{edit_state['current_human'] or '(пусто)'}\n\n"
            "Введите новый текст human prompt.\n"
            "Используйте {question}, {topic}, {answer} как переменные."
        )

    if user_text_lower in ("сохранить", "save"):
        return await _save_prompt(user_id, edit_state)

    if user_text_lower in ("версии", "история", "history", "versions"):
        return await _show_versions(edit_state["prompt_key"])

    if user_text_lower.startswith("откат:") or user_text_lower.startswith("rollback:"):
        try:
            version_num = int(user_text.split(":")[1].strip())
            return await _rollback_prompt(edit_state["prompt_key"], version_num, user_id)
        except ValueError:
            return "❌ Укажите номер версии. Пример: 'откат: 2'"

    if user_text_lower in ("отмена", "cancel"):
        clear_prompt_edit_state(user_id)
        return "✅ Отмена. Редактирование завершено."

    if user_text_lower in ("назад", "back"):
        clear_prompt_edit_state(user_id)
        return "📝 Возврат к списку промптов.\nНапишите 'список' для просмотра."

    if user_text_lower in ("список", "list", "ls"):
        clear_prompt_edit_state(user_id)
        return await _edit_mode_idle(user_text, user_id)

    return "Неизвестная команда. Используйте 'система:', 'человек:', 'сохранить', 'отмена'."


async def _edit_mode_edit_system(user_text: str, user_id: int, edit_state: dict) -> str:
    """Редактирование system prompt."""
    user_text_lower = user_text.lower()

    if user_text_lower in ("отмена", "cancel"):
        set_prompt_edit_state(user_id, {**edit_state, "stage": "view"})
        return "❌ Редактирование system prompt отменено."

    if user_text_lower in ("да", "yes", "save", "сохранить", "ok", "ок"):
        return await _save_prompt(user_id, edit_state)

    if user_text_lower in ("список", "list", "ls", "назад", "back"):
        set_prompt_edit_state(user_id, {**edit_state, "stage": "view"})
        return "📝 Возврат к просмотру промпта. Введите команду."

    if user_text_lower in ("выход", "exit"):
        clear_prompt_edit_state(user_id)
        return "✅ Режим редактирования завершён."

    new_text = user_text.strip()
    if len(new_text) < 10:
        return "❌ Текст слишком короткий. Введите корректный system prompt."

    set_prompt_edit_state(user_id, {**edit_state, "stage": "confirm", "new_system": new_text})

    return (
        "✅ <b>Новый system prompt:</b>\n"
        f"{new_text[:300]}{'...' if len(new_text) > 300 else ''}\n\n"
        "Команды:\n"
        "• 'да' — сохранить изменения\n"
        "• 'отмена' — отменить"
    )


async def _edit_mode_edit_human(user_text: str, user_id: int, edit_state: dict) -> str:
    """Редактирование human prompt."""
    user_text_lower = user_text.lower()

    if user_text_lower in ("отмена", "cancel"):
        set_prompt_edit_state(user_id, {**edit_state, "stage": "view"})
        return "❌ Редактирование human prompt отменено."

    if user_text_lower in ("да", "yes", "save", "сохранить", "ok", "ок"):
        return await _save_prompt(user_id, edit_state)

    if user_text_lower in ("список", "list", "ls", "назад", "back"):
        set_prompt_edit_state(user_id, {**edit_state, "stage": "view"})
        return "📝 Возврат к просмотру промпта. Введите команду."

    if user_text_lower in ("выход", "exit"):
        clear_prompt_edit_state(user_id)
        return "✅ Режим редактирования завершён."

    new_text = user_text.strip()

    set_prompt_edit_state(user_id, {
        **edit_state,
        "stage": "confirm",
        "new_human": new_text,
    })

    return (
        "✅ <b>Новый human prompt:</b>\n"
        f"{new_text or '(пусто)'}\n\n"
        "Команды:\n"
        "• 'да' — сохранить изменения\n"
        "• 'отмена' — отменить"
    )


async def _edit_mode_confirm(user_text: str, user_id: int, edit_state: dict) -> str:
    """Подтверждение сохранения."""
    user_text_lower = user_text.lower()

    if user_text_lower in ("да", "yes", "save", "сохранить", "ok", "ок"):
        return await _save_prompt(user_id, edit_state)

    if user_text_lower in ("отмена", "cancel", "нет", "no"):
        set_prompt_edit_state(user_id, {**edit_state, "stage": "view"})
        return "❌ Сохранение отменено."

    if user_text_lower in ("список", "list", "ls", "назад", "back"):
        set_prompt_edit_state(user_id, {**edit_state, "stage": "view"})
        return "📝 Возврат к просмотру промпта. Введите команду."

    if user_text_lower in ("выход", "exit"):
        clear_prompt_edit_state(user_id)
        return "✅ Режим редактирования завершён."

    return "Напишите 'да' для сохранения или 'отмена' для отмены."


async def _save_prompt(user_id: int, edit_state: dict) -> str:
    """Сохраняет промпт в базу данных."""
    prompt_key = edit_state["prompt_key"]
    new_system = edit_state.get("new_system", edit_state["current_system"])
    new_human = edit_state.get("new_human", edit_state["current_human"])

    success, message = await PromptRepository.update_prompt(
        prompt_key=prompt_key,
        system_text=new_system,
        human_text=new_human,
        updated_by=user_id,
    )

    if success:
        clear_prompt_edit_state(user_id)

        if prompt_key.startswith("mentor_"):
            invalidate_prompt_cache()
        elif prompt_key.startswith("rag_"):
            invalidate_rag_prompt_cache()

        return f"✅ {message}\n\nПромпт обновлён и будет использован в следующих запросах."

    clear_prompt_edit_state(user_id)
    return f"❌ Ошибка сохранения: {message}"


async def _show_versions(prompt_key: str) -> str:
    """Показывает историю версий промпта."""
    versions = await PromptRepository.get_versions(prompt_key, limit=10)

    if not versions:
        return f"❌ История версий для '{prompt_key}' не найдена."

    lines = [f"📜 <b>История версий: {prompt_key}</b>\n"]

    for v in versions:
        created = v["created_at"].strftime("%d.%m.%Y %H:%M") if v["created_at"] else "?"
        lines.append(
            f"\n<b>Версия {v['version_number']}</b> — {created}\n"
            f"   System: {v['system_text'][:100]}{'...' if len(v['system_text']) > 100 else ''}"
        )

    lines.append("\n\nКоманды:\n• 'откат: N' — откатить к версии N\n• 'назад' — к списку команд")
    return "\n".join(lines)


async def _rollback_prompt(prompt_key: str, version_number: int, user_id: int) -> str:
    """Откатывает промпт к указанной версии."""
    success, message = await PromptRepository.rollback_version(
        prompt_key=prompt_key,
        version_number=version_number,
        updated_by=user_id,
    )

    if success:
        clear_prompt_edit_state(user_id)

        if prompt_key.startswith("mentor_"):
            invalidate_prompt_cache()
        elif prompt_key.startswith("rag_"):
            invalidate_rag_prompt_cache()

        return f"✅ {message}\n\nПромпт откачен и будет использоваться."

    return f"❌ Ошибка отката: {message}"
