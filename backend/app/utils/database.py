"""Connexion à la base (PostGIS en production, SQLite pour les tests et le développement local)."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine(url: str):
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 60})

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):  # pragma: no cover - trivial
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

        return engine
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


engine = _make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """Crée les tables manquantes (idempotent ; en PostGIS, init_db.sql a déjà tout créé)."""
    from app import models  # noqa: F401  (enregistre les modèles)

    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS postgis")
    Base.metadata.create_all(engine)


def get_db() -> Iterator[Session]:
    """Dépendance FastAPI."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
