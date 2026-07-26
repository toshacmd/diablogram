import datetime as dt
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


def utc_now() -> dt.datetime:
    """Timezone-aware UTC now — column default for every created_at/updated_at.
    (datetime.utcnow is naive and deprecated; mixing naive and aware values
    breaks comparisons on SQLite in dev.)"""
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


engine = create_async_engine(get_settings().database_url, echo=False, pool_pre_ping=True)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session
