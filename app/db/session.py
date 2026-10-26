from collections.abc import Generator

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

_engine: Engine | None = None


def normalize_database_url(url: str) -> str:
    """Supabase entrega postgres:// o postgresql://. psycopg 3 usa postgresql+psycopg://."""
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    return url


def make_engine(database_url: str | None = None) -> Engine:
    url = normalize_database_url(database_url or settings.database_url)
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = make_engine()
    return _engine


def init_db(engine: Engine | None = None) -> None:
    from app.models import conversation as _models  # noqa: F401
    from app.models import errors as _errors  # noqa: F401

    SQLModel.metadata.create_all(engine or get_engine())


def get_session() -> Generator[Session, None, None]:
    with Session(get_engine()) as session:
        yield session
