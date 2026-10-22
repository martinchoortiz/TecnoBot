import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from app.channel.base import IncomingMessage
from app.channel.errors import ExternalServiceError
from app.channel.kapso_channel import KapsoChannel
from app.channel.kapso_handoff import (
    _ensure_api_trigger,
    clear_handoff_workflow_cache,
    e164,
    handoff_workflow_definition,
    merged_metadata,
    publish_contact_note,
    resolve_workflow_id,
    resume_secret_matches,
)
from app.config import settings
from app.conversation.state_store import ConversationStore
from app.db.session import init_db
from app.main import app
from app.models.conversation import ConversationState


def test_e164_and_metadata_merge() -> None:
    assert e164("549 264 111-2222") == "+5492641112222"
    assert e164("") == ""
    assert merged_metadata({"otro": 1}, "turno:taller") == {
        "otro": 1,
        "tecnobot_nota": "turno:taller",
    }
    assert merged_metadata(None, "nota")["tecnobot_nota"] == "nota"


def test_workflow_definition_is_handoff_then_resume() -> None:
    definition = handoff_workflow_definition("https://bot.example/", "sec")
    types = [node["data"]["node_type"] for node in definition["nodes"]]
    assert types == ["start", "handoff", "webhook"]
    webhook = definition["nodes"][2]["data"]["config"]
    assert webhook["url"] == "https://bot.example/webhooks/kapso/resume"
    assert webhook["headers"]["X-Resume-Secret"] == "sec"
    assert webhook["body_template"]["conversation_ref"] == "{{vars.conversation_ref}}"
    assert [edge["label"] for edge in definition["edges"]] == ["next", "next"]


def test_resume_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "kapso_webhook_secret", "sec")
    assert resume_secret_matches("sec")
    assert not resume_secret_matches("no")
    assert not resume_secret_matches("")
    monkeypatch.setattr(settings, "kapso_webhook_secret", "")
    assert not resume_secret_matches("sec")


@pytest.mark.asyncio
async def test_ensure_api_trigger_creates_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    posted: dict = {}

    class _Response:
        def json(self) -> dict:
            return {"data": []}

    async def get(*_args, **_kwargs):
        return _Response()

    async def post(*_args, **kwargs):
        posted["url"] = _args[2]
        posted["json"] = kwargs["json"]
        return _Response()

    monkeypatch.setattr("app.channel.kapso_handoff.external_get", get)
    monkeypatch.setattr("app.channel.kapso_handoff.external_post", post)
    await _ensure_api_trigger("wf-1")
    assert posted["url"].endswith("/workflows/wf-1/triggers")
    assert posted["json"] == {"trigger": {"trigger_type": "api_call", "active": True}}


@pytest.mark.asyncio
async def test_ensure_api_trigger_skips_when_active(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Response:
        def json(self) -> dict:
            return {"data": [{"id": "t1", "trigger_type": "api_call", "active": True}]}

    async def get(*_args, **_kwargs):
        return _Response()

    async def post(*_args, **_kwargs):
        raise AssertionError("no debería crear otro trigger")

    monkeypatch.setattr("app.channel.kapso_handoff.external_get", get)
    monkeypatch.setattr("app.channel.kapso_handoff.external_post", post)
    await _ensure_api_trigger("wf-1")


@pytest.mark.asyncio
async def test_publish_contact_note_merges_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "kapso_api_key", "k")
    monkeypatch.setattr(settings, "kapso_base_url", "https://api.kapso.ai")
    seen: dict = {}

    class _Response:
        def json(self) -> dict:
            return {"data": {"metadata": {"otro": 1}}}

    async def get(*_args, **_kwargs):
        return _Response()

    async def patch(*_args, **kwargs):
        seen["json"] = kwargs["json"]
        seen["url"] = _args[2]
        return _Response()

    monkeypatch.setattr("app.channel.kapso_handoff.external_get", get)
    monkeypatch.setattr("app.channel.kapso_handoff.external_patch", patch)
    await publish_contact_note("+54 9 264 111", "turno:taller")
    assert seen["url"].endswith("/whatsapp/contacts/549264111")
    assert seen["json"]["contact"]["metadata"] == {"otro": 1, "tecnobot_nota": "turno:taller"}


