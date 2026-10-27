import json
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.channel.base import IncomingMessage
from app.config import settings
from app.conversation.llm_engine import TOOLS, load_system_prompt
from app.conversation.state_store import ConversationStore
from app.conversation.tools import execute_tool
from app.models.conversation import Quote
from app.skills.mechanical_estimator import (
    ESTIMATE_DISCLAIMER,
    PLUS_TAX,
    assemble_quote,
    build_estimator_input,
    clear_estimate_cache,
    estimate_cache_key,
    estimate_repair,
    load_estimator_prompt,
    parse_estimate_payload,
)
from app.skills.mechanical_service import MechanicalServiceSkill
from tests.fakes import FakeChannel


PAYLOAD = {
    "descripcion": "Cambio de pastillas de freno delanteras",
    "detalle_repuestos": "juego de pastillas",
    "repuestos_ars": 45000,
    "horas_mano_de_obra": 1.5,
}

SLOTS = {
    "marca": "Volkswagen",
    "modelo": "Gol",
    "anio": "2014",
    "generacion": "G6",
    "trim": "Comfortline",
    "motor": "1.6",
    "transmision": "manual",
    "descripcion_problema": "frena largo, hay que cambiar las pastillas",
    "valor_hora_mano_de_obra": 20000,
}


class _Responses:
    def __init__(self, text: str) -> None:
        self.text = text
        self.kwargs: dict | None = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(output_text=self.text)


class _Client:
    def __init__(self, text: str) -> None:
        self.responses = _Responses(text)


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    clear_estimate_cache()
    yield
    clear_estimate_cache()


ITEMS_PAYLOAD = {
    "descripcion": "Service de caja automática",
    "alcance": "cambio parcial de aceite, filtro y junta",
    "repuestos_items": [
        {
            "concepto": "Aceite ATF",
            "cantidad": 5,
            "unidad": "l",
            "precio_unitario_ars_min": 20000,
            "precio_unitario_ars_max": 24000,
        },
        {"concepto": "Filtro de caja", "cantidad": 1, "unidad": "u", "precio_unitario_ars": 50000},
    ],
    "horas_mano_de_obra_min": 2,
    "horas_mano_de_obra_max": 2.5,
}


def test_parts_are_summed_from_items_by_the_system(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "labor_hourly_rate_ars", 0)
    result = assemble_quote(SLOTS, ITEMS_PAYLOAD)
    assert result["status"] == "ok"
    assert result["repuestos_ars_min"] == 150000
    assert result["repuestos_ars_max"] == 170000
    assert result["mano_obra_ars_min"] == 40000
    assert result["mano_obra_ars_max"] == 50000
    assert result["total_min"] == 190000
    assert result["total_max"] == 220000
    assert result["detalle_repuestos"] == "Aceite ATF: 5 l × entre $20.000 y $24.000, Filtro de caja: 1 × $50.000"
    assert "Aceite ATF: 5 l × entre $20.000 y $24.000" in result["detail"]
    assert "Filtro de caja: 1 × $50.000" in result["detail"]
    assert len(result["repuestos_items"]) == 2


def test_invalid_items_fall_back_to_the_part_total(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "labor_hourly_rate_ars", 0)
    broken = {**PAYLOAD, "repuestos_items": [{"concepto": "Aceite", "cantidad": 0, "precio_unitario_ars": 10}]}
    assert assemble_quote(SLOTS, broken)["price"] == 75000
    no_total = {**ITEMS_PAYLOAD, "repuestos_items": [{"concepto": "Aceite"}]}
    assert assemble_quote(SLOTS, no_total) == {"status": "error", "error": "estimacion_invalida"}


def test_estimator_runs_at_temperature_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "labor_hourly_rate_ars", 0)
    client = _Client(json.dumps(PAYLOAD))
    estimate_repair(SLOTS, client=client)
    assert client.responses.kwargs["temperature"] == 0


def test_cache_key_ignores_case_accents_and_spacing() -> None:
    first = {**SLOTS, "descripcion_problema": "Cambio aceite  caja automática", "marca": "Ford"}
    second = {**SLOTS, "descripcion_problema": "cambio aceite caja automatica", "marca": " ford "}
    assert estimate_cache_key(first) == estimate_cache_key(second)
    assert estimate_cache_key(first) != estimate_cache_key({**first, "motor": "3.2"})


