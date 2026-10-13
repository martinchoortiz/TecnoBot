import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.channel.base import IncomingMessage
from app.config import settings
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
    assert quotes[0].price == 184600


@pytest.mark.asyncio
async def test_coordinar_turno_handoff(session: Session) -> None:
    store = ConversationStore(session)
    conv = store.get_or_create(
        IncomingMessage(conversation_ref="c2", text="x", message_id="m2", from_phone="1")
    )
    channel = FakeChannel()
    result = await execute_tool(
        "coordinar_turno",
        {
            "lugar_colocacion": "domicilio",
            "direccion": "Av. Libertador 1234",
            "departamento": "Rivadavia",
        },
        store=store,
        conversation=conv,
        channel=channel,
    )
    assert result["status"] == "handed_off"
    assert channel.handoffs[0][0] == "c2"
    assert "Libertador 1234" in channel.handoffs[0][1]
    session.refresh(conv)
    assert conv.state == ConversationState.HANDED_OFF.value
    assert conv.vehicle_slots["lugar_colocacion"] == "domicilio"
    assert conv.vehicle_slots["departamento"] == "Rivadavia"


@pytest.mark.asyncio
async def test_coordinar_turno_domicilio_pide_direccion(session: Session) -> None:
    store = ConversationStore(session)
    conv = store.get_or_create(
        IncomingMessage(conversation_ref="c4", text="x", message_id="m4", from_phone="1")
    )
    channel = FakeChannel()
    result = await execute_tool(
        "coordinar_turno",
        {"lugar_colocacion": "domicilio"},
        store=store,
        conversation=conv,
        channel=channel,
    )
    assert result["status"] == "need_slots"
    assert set(result["missing"]) == {"direccion", "departamento"}
    assert channel.handoffs == []
    session.refresh(conv)
    assert conv.state != ConversationState.HANDED_OFF.value


@pytest.mark.asyncio
async def test_coordinar_turno_domicilio_fuera_de_zona(session: Session) -> None:
    store = ConversationStore(session)
    conv = store.get_or_create(
        IncomingMessage(conversation_ref="c5", text="x", message_id="m5", from_phone="1")
    )
    channel = FakeChannel()
    result = await execute_tool(
        "coordinar_turno",
        {"lugar_colocacion": "domicilio", "direccion": "Ruta 40 km 12", "departamento": "Pocito"},
        store=store,
        conversation=conv,
        channel=channel,
    )
    assert result["status"] == "fuera_de_zona"
    assert result["localidad"] == "Pocito"
    assert result["alternativa"] == "taller"
    assert channel.handoffs == []
    session.refresh(conv)
    assert conv.state != ConversationState.HANDED_OFF.value


@pytest.mark.asyncio
async def test_coordinar_turno_taller_no_pide_direccion_ni_zona(session: Session) -> None:
    store = ConversationStore(session)
    conv = store.get_or_create(
        IncomingMessage(conversation_ref="c6", text="x", message_id="m6", from_phone="1")
    )
    channel = FakeChannel()
    # Sin dirección: pasa directo.
    result = await execute_tool(
        "coordinar_turno",
        {"lugar_colocacion": "taller"},
        store=store,
        conversation=conv,
        channel=channel,
    )
    assert result["status"] == "handed_off"
    assert result["lugar_colocacion"] == "taller"
    assert channel.handoffs[0][1] == "turno:taller"
    session.refresh(conv)
    assert conv.state == ConversationState.HANDED_OFF.value
    assert "direccion" not in conv.vehicle_slots


@pytest.mark.asyncio
async def test_coordinar_turno_taller_ignora_departamento_fuera_de_zona(session: Session) -> None:
    store = ConversationStore(session)
    conv = store.get_or_create(
        IncomingMessage(conversation_ref="c7", text="x", message_id="m7", from_phone="1")
    )
    channel = FakeChannel()
    result = await execute_tool(
        "coordinar_turno",
        {"lugar_colocacion": "taller", "direccion": "Ruta 40 km 12", "departamento": "Pocito"},
        store=store,
        conversation=conv,
        channel=channel,
    )
    assert result["status"] == "handed_off"
    session.refresh(conv)
    assert "departamento" not in conv.vehicle_slots


