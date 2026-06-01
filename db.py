import json as json_module
from datetime import datetime
from utils import logger
from sqlalchemy import (
    Integer,
    String,
    BigInteger,
    DateTime,
    Boolean,
    func,
    Text,
    ForeignKey,
    UniqueConstraint,
    Index,
    delete,
)
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)
from global_state import MAX_DB_PATH, PROMPT_VERSIONS_LIMIT


# Инициализация асинхронной базы данных
class Base(DeclarativeBase):
    pass


engine = create_async_engine(MAX_DB_PATH, echo=False)
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(50), default="user")
    startdate: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    coindate: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    coins: Mapped[int] = mapped_column(Integer, default=0)
    giftcoins: Mapped[int] = mapped_column(Integer, default=0)
    # заметки, на будущее
    note: Mapped[str] = mapped_column(String(150), default="")
    # уровнь доступа пользователя 0 - администратор, 1 - гость, 2 - сотрудник
    permission: Mapped[int] = mapped_column(Integer, default=1)
    # логическое поле на будущее
    check: Mapped[bool] = mapped_column(Boolean, default=False)
    billings: Mapped[list["Billing"]] = relationship(back_populates="user")


class Billing(Base):
    __tablename__ = "billings"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), index=True
    )
    date: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    usermode: Mapped[str] = mapped_column(String(50))
    userprompt: Mapped[str] = mapped_column(String(255), default="")
    inccoins: Mapped[int] = mapped_column(Integer, default=0)
    deccoins: Mapped[int] = mapped_column(Integer, default=0)
    giftcoins: Mapped[int] = mapped_column(Integer, default=0)
    balance: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str] = mapped_column(String(150), default="")
    user: Mapped["User"] = relationship(back_populates="billings")


class Prompt(Base):
    """Таблица промптов для цепочек бота."""
    __tablename__ = "prompts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prompt_key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str] = mapped_column(String(500), default="")
    current_system_text: Mapped[str] = mapped_column(Text, default="")
    current_human_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    updated_by: Mapped[int] = mapped_column(BigInteger, default=0)
    versions: Mapped[list["PromptVersion"]] = relationship(
        back_populates="prompt", cascade="all, delete-orphan"
    )


class PromptVersion(Base):
    """История версий промптов."""
    __tablename__ = "prompt_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prompt_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("prompts.id", ondelete="CASCADE"), index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, default=1)
    system_text: Mapped[str] = mapped_column(Text, default="")
    human_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    created_by: Mapped[int] = mapped_column(BigInteger, default=0)
    prompt: Mapped["Prompt"] = relationship(back_populates="versions")


class UserContext(Base):
    """Таблица для хранения контекстов диалогов пользователей."""
    __tablename__ = "user_contexts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(50), nullable=False)
    context_data: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    message_count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint('user_id', 'mode', name='uq_user_mode'),
        Index('idx_user_contexts_user_mode', 'user_id', 'mode'),
        Index('idx_user_contexts_updated', 'updated_at'),
    )


async def create_database():
    """Создает базу данных и таблицы, а также системного пользователя."""
    try:
        # Создаем базу и таблицы если их не существует
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Создание системного пользователя с id=0, если он не существует
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select

            result = await db.execute(select(User).where(User.id == 0))
            user = result.scalar_one_or_none()

            if not user:
                new_user = User(id=0, name="System")
                db.add(new_user)
                await db.commit()
                await db.refresh(new_user)

    except Exception as e:
        logger.error(f"Ошибка при создании базы: {e}")
        raise


async def check_user(userid: int) -> bool:
    """
    Проверяет, существует ли пользователь с заданным userid в таблице users.
    Возвращает True, если пользователь найден, иначе False.
    """
    try:
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select

            result = await db.execute(select(User).where(User.id == userid))
            user = result.scalar_one_or_none()
            return user is not None
    except Exception as e:
        logger.error(f"Ошибка при проверке пользователя: {e}")
        return False


async def create_user(
    userid: int,
    nickname: str,
    coins: int = 0,
    giftcoins: int = 10,
    note: str = None,
    permission: int = 1,
    check: bool = False,
) -> bool:
    """
    Создаёт пользователя в таблице users.
    В поля startdate и coindate заносится текущее время.
    Возвращает True при успехе, False — при ошибке.
    """
    try:
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select

            # Проверяем, не существует ли уже пользователь
            result = await db.execute(select(User).where(User.id == userid))
            existing_user = result.scalar_one_or_none()
            if existing_user:
                logger.info(f"Пользователь с userid={userid} уже есть в базе.")
                return False

            now = datetime.now()
            new_user = User(
                id=userid,
                name=nickname,
                startdate=now,
                coindate=now,
                coins=coins,
                giftcoins=giftcoins,
                note=note or "",
                permission=permission,
            )
            db.add(new_user)
            await db.commit()
            return True

    except Exception as e:
        logger.error(f"Ошибка при создании пользователя: {e}")
        return False