def test_same_vehicle_and_job_reuses_the_estimate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "labor_hourly_rate_ars", 0)
    monkeypatch.setattr(settings, "estimate_cache_ttl_hours", 24)
    first_client = _Client(json.dumps(PAYLOAD))
    first = estimate_repair(SLOTS, client=first_client)
    other_payload = {**PAYLOAD, "repuestos_ars": 999999}
    second_client = _Client(json.dumps(other_payload))
    second = estimate_repair(
        {**SLOTS, "valor_hora_mano_de_obra": 20000, "descripcion_problema": "Frena largo, hay que cambiar las PASTILLAS"},
        client=second_client,
    )
    assert second["price"] == first["price"] == 75000
    assert second_client.responses.kwargs is None


def test_cache_still_applies_the_current_hourly_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "labor_hourly_rate_ars", 20000)
    estimate_repair(SLOTS, client=_Client(json.dumps(PAYLOAD)))
    monkeypatch.setattr(settings, "labor_hourly_rate_ars", 40000)
    again = estimate_repair(SLOTS, client=_Client("{}"))
    assert again["mano_obra_ars"] == 60000


def test_cache_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "labor_hourly_rate_ars", 0)
    monkeypatch.setattr(settings, "estimate_cache_ttl_hours", 0)
    estimate_repair(SLOTS, client=_Client(json.dumps(PAYLOAD)))
    client = _Client(json.dumps({**PAYLOAD, "repuestos_ars": 60000}))
    result = estimate_repair(SLOTS, client=client)
    assert client.responses.kwargs is not None
    assert result["price"] == 90000


def test_failed_estimates_are_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "labor_hourly_rate_ars", 0)
    monkeypatch.setattr(settings, "estimate_cache_ttl_hours", 24)
    estimate_repair(SLOTS, client=_Client("no pude"))
    client = _Client(json.dumps(PAYLOAD))
    result = estimate_repair(SLOTS, client=client)
    assert client.responses.kwargs is not None
    assert result["status"] == "ok"


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def test_missing_slots_does_not_estimate() -> None:
    calls: list[dict] = []
    skill = MechanicalServiceSkill(estimator=lambda slots: calls.append(slots) or {})
    result = skill.calculate_quote({"marca": "VW", "modelo": "Gol"})
    assert result["status"] == "need_slots"
    assert result["missing"] == ["anio", "descripcion_problema"]
    assert calls == []


def test_missing_labor_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "labor_hourly_rate_ars", 0)
    calls: list[dict] = []
    skill = MechanicalServiceSkill(estimator=lambda slots: calls.append(slots) or {})
    result = skill.calculate_quote(
        {
            "marca": "VW",
            "modelo": "Gol",
            "anio": "2014",
            "descripcion_problema": "no arranca",
        }
    )
    assert result == {"status": "error", "error": "falta_valor_hora"}
    assert calls == []


def test_workshop_rate_overrides_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "labor_hourly_rate_ars", 30000)
    seen: dict = {}

    def estimator(slots: dict) -> dict:
        seen.update(slots)
        return assemble_quote(slots, PAYLOAD)

    skill = MechanicalServiceSkill(estimator=estimator)
    result = skill.calculate_quote({**SLOTS, "valor_hora_mano_de_obra": 1})
    assert seen["valor_hora_mano_de_obra"] == 30000
    assert result["mano_obra_ars"] == 45000
    assert result["price"] == 90000


def test_assemble_sums_parts_and_labor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "labor_hourly_rate_ars", 0)
    result = assemble_quote(SLOTS, PAYLOAD)
    assert result["status"] == "ok"
    assert result["repuestos_ars"] == 45000
    assert result["mano_obra_ars"] == 30000
    assert result["price"] == 75000
    assert result["es_rango"] is False
    assert result["total_min"] == result["total_max"] == 75000
    assert "entre" not in result["detail"]
    assert f"Total estimado: $75.000, {PLUS_TAX}." in result["detail"]
    assert result["aviso"] == ESTIMATE_DISCLAIMER
    assert result["detail"].endswith(ESTIMATE_DISCLAIMER)
    assert "Cambio de pastillas" in result["descripcion"]
    assert result["slots"]["trim"] == "Comfortline"


