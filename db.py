from datetime import datetime
from dotenv import load_dotenv
from sqlalchemy import (
    Integer,
    String,
    BigInteger,
    DateTime,
    Boolean,
    func,
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
)

# Загрузить переменные из файла .env
load_dotenv()


# Инициализация асинхронной базы данных
class Base(DeclarativeBase):
    pass


DATABASE_URL = "sqlite+aiosqlite:///maxbot.db"
engine = create_async_engine(DATABASE_URL, echo=False)
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
        DateTime,
        server_default=func.now()
    )
    coindate: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now()
    )
    coins: Mapped[int] = mapped_column(Integer, default=0)
    giftcoins: Mapped[int] = mapped_column(Integer, default=0)
    # заметки, на будущее
    note: Mapped[str] = mapped_column(String(150), default="")
    # уровнь доступа пользователя 0 - администратор, 1 - гость, 2 - сотрудник
    permission: Mapped[int] = mapped_column(Integer, default=1)
    # логическое поле на будущее
    check: Mapped[bool] = mapped_column(Boolean, default=False)


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
        print(f"Ошибка при создании базы или системного пользователя: {e}")


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
        print(f"Ошибка при проверке пользователя: {e}")
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
                print(f"Пользователь с userid={userid} уже есть в базе.")
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
        print(f"Ошибка при создании пользователя: {e}")
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
                }
            return None

    except Exception as e:
        print(f"Ошибка при получении данных пользователя: {e}")
        return None


async def add_coins(
        userid: int,
        coins: int = 0,
        giftcoins: int = 0
) -> bool:
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
        print(f"Ошибка при обновлении данных: {e}")
        return False