@pytest.mark.asyncio
async def test_coordinar_turno_mecanica_siempre_taller(session: Session) -> None:
    store = ConversationStore(session)
    conv = store.get_or_create(
        IncomingMessage(conversation_ref="c3", text="x", message_id="m3", from_phone="1")
    )
    store.save_quote(conv.id, "atencion_mecanica", {"marca": "Ford"}, 100000.0, "estimación")
    channel = FakeChannel()
    result = await execute_tool(
        "coordinar_turno",
        {"lugar_colocacion": "domicilio"},
        store=store,
        conversation=conv,
        channel=channel,
    )
    assert result["lugar_colocacion"] == "taller"
    session.refresh(conv)
    assert conv.vehicle_slots["lugar_colocacion"] == "taller"
    assert "turno:taller" in channel.handoffs[0][1]


@pytest.mark.asyncio
async def test_revision_bateria_domicilio_aun_con_cotizacion_mecanica(session: Session) -> None:
    store = ConversationStore(session)
    conv = store.get_or_create(
        IncomingMessage(conversation_ref="c8", text="x", message_id="m8", from_phone="1")
    )
    store.save_quote(conv.id, "atencion_mecanica", {"marca": "Ford"}, 100000.0, "estimación")
    channel = FakeChannel()
    result = await execute_tool(
        "coordinar_turno",
        {
            "tipo_turno": "revision_bateria",
            "lugar_colocacion": "domicilio",
            "direccion": "Av. Libertador 1234",
            "departamento": "Rawson",
            "nota": "Ford Focus 2015, no arranca",
        },
        store=store,
        conversation=conv,
        channel=channel,
    )
    assert result["status"] == "handed_off"
    assert result["lugar_colocacion"] == "domicilio"
    note = channel.handoffs[0][1]
    assert "revisión de batería" in note
    assert "Libertador 1234 (Rawson)" in note
    session.refresh(conv)
    assert conv.vehicle_slots["departamento"] == "Rawson"


@pytest.mark.asyncio
async def test_revision_bateria_domicilio_fuera_de_zona(session: Session) -> None:
    store = ConversationStore(session)
    conv = store.get_or_create(
        IncomingMessage(conversation_ref="c9", text="x", message_id="m9", from_phone="1")
    )
    channel = FakeChannel()
    result = await execute_tool(
        "coordinar_turno",
        {
            "tipo_turno": "revision_bateria",
            "lugar_colocacion": "domicilio",
            "direccion": "Ruta 40 km 12",
            "departamento": "Pocito",
        },
        store=store,
        conversation=conv,
        channel=channel,
    )
    assert result["status"] == "fuera_de_zona"
    assert result["alternativa"] == "taller"
    assert channel.handoffs == []


@pytest.mark.asyncio
async def test_revision_bateria_taller_sin_direccion(session: Session) -> None:
    store = ConversationStore(session)
    conv = store.get_or_create(
        IncomingMessage(conversation_ref="c10", text="x", message_id="m10", from_phone="1")
    )
    channel = FakeChannel()
    result = await execute_tool(
        "coordinar_turno",
        {"tipo_turno": "revision_bateria", "lugar_colocacion": "taller"},
        store=store,
        conversation=conv,
        channel=channel,
    )
    assert result["status"] == "handed_off"
    assert result["lugar_colocacion"] == "taller"
    assert "cargo_revision_ars" not in result
    assert channel.handoffs[0][1] == "turno:taller revisión de batería"


@pytest.mark.asyncio
async def test_revision_bateria_domicilio_informa_cargo(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "battery_revision_fee_ars", 12000)
    store = ConversationStore(session)
    conv = store.get_or_create(
        IncomingMessage(conversation_ref="c11", text="x", message_id="m11", from_phone="1")
    )
    channel = FakeChannel()
    result = await execute_tool(
        "coordinar_turno",
        {
            "tipo_turno": "revision_bateria",
            "lugar_colocacion": "domicilio",
            "direccion": "Av. Libertador 1234",
            "departamento": "Rawson",
        },
        store=store,
        conversation=conv,
        channel=channel,
    )
    assert result["status"] == "handed_off"
    assert result["cargo_revision_ars"] == 12000
    assert "12000" in result["aviso"]
    assert "se descuenta del pago final" in result["aviso"]
    assert "cargo revisión técnica: 12000" in channel.handoffs[0][1]


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Capital", "San Juan Capital"),
        ("San Juan", "San Juan Capital"),
        ("Ciudad de San Juan", "San Juan Capital"),
        ("rawson", "Rawson"),
        ("Departamento Chimbas", "Chimbas"),
        ("Santa Lucia", "Santa Lucía"),
        ("Santa Lucía", "Santa Lucía"),
        ("Rivadavia, San Juan", "Rivadavia"),
        ("Pocito", None),
        ("Mendoza", None),
        ("", None),
        (None, None),
    ],
)
def test_match_department(value, expected) -> None:
    from app.skills.service_area import match_department

    assert match_department(value) == expected