def test_range_is_what_the_client_hears(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "labor_hourly_rate_ars", 0)
    result = assemble_quote(
        SLOTS,
        {
            "descripcion": "Cambio de pastillas de freno delanteras",
            "detalle_repuestos": "juego de pastillas",
            "repuestos_ars_min": 50000,
            "repuestos_ars_max": 40000,
            "horas_mano_de_obra_min": 1,
            "horas_mano_de_obra_max": 2,
        },
    )
    assert result["es_rango"] is True
    assert result["price"] is None
    assert result["repuestos_ars_min"] == 40000
    assert result["repuestos_ars_max"] == 50000
    assert result["mano_obra_ars_min"] == 20000
    assert result["mano_obra_ars_max"] == 40000
    assert result["total_min"] == 60000
    assert result["total_max"] == 90000
    assert "entre $40.000 y $50.000" in result["detail"]
    assert "entre 1 h y 2 h" in result["detail"]
    assert f"Total estimado: entre $60.000 y $90.000, {PLUS_TAX}." in result["detail"]
    assert result["detail"].endswith(ESTIMATE_DISCLAIMER)


def test_parse_fenced_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "labor_hourly_rate_ars", 0)
    text = '```json\n{"descripcion": "Ajuste", "repuestos_ars": "20.000", "horas_mano_de_obra": "1,5"}\n```'
    payload = parse_estimate_payload(text)
    assert payload["descripcion"] == "Ajuste"
    quote = assemble_quote(
        {**SLOTS, "valor_hora_mano_de_obra": "20.000"},
        payload,
    )
    assert quote["repuestos_ars"] == 20000
    assert quote["mano_obra_ars"] == 30000
    assert quote["price"] == 50000


def test_estimator_input_includes_vehicle_and_problem() -> None:
    text = build_estimator_input(SLOTS)
    for bit in ("Volkswagen", "Gol", "2014", "G6", "Comfortline", "1.6", "manual", "pastillas", "Mercado Libre"):
        assert bit in text
    assert "No inventes precios." in text


def test_estimator_is_a_fresh_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "labor_hourly_rate_ars", 0)
    monkeypatch.setattr(settings, "llm_disable_thinking", True)
    monkeypatch.setattr(settings, "llm_estimator_model", "deepseek-v4-pro")
    raw = json.dumps(PAYLOAD, ensure_ascii=False)
    client = _Client(raw)
    result = estimate_repair(SLOTS, client=client)
    sent = client.responses.kwargs
    assert sent is not None
    assert sent["tools"] == [{"type": "web_search"}]
    assert sent["tool_choice"] == {"type": "web_search"}
    assert sent["model"] == "deepseek-v4-pro"
    assert "messages" not in sent
    assert "frena largo" in sent["input"]
    assert "Gol" in sent["input"]
    assert "Mercado Libre" in sent["instructions"]
    assert result["price"] == 75000
    assert result["aviso"] == ESTIMATE_DISCLAIMER


def test_missing_published_price_is_sin_precio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "labor_hourly_rate_ars", 20000)
    raw = json.dumps({"sin_precio": True, "motivo": "No hay precio del kit en Mercado Libre."})
    client = _Client(raw)
    result = estimate_repair(SLOTS, client=client)
    assert result["status"] == "sin_precio"
    assert "Mercado Libre" in result["motivo"]
    assert "price" not in result
    assert client.responses.kwargs is not None


def test_estimator_can_ask_for_more_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "labor_hourly_rate_ars", 20000)
    raw = json.dumps(
        {
            "necesita_detalle": True,
            "faltan": ["motor", "descripcion_problema"],
            "motivo": "Los resultados no son de un service de Onix.",
        }
    )
    result = estimate_repair(
        {
            "marca": "Chevrolet",
            "modelo": "Onix",
            "anio": "2017",
            "descripcion_problema": "service",
            "valor_hora_mano_de_obra": 20000,
        },
        client=_Client(raw),
    )
    assert result["status"] == "need_slots"
    assert result["missing"] == ["motor", "descripcion_problema"]
    assert result["motivo"] == "Los resultados no son de un service de Onix."
    assert "price" not in result


