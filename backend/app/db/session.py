"""Lazy SQLAlchemy engine/session factories (sync + async).

IMPORTANT: importing this module must NOT connect to the database. Engines are
created on first get_*_engine() call only (and SQLAlchemy itself connects lazily
on first use). Tests import app code without any DB running.

Both a sync and an async stack are provided over the *same* ``DATABASE_URL``:
the ``postgresql+psycopg://`` URL drives psycopg3, whose dialect is dual-mode --
``create_engine`` uses it synchronously and ``create_async_engine`` uses its
async path (which additionally requires the ``greenlet`` runtime). The async
stack is what the ingest writer (app/ingest/persist.py) uses, to match the
project's httpx.AsyncClient I/O pattern; the sync stack remains for Phase 2
FastAPI request handlers.
"""

import logging
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache
def get_engine() -> Engine:
    """Create (once) and return the SQLAlchemy engine.

    create_engine() itself does not open a connection; connections are
    established lazily on first execute.
    """
    settings = get_settings()
    logger.info("creating SQLAlchemy engine (lazy connect)")
    return create_engine(settings.database_url, pool_pre_ping=True)


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    """Return a cached sessionmaker bound to the lazy engine."""
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_session() -> Session:
    """Create a new Session. Caller is responsible for closing it.

    Intended for FastAPI dependencies in Phase 2, e.g.:

        def db_session():
            session = get_session()
            try:
                yield session
            finally:
                session.close()
    """
    return get_session_factory()()


@lru_cache
def get_async_engine() -> AsyncEngine:
    """Create (once) and return the async SQLAlchemy engine.

    Uses the same ``postgresql+psycopg://`` URL as the sync engine -- psycopg3's
    dialect selects its async path here. No connection is opened until first use.
    Requires the ``greenlet`` runtime (declared in requirements.txt).
    """
    settings = get_settings()
    logger.info("creating async SQLAlchemy engine (lazy connect)")
    return create_async_engine(settings.database_url, pool_pre_ping=True)


@lru_cache
def get_async_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return a cached async_sessionmaker bound to the lazy async engine.

    ``expire_on_commit=False`` so values read before a commit stay usable after
    it without an implicit (and, on an AsyncSession, illegal) lazy reload.
    """
    return async_sessionmaker(bind=get_async_engine(), expire_on_commit=False)


def get_async_session() -> AsyncSession:
    """Create a new AsyncSession. Caller is responsible for closing it.

    Intended to be used as an async context manager, e.g.:

        async with get_async_session() as session:
            await ingest_filing(session, ...)
    """
    return get_async_session_factory()()
