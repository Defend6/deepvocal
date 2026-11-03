from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

_engine = None
_sessions = None


def _session_factory() -> async_sessionmaker[AsyncSession]:
    global _engine, _sessions
    if _sessions is None:
        _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
        _sessions = async_sessionmaker(_engine, expire_on_commit=False)
    return _sessions


async def get_session() -> AsyncIterator[AsyncSession]:
    async with _session_factory()() as session:
        yield session