def test_symptom_needing_inspection_gets_no_price(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "labor_hourly_rate_ars", 20000)
    raw = json.dumps({"requiere_revision": True, "motivo": "Un ruido en el tren delantero puede ser varias piezas."})
    result = estimate_repair(
        {**SLOTS, "descripcion_problema": "hace un ruido raro adelante"},
        client=_Client(raw),
    )
    assert result == {
        "status": "requiere_revision",
        "motivo": "Un ruido en el tren delantero puede ser varias piezas.",
    }
    again = estimate_repair(
        {**SLOTS, "descripcion_problema": "hace un ruido raro adelante"},
        client=_Client(json.dumps(PAYLOAD)),
    )
    assert again["status"] == "ok"


def test_prompt_requires_formal_address() -> None:
    text = load_system_prompt()
    assert "Siempre de usted" in text
    assert "Importante:" in text
    assert "qué bajón" in text


def test_prompts_split_concrete_jobs_from_symptoms() -> None:
    text = load_system_prompt()
    assert "handed_off" in text
    assert "Síntoma sin causa conocida" in text
    assert "Revisión pedida" in text
    estimator = load_estimator_prompt()
    assert "sin_precio" in estimator
    assert "Mercado Libre" in estimator
    assert "No inventes precios" in estimator
    assert "estimalo con tu conocimiento del mercado" not in estimator


def test_estimator_does_not_reask_data_already_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "labor_hourly_rate_ars", 20000)
    raw = json.dumps({"necesita_detalle": True, "faltan": ["motor"], "motivo": "falta motor"})
    result = estimate_repair(SLOTS, client=_Client(raw))
    assert result == {"status": "error", "error": "busqueda_vacia"}


def test_unreadable_estimate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "labor_hourly_rate_ars", 20000)
    client = _Client("no pude calcularlo")
    result = estimate_repair(SLOTS, client=client)
    assert result == {"status": "error", "error": "estimacion_invalida"}


def test_prompt_quotes_battery_before_handoff() -> None:
    text = load_system_prompt()
    assert "Cuándo llamar `cotizar_bateria` (dos momentos)" in text
    assert "Precio aceptado por el cliente" in text
    assert "usá `derivar_a_humano`" in text
    assert "No encontré un modelo de batería para el vehículo indicado." in text
    quote = next(item["function"] for item in TOOLS if item["function"]["name"] == "cotizar_bateria")
    assert "ni coordines turno" in quote["description"]
    assert "derivar_a_humano" in quote["description"]
    handoff = next(item["function"] for item in TOOLS if item["function"]["name"] == "coordinar_turno")
    assert "precio aceptado" in handoff["description"]
    assert "no la llames" in handoff["description"]


def test_prompt_states_home_battery_revision_fee(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "battery_revision_fee_ars", 15000)
    text = load_system_prompt()
    assert "Cargo de revisión técnica a domicilio (batería): 15000 pesos argentinos." in text
    assert "se descuenta del pago final" in text
    assert "Siempre que ofrezcas la revisión a domicilio" in text
    tool = next(item["function"] for item in TOOLS if item["function"]["name"] == "coordinar_turno")
    assert "cargo de revisión técnica" in tool["description"]


def test_prompt_does_not_invent_revision_fee(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "battery_revision_fee_ars", 0)
    text = load_system_prompt()
    assert "Cargo de revisión técnica a domicilio (batería): no está configurado." in text
    assert "No inventes un monto." in text


def test_prompt_tells_controller_when_to_quote(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "labor_hourly_rate_ars", 18000)
    text = load_system_prompt()
    assert "cotizar_mecanica_ia" in text
    assert "Trabajo concreto" in text
    assert "volvé a llamar `cotizar_mecanica_ia`" in text
    assert "necesita_detalle" in load_estimator_prompt()
    assert "es_rango" in text
    assert "total_min" in text
    assert "más impuestos" in text
    assert "No indiques el porcentaje ni el importe de impuestos" in text
    assert "sin impuestos" in load_estimator_prompt()
    assert "18000" in text
    tool = next(item["function"] for item in TOOLS if item["function"]["name"] == "cotizar_mecanica_ia")
    assert "más impuestos" in tool["description"]
    assert "problema en el vehículo" in tool["description"]
    assert "handed_off" in tool["description"]
    assert "need_slots" in tool["description"]
    assert tool["parameters"]["required"] == [
        "descripcion_problema",
        "valor_hora_mano_de_obra",
        "marca",
        "modelo",
        "anio",
    ]


