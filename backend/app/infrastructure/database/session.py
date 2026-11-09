"""Database engine and session factory."""

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.infrastructure.database.models import Base

_BACKEND_DIR = Path(__file__).resolve().parents[3]


def database_url(raw_url: str | None = None) -> str:
    """Resolve the SQLAlchemy URL; local development falls back to ``backend/recoverx.db``."""
    value = raw_url if raw_url is not None else settings.DATABASE_URL
    if not value:
        return f"sqlite:///{(_BACKEND_DIR / 'recoverx.db').as_posix()}"
    # Hosting providers hand out postgres:// URLs; SQLAlchemy needs the psycopg driver name.
    for prefix in ("postgres://", "postgresql://"):
        if value.startswith(prefix):
            return "postgresql+psycopg://" + value[len(prefix) :]
    return value


_url = database_url()
engine = create_engine(
    _url,
    connect_args={"check_same_thread": False} if _url.startswith("sqlite") else {},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    """Create missing tables directly. Development and tests only; production uses Alembic."""
    Base.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
