import asyncio
import json
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.channel.base import IncomingMessage
from app.config import settings
from app.conversation.llm_debug import (
    configured_debug_phones,
    is_debug_phone,
    llm_debug_scope,
)
from app.conversation.llm_engine import run_turn
from app.conversation.orchestrator import handle_incoming
from app.conversation.state_store import ConversationStore
from app.conversation.tools import execute_tool
from app.db.session import init_db, make_engine
from app.models.llm_log import LlmLog
from app.skills.mechanical_estimator import clear_estimate_cache, estimate_repair
from app.skills.mechanical_service import MechanicalServiceSkill
from tests.fakes import FakeChannel

PHONE = "542644132300"
OTHER = "5491100000000"
PAYLOAD = {
    "descripcion": "Cambio de correa de distribución",
    "detalle_repuestos": "kit de distribución",
    "repuestos_ars": 120000,
    "horas_mano_de_obra": 3,
}
SLOTS = {
    "marca": "Volkswagen",
    "modelo": "Gol",
    "anio": "2014",
    "motor": "1.4",
    "descripcion_problema": "cambio de correa de distribución",
    "valor_hora_mano_de_obra": 20000,
}


class _Responses:
    def __init__(self, text: str) -> None:
        self.text = text
        self.kwargs: dict | None = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(output_text=self.text)


class _EstimatorClient:
    def __init__(self, text: str) -> None:
        self.responses = _Responses(text)


class _Function:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, name: str, arguments: str) -> None:
        self.id = "call-1"
        self.function = _Function(name, arguments)


class _ChatMessage:
    def __init__(self, content: str | None, tool_calls: list | None) -> None:
        self.content = content
        self.tool_calls = tool_calls

    def model_dump(self, exclude_none: bool = True) -> dict:
        return {"role": "assistant", "content": self.content}


class _Completions:
    def __init__(self, messages: list[_ChatMessage]) -> None:
        self._messages = messages
        self.calls = 0

    def create(self, **kwargs):
        message = self._messages[self.calls]
        self.calls += 1
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _ChatClient:
    def __init__(self, messages: list[_ChatMessage]) -> None:
        self.chat = SimpleNamespace(completions=_Completions(messages))


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as opened:
        yield opened


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    clear_estimate_cache()
    yield
    clear_estimate_cache()


def _rows(session: Session) -> list[LlmLog]:
    return list(session.exec(select(LlmLog).order_by(LlmLog.seq)).all())


def test_config_lists_the_two_phones() -> None:
    assert configured_debug_phones() == ["542644132300", "542645094418"]


def test_debug_phone_matches_whatsapp_mobile_prefix() -> None:
    assert is_debug_phone("542644132300")
    assert is_debug_phone("+54 264 413-2300")
    assert is_debug_phone("5492644132300")
    assert is_debug_phone("542645094418")
    assert not is_debug_phone(OTHER)
    assert not is_debug_phone(None)


def test_init_db_creates_llm_logs(tmp_path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'tecnobot.db'}")
    init_db(engine)
    names = set(SQLModel.metadata.tables)
    assert "llm_logs" in names
    from sqlalchemy import inspect

    assert "llm_logs" in inspect(engine).get_table_names()


def test_other_phone_does_not_log(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "labor_hourly_rate_ars", 0)
    client = _EstimatorClient(json.dumps(PAYLOAD))
    with llm_debug_scope(session, "conv-other", OTHER):
        result = estimate_repair(SLOTS, client=client)
    assert result["status"] == "ok"
    assert client.responses.kwargs is not None
    assert _rows(session) == []


def test_quoter_request_and_response_are_stored(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "labor_hourly_rate_ars", 0)
    raw = json.dumps(PAYLOAD)
    client = _EstimatorClient(raw)
    with llm_debug_scope(session, "conv-1", PHONE):
        result = estimate_repair(SLOTS, client=client)
    assert result["status"] == "ok"
    rows = _rows(session)
    assert [row.kind for row in rows] == ["pedido_cotizador", "respuesta_cotizador"]
    assert rows[0].phone == PHONE
    assert rows[0].conversation_id == "conv-1"
    pedido = rows[0].payload
    assert pedido["slots"]["descripcion_problema"] == "cambio de correa de distribución"
    assert pedido["busqueda"] == "web_search"
    assert "cambio de correa de distribución" in pedido["entrada"]
    assert "cache" not in pedido
    respuesta = rows[1].payload
    assert respuesta["llamo_modelo"] is True
    assert respuesta["texto"] == raw
    assert respuesta["resultado"]["status"] == "ok"
    assert respuesta["resultado"]["price"] == 180000


