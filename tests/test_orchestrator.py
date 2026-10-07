import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.channel.base import IncomingMessage
from app.config import settings
from app.conversation.orchestrator import handle_incoming
from app.conversation.state_store import ConversationStore
from app.models.conversation import Conversation, ConversationState, Message
from tests.fakes import FakeChannel


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture(autouse=True)
def force_echo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "echo_mode", True)


def _incoming(text: str = "hola", msg_id: str = "wamid.1") -> IncomingMessage:
    return IncomingMessage(
        conversation_ref="conv_abc",
        text=text,
        message_id=msg_id,
        from_phone="54911",
    )


@pytest.mark.asyncio
async def test_echo_persists_both_messages(session: Session) -> None:
    channel = FakeChannel()
    await handle_incoming(channel, _incoming(), session)
    msgs = session.exec(select(Message)).all()
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[1].content == "Recibí: hola"
    assert channel.sent == [("conv_abc", "Recibí: hola", "54911")]


@pytest.mark.asyncio
async def test_handed_off_does_not_reply(session: Session) -> None:
    channel = FakeChannel()
    await handle_incoming(channel, _incoming("uno", "wamid.a"), session)
    conv = session.exec(select(Conversation)).one()
    ConversationStore(session).set_state(conv.id, ConversationState.HANDED_OFF)
    await handle_incoming(channel, _incoming("dos", "wamid.b"), session)
    assert len(channel.sent) == 1
    roles = [m.role for m in session.exec(select(Message)).all()]
    assert roles == ["user", "assistant", "user"]


@pytest.mark.asyncio
async def test_duplicate_external_id_skipped(session: Session) -> None:
    channel = FakeChannel()
    incoming = _incoming()
    await handle_incoming(channel, incoming, session)
    await handle_incoming(channel, incoming, session)
    assert len(channel.sent) == 1
    assert session.exec(select(Message)).all().__len__() == 2