async def get_user(userid: int) -> dict | None:
    """
    Извлекает данные пользователя из таблицы users по userid.
    Возвращает словарь с данными или None, если пользователь не найден.
    """
    try:
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select

            result = await db.execute(select(User).where(User.id == userid))
            user = result.scalar_one_or_none()

            if user:
                return {
                    "id": user.id,
                    "userid": user.id,
                    "nickname": user.name,
                    "startdate": user.startdate,
                    "coindate": user.coindate,
                    "coins": user.coins,
                    "giftcoins": user.giftcoins,
                    "note": user.note,
                    "permission": user.permission,
                    "check": user.check,
                }
            return None

    except Exception as e:
        logger.error(f"Ошибка при получении данных пользователя: {e}")
        return None


async def add_coins(userid: int, coins: int = 0, giftcoins: int = 0) -> bool:
    """
    Обновляет количество coins и giftcoins,
    и устанавливает coindate в текущее время
    для пользователя с заданным userid.
    Возвращает True при успехе, False — при ошибке.
    """
    try:
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select

            result = await db.execute(select(User).where(User.id == userid))
            user = result.scalar_one_or_none()

            if not user:
                return False

            user.coins += coins
            user.giftcoins += giftcoins
            user.coindate = datetime.now()

            await db.commit()
            return True

    except Exception as e:
        logger.error(f"Ошибка при обновлении данных: {e}")
        return False


async def add_billing(
    userid: int,
    usermode: str,
    userprompt: str = "",
    inccoins: int = 0,
    deccoins: int = 0,
    giftcoins: int = 0,
    notes: str = "",
) -> bool:
    """
    Создаёт запись в billings и обновляет баланс пользователя.
    Возвращает True при успехе, False — при ошибке.
    """
    try:
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select

            result = await db.execute(select(User).where(User.id == userid))
            user = result.scalar_one_or_none()

            if not user:
                return False

            balance = (
                user.coins + user.giftcoins + inccoins + giftcoins - deccoins
            )

            new_billing = Billing(
                user_id=userid,
                usermode=usermode,
                userprompt=userprompt,
                inccoins=inccoins,
                deccoins=deccoins,
                giftcoins=giftcoins,
                balance=balance,
                notes=notes,
            )
            db.add(new_billing)

            user.coins += inccoins
            user.giftcoins += giftcoins
            user.giftcoins -= deccoins

            await db.commit()
            return True

    except Exception as e:
        logger.error(f"Ошибка при создании billing: {e}")
        return False


async def get_billing_history(
    userid: int,
    limit: int = 50,
) -> list[dict] | None:
    """
    Возвращает историю billings для пользователя.
    """
    try:
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select, desc

            result = await db.execute(
                select(Billing)
                .where(Billing.user_id == userid)
                .order_by(desc(Billing.date))
                .limit(limit)
            )
            billings = result.scalars().all()

            return [
                {
                    "id": b.id,
                    "date": b.date,
                    "usermode": b.usermode,
                    "userprompt": b.userprompt,
                    "inccoins": b.inccoins,
                    "deccoins": b.deccoins,
                    "giftcoins": b.giftcoins,
                    "balance": b.balance,
                    "notes": b.notes,
                }
                for b in billings
            ]

    except Exception as e:
        logger.error(f"Ошибка при получении истории billings: {e}")
        return None


