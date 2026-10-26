from sqlalchemy.engine.url import make_url

from app.db.session import normalize_database_url


def test_normalize_database_url_accepts_railway_and_keeps_sqlite() -> None:
    cases = {
        "postgres://user:secret@host:5432/tecnobot": "postgresql+psycopg://user:secret@host:5432/tecnobot",
        "postgresql://user:secret@host:5432/tecnobot?sslmode=require": (
            "postgresql+psycopg://user:secret@host:5432/tecnobot?sslmode=require"
        ),
        "postgresql+psycopg://user:secret@host:5432/tecnobot": "postgresql+psycopg://user:secret@host:5432/tecnobot",
        "sqlite:///./tecnobot.db": "sqlite:///./tecnobot.db",
    }
    for raw, expected in cases.items():
        normalized = normalize_database_url(raw)
        assert normalized == expected
        assert make_url(normalized).drivername == make_url(expected).drivername