def test_missing_price_is_logged(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "labor_hourly_rate_ars", 20000)
    raw = json.dumps({"sin_precio": True, "motivo": "No hay precio del kit."})
    client = _EstimatorClient(raw)
    with llm_debug_scope(session, "conv-2", "+5492645094418"):
        result = estimate_repair(SLOTS, client=client)
    assert result["status"] == "sin_precio"
    assert client.responses.kwargs is not None
    rows = _rows(session)
    assert rows[1].payload["llamo_modelo"] is True
    assert rows[1].payload["resultado"]["status"] == "sin_precio"
    assert "Mercado Libre" in rows[0].payload["entrada"]
    assert rows[0].phone == "+5492645094418"


def test_quoter_timeout_is_logged_and_still_raises(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "labor_hourly_rate_ars", 20000)

    class _TimeoutClient:
        def __init__(self) -> None:
            self.responses = self

        def create(self, **kwargs):
            raise TimeoutError("web_search timed out")

    with llm_debug_scope(session, "conv-3", PHONE):
        with pytest.raises(TimeoutError):
            estimate_repair(SLOTS, client=_TimeoutClient())
    rows = _rows(session)
    assert [row.kind for row in rows] == ["pedido_cotizador", "respuesta_cotizador"]
    assert rows[0].payload["busqueda"] == "web_search"
    assert rows[1].payload["resultado"]["error"] == "TimeoutError"


@pytest.mark.asyncio
async def test_worker_thread_still_logs(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "labor_hourly_rate_ars", 0)
    client = _EstimatorClient(json.dumps(PAYLOAD))
    with llm_debug_scope(session, "conv-4", PHONE):
        result = await asyncio.to_thread(
            estimate_repair, SLOTS, client=client
        )
    assert result["status"] == "ok"
    assert [row.kind for row in _rows(session)] == ["pedido_cotizador", "respuesta_cotizador"]


def test_missing_slots_do_not_call_the_model(session: Session) -> None:
    with llm_debug_scope(session, "conv-5", PHONE):
        result = MechanicalServiceSkill().calculate_quote({"marca": "Ford"})
    assert result["status"] == "need_slots"
    rows = _rows(session)
    assert rows[1].payload["llamo_modelo"] is False
    assert rows[1].payload["resultado"]["missing"] == ["modelo", "anio", "descripcion_problema"]


@pytest.mark.asyncio
async def test_chat_decision_then_quoter_then_reply(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "labor_hourly_rate_ars", 20000)
    monkeypatch.setattr(settings, "llm_disable_thinking", True)
    monkeypatch.setattr(
        "app.skills.mechanical_estimator.build_client",
        lambda: _EstimatorClient(json.dumps(PAYLOAD)),
    )
    incoming = IncomingMessage(
        conversation_ref="wa-1",
        text="cuánto sale la correa",
        message_id="m1",
        from_phone=PHONE,
    )
    conversation = ConversationStore(session).get_or_create(incoming)
    args = {
        "marca": "Volkswagen",
        "modelo": "Gol",
        "anio": "2014",
        "motor": "1.4",
        "descripcion_problema": "cambio de correa de distribución",
        "valor_hora_mano_de_obra": 20000,
    }
    client = _ChatClient(
        [
            _ChatMessage(None, [_ToolCall("cotizar_mecanica_ia", json.dumps(args))]),
            _ChatMessage("La estimación del cambio de correa es $180.000 más impuestos.", None),
        ]
    )

    async def _tool(name: str, tool_args: dict) -> dict:
        return await execute_tool(
            name,
            tool_args,
            store=ConversationStore(session),
            conversation=conversation,
            channel=FakeChannel(),
        )

    with llm_debug_scope(session, conversation.id, conversation.phone):
        reply = await run_turn(
            [{"role": "user", "content": "cuánto sale la correa"}],
            _tool,
            client=client,
        )
    assert "180.000" in reply
    rows = _rows(session)
    assert [row.kind for row in rows] == [
        "decision_chat",
        "pedido_cotizador",
        "respuesta_cotizador",
        "decision_chat",
    ]
    decision = rows[0].payload
    assert decision["herramientas"][0]["nombre"] == "cotizar_mecanica_ia"
    assert decision["herramientas"][0]["argumentos"]["anio"] == "2014"
    assert decision["herramientas"][0]["argumentos"]["descripcion_problema"] == (
        "cambio de correa de distribución"
    )
    assert rows[1].payload["entrada"]
    assert "Gol" in rows[1].payload["entrada"]
    assert rows[2].payload["llamo_modelo"] is True
    assert rows[2].payload["resultado"]["status"] == "ok"
    assert rows[3].payload["texto"].startswith("La estimación")
    assert rows[3].payload["herramientas"] == []


@pytest.mark.asyncio
async def test_echo_mode_does_not_log(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "echo_mode", True)
    await handle_incoming(
        FakeChannel(),
        IncomingMessage(conversation_ref="wa-echo", text="hola", message_id="m-echo", from_phone=PHONE),
        session,
    )
    assert _rows(session) == []