# ─── Промпты для цепочек ───
DEFAULT_PROMPTS = {
    "mentor_question": {
        "description": "Промпт для генерации вопросов в режиме ментора",
        "system": """Ты — строгий преподаватель колледжа, который проверяет знания студента.

Контекст из документов колледжа:
{context}

Задание:
1. На основе КОНТЕКСТА сформулируй ОДИН проверочный вопрос.
2. Вопрос должен проверять понимание ключевого материала.
3. Вопрос должен иметь КОНКРЕТНЫЙ ответ (факт, определение, число, название).
4. Не задавай вопросы типа "объясните", "опишите" — только фактические вопросы.
5. НЕ добавляй префикс "Вопрос:" — просто напиши сам вопрос.

Формат ответа: ТОЛЬКО сам вопрос, без лишних слов.""",
        "human": "Сформулируй проверочный вопрос по теме: {topic}",
    },
    "mentor_evaluation": {
        "description": "Промпт для оценки ответов студента в режиме ментора",
        "system": """Ты — строгий преподаватель, который проверяет знания студента.

Материал из документов (эталон):
{context}

Вопрос, на который отвечал студент:
{question}

Ответ студента:
{answer}

ВНИМАНИЕ: Будь КРАЙНЕ строг при оценке. Оценивай буквально.

"ПРАВИЛЬНО" — только если:
- Ответ ТОЧНО совпадает с эталоном
- Числа, названия, буквенные коды идентичны эталону
- Нет ни одной ошибки

"ЧАСТИЧНО" — если:
- Ответ содержит верную идею, но неполон
- Упущены важные детали эталонного ответа

"НЕПРАВИЛЬНО" — если:
- Названия отличаются хотя бы одним символом/буквой
- Числа не совпадают
- Упомянуты неверные данные (не те авторы, не тот год, не тот формат)
- Ответ не раскрывает суть вопроса

Формат (ТОЛЬКО эти две строки):
ОЦЕНКА: ПРАВИЛЬНО или ЧАСТИЧНО или НЕПРАВИЛЬНО
ОБРАТНАЯ СВЯЗЬ: Одно предложение
ПРАВИЛЬНЫЙ ОТВЕТ: эталонный ответ из контекста (если есть)""",
        "human": "",
    },
    "rag_default": {
        "description": "Основной RAG промпт для ответов на вопросы по документам",
        "system": """Ты — помощник студентов Саранского строительного техникума.
Отвечай только про техникумы, колледжи, cреднее профессиональное образование.
Не включай в ответ информацию про высшее образование и про ВУЗы
Отвечай ТОЛЬКО на основе предоставленных фрагментов документов.
Внимательно изучи ВСЕ предоставленные фрагменты и объедини информацию.
Если в документах несколько фактов — приведи их все.
Если информация противоречит — укажи это.
Отвечай кратко: 5-12 предложений.
Не придумывай факты.
ВАЖНО: в конце ответа ОБЯЗАТЕЛЬНО укажи источники.
Формат: «Название документа», <Статья/Раздел>
Пример: «ФЗ от 5 апреля 2013 г N 44 ФЗ О контрактной системе», Статья 32
Копируй название документа ТОЧНО как указано в поле 'Документ:'.

Фрагменты документов:
{context}""",
        "human": "{question}",
    },
}


async def init_default_prompts():
    """
    Инициализирует промпты по умолчанию в базе данных.
    Создаёт записи если их нет, иначе пропускает.
    """
    try:
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select

            for prompt_key, data in DEFAULT_PROMPTS.items():
                result = await db.execute(
                    select(Prompt).where(Prompt.prompt_key == prompt_key)
                )
                existing = result.scalar_one_or_none()

                if not existing:
                    prompt = Prompt(
                        prompt_key=prompt_key,
                        description=data["description"],
                        current_system_text=data["system"],
                        current_human_text=data["human"],
                    )
                    db.add(prompt)

                    await db.flush()

                    version = PromptVersion(
                        prompt_id=prompt.id,
                        version_number=1,
                        system_text=data["system"],
                        human_text=data["human"],
                        created_by=0,
                    )
                    db.add(version)

                    logger.info(f"Инициализирован промпт: {prompt_key}")

            await db.commit()
    except Exception as e:
        logger.error(f"Ошибка при инициализации промптов: {e}")


# ==================== User Context Persistence ====================

async def save_user_context(user_id: int, mode: str, context: list[dict]) -> bool:
    """
    Сохраняет контекст пользователя в БД.
    Обновляет запись, если она существует, иначе создаёт новую.
    """
    try:
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select

            result = await db.execute(
                select(UserContext).where(
                    UserContext.user_id == user_id,
                    UserContext.mode == mode
                )
            )
            context_record = result.scalar_one_or_none()

            context_json = json_module.dumps(context, ensure_ascii=False)
            message_count = len([m for m in context if m.get("role") in ("user", "assistant")])

            if context_record:
                context_record.context_data = context_json
                context_record.updated_at = datetime.now()
                context_record.message_count = message_count
            else:
                new_context = UserContext(
                    user_id=user_id,
                    mode=mode,
                    context_data=context_json,
                    message_count=message_count
                )
                db.add(new_context)

            await db.commit()
            return True
    except Exception as e:
        logger.error(f"Ошибка сохранения контекста в БД: {e}")
        return False


async def load_user_context(user_id: int, mode: str) -> list[dict] | None:
    """
    Загружает контекст пользователя из БД.
    Возвращает список сообщений или None, если контекст не найден.
    """
    try:
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select

            result = await db.execute(
                select(UserContext).where(
                    UserContext.user_id == user_id,
                    UserContext.mode == mode
                )
            )
            context_record = result.scalar_one_or_none()

            if context_record:
                return json_module.loads(context_record.context_data)
            return None
    except Exception as e:
        logger.error(f"Ошибка загрузки контекста из БД: {e}")
        return None


async def delete_user_context(user_id: int, mode: str) -> bool:
    """
    Удаляет контекст пользователя из БД для указанного режима.
    """
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(
                delete(UserContext).where(
                    UserContext.user_id == user_id,
                    UserContext.mode == mode
                )
            )
            await db.commit()
            return True
    except Exception as e:
        logger.error(f"Ошибка удаления контекста из БД: {e}")
        return False
