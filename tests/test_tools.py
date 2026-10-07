import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.channel.base import IncomingMessage
from app.conversation.state_store import ConversationStore
from app.conversation.tools import execute_tool
from app.models.conversation import ConversationState, Quote
from tests.fakes import FakeChannel


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.mark.asyncio
async def test_cotizar_persists_quote(session: Session, catalog_fixture) -> None:
    store = ConversationStore(session)
    incoming = IncomingMessage(conversation_ref="c1", text="x", message_id="m1", from_phone="1")
    conv = store.get_or_create(incoming)
    channel = FakeChannel()
    result = await execute_tool(
        "cotizar_bateria",
        {"marca": "Toyota", "modelo": "Corolla", "anio": "2016"},
        store=store,
        conversation=conv,
        channel=channel,
    )
    assert result["status"] == "ok"
    quotes = session.exec(select(Quote)).all()
    assert len(quotes) == 1
    assert quotes[0].price == 142000


@pytest.mark.asyncio
async def test_coordinar_turno_handoff(session: Session) -> None:
    store = ConversationStore(session)
    conv = store.get_or_create(
        IncomingMessage(conversation_ref="c2", text="x", message_id="m2", from_phone="1")
    )
    channel = FakeChannel()
    result = await execute_tool(
        "coordinar_turno",
        {"lugar_colocacion": "domicilio"},
        store=store,
        conversation=conv,
        channel=channel,
    )
    assert result["status"] == "handed_off"
    assert channel.handoffs[0][0] == "c2"
    session.refresh(conv)
    assert conv.state == ConversationState.HANDED_OFF.value
    assert conv.vehicle_slots["lugar_colocacion"] == "domicilio"
