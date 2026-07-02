"""Lazy SQLAlchemy engine/session factories.

IMPORTANT: importing this module must NOT connect to the database. The
engine is created on first get_engine() call only (and SQLAlchemy itself
connects lazily on first use). Tests import app code without any DB running.
"""

import logging
from functools import lru_cache

from sqlalchemy import Engine, create_engine
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
