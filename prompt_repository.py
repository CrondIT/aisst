"""
Репозиторий для работы с промптами в базе данных.
Обеспечивает CRUD операции и управление версиями промптов.
"""

from typing import Optional
from datetime import datetime

from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from db import AsyncSessionLocal, Prompt, PromptVersion
from global_state import PROMPT_VERSIONS_LIMIT, PROMPT_VERSIONS_KEEP
from utils import logger


class PromptRepository:
    """Репозиторий для работы с промптами."""

    @staticmethod
    async def get_prompt(prompt_key: str) -> Optional[Prompt]:
        """
        Получает промпт по ключу.

        Args:
            prompt_key: Уникальный ключ промпта

        Returns:
            Prompt объект или None
        """
        try:
            async with AsyncSessionLocal() as db:
                from sqlalchemy import select

                result = await db.execute(
                    select(Prompt).where(Prompt.prompt_key == prompt_key)
                )
                return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Ошибка при получении промпта {prompt_key}: {e}")
            return None

    @staticmethod
    async def list_prompts() -> list[dict]:
        """
        Возвращает список всех промптов с их текущими версиями.

        Returns:
            Список словарей с данными промптов
        """
        try:
            async with AsyncSessionLocal() as db:
                from sqlalchemy import select

                result = await db.execute(select(Prompt).order_by(Prompt.prompt_key))
                prompts = result.scalars().all()

                prompts_data = []
                for p in prompts:
                    version_count = await db.execute(
                        select(func.count(PromptVersion.id))
                        .where(PromptVersion.prompt_id == p.id)
                    )
                    count = version_count.scalar() or 0

                    prompts_data.append({
                        "id": p.id,
                        "prompt_key": p.prompt_key,
                        "description": p.description,
                        "system_text": p.current_system_text[:200] + "..." if len(p.current_system_text) > 200 else p.current_system_text,
                        "human_text": p.current_human_text[:100] + "..." if len(p.current_human_text) > 100 else p.current_human_text,
                        "updated_at": p.updated_at,
                        "updated_by": p.updated_by,
                        "version_count": count,
                    })
                return prompts_data
        except Exception as e:
            logger.error(f"Ошибка при получении списка промптов: {e}")
            return []

    @staticmethod
    async def update_prompt(
        prompt_key: str,
        system_text: str,
        human_text: str,
        updated_by: int = 0,
    ) -> tuple[bool, str]:
        """
        Обновляет промпт и создаёт новую версию.

        Args:
            prompt_key: Ключ промпта
            system_text: Новый system prompt
            human_text: Новый human prompt
            updated_by: ID пользователя, внёсшего изменения

        Returns:
            (успех, сообщение)
        """
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Prompt).where(Prompt.prompt_key == prompt_key)
                )
                prompt = result.scalar_one_or_none()

                if not prompt:
                    return False, f"Промпт '{prompt_key}' не найден"

                next_version_result = await db.execute(
                    select(func.count(PromptVersion.id))
                    .where(PromptVersion.prompt_id == prompt.id)
                )
                next_version = (next_version_result.scalar() or 0) + 1

                version = PromptVersion(
                    prompt_id=prompt.id,
                    version_number=next_version,
                    system_text=system_text,
                    human_text=human_text,
                    created_by=updated_by,
                )
                db.add(version)

                prompt.current_system_text = system_text
                prompt.current_human_text = human_text
                prompt.updated_at = datetime.now()
                prompt.updated_by = updated_by

                await db.flush()

                await PromptRepository._cleanup_old_versions(db, prompt.id, PROMPT_VERSIONS_KEEP)

                await db.commit()

                logger.info(
                    f"Промпт '{prompt_key}' обновлён до версии {next_version} пользователем {updated_by}"
                )
                return True, f"Промпт обновлён (версия {next_version})"

        except Exception as e:
            logger.error(f"Ошибка при обновлении промпта {prompt_key}: {e}")
            return False, f"Ошибка: {e}"

    @staticmethod
    async def _cleanup_old_versions(
        db: AsyncSession, prompt_id: int, keep_count: int
    ):
        """
        Удаляет старые версии, оставляя только последние keep_count.
        Для отката оставляем на 5 версий больше чем лимит.
        """
        try:
            buffer_count = keep_count + 5  # Запас для откатов

            result = await db.execute(
                select(PromptVersion)
                .where(PromptVersion.prompt_id == prompt_id)
                .order_by(PromptVersion.version_number.desc())
            )
            versions = result.scalars().all()

            if len(versions) > buffer_count:
                to_delete = versions[buffer_count:]
                for v in to_delete:
                    await db.delete(v)
                logger.info(
                    f"Удалено {len(to_delete)} старых версий промпта {prompt_id}"
                )
        except Exception as e:
            logger.error(f"Ошибка при очистке версий: {e}")

    @staticmethod
    async def get_versions(prompt_key: str, limit: int = 10) -> list[dict]:
        """
        Возвращает историю версий промпта.

        Args:
            prompt_key: Ключ промпта
            limit: Максимальное количество версий

        Returns:
            Список версий
        """
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Prompt)
                    .where(Prompt.prompt_key == prompt_key)
                )
                prompt = result.scalar_one_or_none()

                if not prompt:
                    return []

                result = await db.execute(
                    select(PromptVersion)
                    .where(PromptVersion.prompt_id == prompt.id)
                    .order_by(PromptVersion.version_number.desc())
                    .limit(limit)
                )
                versions = result.scalars().all()

                return [
                    {
                        "version_number": v.version_number,
                        "system_text": v.system_text[:300] + "..." if len(v.system_text) > 300 else v.system_text,
                        "human_text": v.human_text,
                        "created_at": v.created_at,
                        "created_by": v.created_by,
                    }
                    for v in versions
                ]
        except Exception as e:
            logger.error(f"Ошибка при получении версий {prompt_key}: {e}")
            return []

    @staticmethod
    async def rollback_version(
        prompt_key: str,
        version_number: int,
        updated_by: int = 0,
    ) -> tuple[bool, str]:
        """
        Откатывает промпт к указанной версии.

        Args:
            prompt_key: Ключ промпта
            version_number: Номер версии для отката
            updated_by: ID пользователя

        Returns:
            (успех, сообщение)
        """
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Prompt)
                    .where(Prompt.prompt_key == prompt_key)
                )
                prompt = result.scalar_one_or_none()

                if not prompt:
                    return False, f"Промпт '{prompt_key}' не найден"

                result = await db.execute(
                    select(PromptVersion)
                    .where(
                        PromptVersion.prompt_id == prompt.id,
                        PromptVersion.version_number == version_number,
                    )
                )
                version = result.scalar_one_or_none()

                if not version:
                    return False, f"Версия {version_number} не найдена"

                return await PromptRepository.update_prompt(
                    prompt_key=prompt_key,
                    system_text=version.system_text,
                    human_text=version.human_text,
                    updated_by=updated_by,
                )

        except Exception as e:
            logger.error(f"Ошибка при откате версии {prompt_key}:{version_number}: {e}")
            return False, f"Ошибка: {e}"

    @staticmethod
    async def get_prompt_template(prompt_key: str) -> Optional[object]:
        """
        Получает ChatPromptTemplate для указанного промпта.

        Args:
            prompt_key: Ключ промпта

        Returns:
            ChatPromptTemplate или None
        """
        try:
            from langchain_core.prompts import ChatPromptTemplate

            prompt = await PromptRepository.get_prompt(prompt_key)
            if not prompt:
                logger.warning(f"Промпт '{prompt_key}' не найден, используется значение по умолчанию")
                return None

            messages = []
            if prompt.current_system_text:
                messages.append(("system", prompt.current_system_text))
            if prompt.current_human_text:
                messages.append(("human", prompt.current_human_text))

            if not messages:
                return None

            return ChatPromptTemplate.from_messages(messages)

        except Exception as e:
            logger.error(f"Ошибка при создании ChatPromptTemplate для {prompt_key}: {e}")
            return None