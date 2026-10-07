import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.channel.base import IncomingMessage
from app.config import settings
from app.conversation.orchestrator import handle_incoming
from app.db.error_log import record_internal_error
from app.models.errors import InternalError
from tests.fakes import FakeChannel


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


class InsufficientBalance(Exception):
    status_code = 402
    body = {"error": {"message": "Insufficient Balance"}}


def test_record_persists_http_status(session: Session) -> None:
    try:
        raise InsufficientBalance("Error code: 402 - Insufficient Balance")
    except InsufficientBalance as exc:
        row = record_internal_error(session, source="llm", exc=exc, channel_ref="conv_1")
    assert row is not None
    assert row.http_status == 402
    assert row.source == "llm"
    assert "Insufficient" in row.message
    assert session.exec(select(InternalError)).one().id == row.id


@pytest.mark.asyncio
async def test_llm_402_is_logged_and_user_gets_fallback(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "echo_mode", False)
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")

    async def boom(*_args, **_kwargs):
        raise InsufficientBalance("Error code: 402 - Insufficient Balance")

    monkeypatch.setattr("app.conversation.orchestrator.run_turn", boom)
    channel = FakeChannel()
    await handle_incoming(
        channel,
        IncomingMessage(conversation_ref="c", text="hola", message_id="m1", from_phone="1"),
        session,
    )
    row = session.exec(select(InternalError)).one()
    assert row.source == "llm"
    assert row.http_status == 402
    assert channel.sent[0][1].startswith("Disculpá")