@pytest.mark.asyncio
async def test_resolve_workflow_id_without_public_url(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_handoff_workflow_cache()
    monkeypatch.setattr(settings, "kapso_public_base_url", "")
    monkeypatch.setattr(settings, "kapso_handoff_workflow_id", "wf-1")
    assert await resolve_workflow_id() == "wf-1"


@pytest.mark.asyncio
async def test_resolve_workflow_id_syncs_once(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_handoff_workflow_cache()
    monkeypatch.setattr(settings, "kapso_public_base_url", "https://bot.example")
    monkeypatch.setattr(settings, "kapso_webhook_secret", "sec")
    monkeypatch.setattr(settings, "kapso_handoff_workflow_id", "")
    calls: list[tuple] = []

    async def ensure(workflow_id, public_base, secret):
        calls.append((workflow_id, public_base, secret))
        return "wf-9"

    monkeypatch.setattr("app.channel.kapso_handoff.ensure_handoff_workflow", ensure)
    assert await resolve_workflow_id() == "wf-9"
    assert await resolve_workflow_id() == "wf-9"
    assert calls == [(None, "https://bot.example", "sec")]


@pytest.mark.asyncio
async def test_handoff_runs_note_assignment_and_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "kapso_api_key", "k")
    calls: list[tuple] = []

    async def note(phone, reason):
        calls.append(("nota", phone, reason))

    async def assign(ref, reason):
        calls.append(("asignacion", ref, reason))

    async def start(phone, ref, reason):
        calls.append(("workflow", phone, ref, reason))

    monkeypatch.setattr("app.channel.kapso_channel.publish_contact_note", note)
    monkeypatch.setattr("app.channel.kapso_channel.assign_conversation", assign)
    monkeypatch.setattr("app.channel.kapso_channel.start_handoff_execution", start)
    await KapsoChannel().handoff_to_human("conv", "turno:taller", phone="54911")
    assert calls == [
        ("nota", "54911", "turno:taller"),
        ("asignacion", "conv", "turno:taller"),
        ("workflow", "54911", "conv", "turno:taller"),
    ]


@pytest.mark.asyncio
async def test_handoff_continues_when_note_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "kapso_api_key", "k")
    calls: list[str] = []
    failures: list[str] = []

    async def note(_phone, _reason):
        raise ExternalServiceError("kapso", "contacto")

    async def assign(_ref, _reason):
        calls.append("asignacion")

    async def start(_phone, _ref, _reason):
        calls.append("workflow")

    def record(_exc, _ref, action):
        failures.append(action)

    monkeypatch.setattr("app.channel.kapso_channel.publish_contact_note", note)
    monkeypatch.setattr("app.channel.kapso_channel.assign_conversation", assign)
    monkeypatch.setattr("app.channel.kapso_channel.start_handoff_execution", start)
    monkeypatch.setattr("app.channel.kapso_channel._record_handoff_failure", record)
    await KapsoChannel().handoff_to_human("conv", "turno:taller", phone="54911")
    assert failures == ["nota"]
    assert calls == ["asignacion", "workflow"]


def _resume_client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    init_db(engine)
    monkeypatch.setattr("app.main.get_engine", lambda: engine)
    monkeypatch.setattr(settings, "kapso_webhook_secret", "sec")
    session = Session(engine)
    return TestClient(app), session


def test_resume_sets_conversation_active(monkeypatch: pytest.MonkeyPatch) -> None:
    client, session = _resume_client(monkeypatch)
    store = ConversationStore(session)
    conv = store.get_or_create(
        IncomingMessage(conversation_ref="conv_resume", text="x", message_id="m", from_phone="549264111")
    )
    store.set_state(conv.id, ConversationState.HANDED_OFF)
    with client:
        response = client.post(
            "/webhooks/kapso/resume",
            json={"conversation_ref": "conv_resume"},
            headers={"X-Resume-Secret": "sec"},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "active"
    session.refresh(conv)
    assert conv.state == ConversationState.ACTIVE.value
    session.close()


def test_resume_finds_by_phone(monkeypatch: pytest.MonkeyPatch) -> None:
    client, session = _resume_client(monkeypatch)
    store = ConversationStore(session)
    conv = store.get_or_create(
        IncomingMessage(conversation_ref="conv_phone", text="x", message_id="m2", from_phone="549264111")
    )
    store.set_state(conv.id, ConversationState.HANDED_OFF)
    with client:
        denied = client.post(
            "/webhooks/kapso/resume",
            json={"phone": "+549264111"},
            headers={"X-Resume-Secret": "otra"},
        )
        missing = client.post(
            "/webhooks/kapso/resume",
            json={"conversation_ref": "{{vars.conversation_ref}}", "phone": "+549264111"},
            headers={"X-Resume-Secret": "sec"},
        )
    assert denied.status_code == 401
    assert missing.status_code == 200
    session.refresh(conv)
    assert conv.state == ConversationState.ACTIVE.value
    session.close()