@pytest.mark.asyncio
async def test_tool_persists_estimate_without_handoff(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "labor_hourly_rate_ars", 20000)

    def estimator(slots: dict) -> dict:
        return assemble_quote(slots, PAYLOAD)

    monkeypatch.setattr("app.conversation.tools._mechanical._estimator", estimator)
    store = ConversationStore(session)
    conv = store.get_or_create(
        IncomingMessage(conversation_ref="c-mec", text="x", message_id="m-mec", from_phone="1")
    )
    channel = FakeChannel()
    result = await execute_tool(
        "cotizar_mecanica_ia",
        {
            "marca": "Volkswagen",
            "modelo": "Gol",
            "anio": "2014",
            "descripcion_problema": "frena largo",
            "valor_hora_mano_de_obra": 999,
        },
        store=store,
        conversation=conv,
        channel=channel,
    )
    assert result["status"] == "ok"
    assert result["price"] == 75000
    assert channel.handoffs == []
    quotes = session.exec(select(Quote)).all()
    assert len(quotes) == 1
    assert quotes[0].skill == "atencion_mecanica"
    assert quotes[0].price == 75000
    assert ESTIMATE_DISCLAIMER in (quotes[0].detail or "")


@pytest.mark.asyncio
async def test_tool_persists_range_in_the_client_text(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "labor_hourly_rate_ars", 20000)
    ranged = {
        "descripcion": "Cambio de pastillas de freno delanteras",
        "detalle_repuestos": "juego de pastillas",
        "repuestos_ars_min": 40000,
        "repuestos_ars_max": 50000,
        "horas_mano_de_obra_min": 1,
        "horas_mano_de_obra_max": 2,
    }

    def estimator(slots: dict) -> dict:
        return assemble_quote(slots, ranged)

    monkeypatch.setattr("app.conversation.tools._mechanical._estimator", estimator)
    store = ConversationStore(session)
    conv = store.get_or_create(
        IncomingMessage(conversation_ref="c-rango", text="x", message_id="m-rango", from_phone="1")
    )
    result = await execute_tool(
        "cotizar_mecanica_ia",
        {
            "marca": "Volkswagen",
            "modelo": "Gol",
            "anio": "2014",
            "descripcion_problema": "frena largo",
        },
        store=store,
        conversation=conv,
        channel=FakeChannel(),
    )
    assert result["es_rango"] is True
    assert result["price"] is None
    assert "entre $60.000 y $90.000" in result["detail"]
    quote = session.exec(select(Quote)).one()
    assert quote.price == 75000
    assert "entre $60.000 y $90.000" in (quote.detail or "")


@pytest.mark.asyncio
async def test_missing_quote_hands_off_immediately(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "labor_hourly_rate_ars", 20000)
    incoming = IncomingMessage(conversation_ref="c-sin", text="x", message_id="m-sin", from_phone="1")
    args = {
        "marca": "Volkswagen",
        "modelo": "Gol",
        "anio": "2014",
        "descripcion_problema": "cambio de correa de distribución",
        "valor_hora_mano_de_obra": 1,
    }
    channel = FakeChannel()
    store = ConversationStore(session)
    conv = store.get_or_create(incoming)

    def ask_detail(slots: dict) -> dict:
        return {"status": "need_slots", "missing": ["motor"], "motivo": "cambia el kit"}

    monkeypatch.setattr("app.conversation.tools._mechanical._estimator", ask_detail)
    asked = await execute_tool(
        "cotizar_mecanica_ia", args, store=store, conversation=conv, channel=channel
    )
    assert asked["status"] == "need_slots"
    assert channel.handoffs == []

    def no_price(slots: dict) -> dict:
        return {"status": "sin_precio", "motivo": "No hay precio del kit."}

    monkeypatch.setattr("app.conversation.tools._mechanical._estimator", no_price)
    failed = await execute_tool(
        "cotizar_mecanica_ia", args, store=store, conversation=conv, channel=channel
    )
    assert failed["status"] == "handed_off"
    assert failed["mensaje"] == (
        "No pude resolver su solicitud porque no encontré precios correctos para su auto. "
        "Lo paso con un empleado."
    )
    assert len(channel.handoffs) == 1
    assert "precios correctos" in channel.handoffs[0][1]
    assert store.get(conv.id).state == "handed_off"
    again = await execute_tool(
        "cotizar_mecanica_ia", args, store=store, conversation=conv, channel=channel
    )
    assert again["mensaje"] == failed["mensaje"]
    assert len(channel.handoffs) == 1
