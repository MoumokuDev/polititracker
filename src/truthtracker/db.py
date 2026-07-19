from collections.abc import AsyncIterator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from truthtracker.config import get_settings

_engine = None
_async_engine = None
_session_factory: sessionmaker | None = None
_async_session_factory: async_sessionmaker | None = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    return _engine


def get_async_engine():
    global _async_engine
    if _async_engine is None:
        _async_engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    return _async_engine


def get_session() -> Session:
    """Sync session for CLI/ingestion scripts."""
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _session_factory()


async def get_async_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an async session."""
    global _async_session_factory
    if _async_session_factory is None:
        _async_session_factory = async_sessionmaker(
            bind=get_async_engine(), expire_on_commit=False
        )
    async with _async_session_factory() as session:
        yield session
