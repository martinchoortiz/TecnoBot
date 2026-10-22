"""Handoff visible en el inbox de Kapso.

Al derivar: metadata del contacto (pestaña Info), asignación opcional y un
workflow que deja el banner amarillo. Resume from Handoff llama a
`/webhooks/kapso/resume` y el bot vuelve a `active`.
"""

from __future__ import annotations

import hmac
import logging
from typing import Any
from urllib.parse import quote

from app.channel.errors import ExternalServiceError
from app.channel.http import external_get, external_patch, external_post
from app.config import settings

logger = logging.getLogger(__name__)

HANDOFF_SLUG = "tecnobot-handoff"
HANDOFF_NAME = "Tecnobot handoff"
NOTE_KEY = "tecnobot_nota"

_synced_workflow: dict[str, str] = {}


def clear_handoff_workflow_cache() -> None:
    _synced_workflow.clear()


def e164(phone: str | None) -> str:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if not digits:
        return ""
    return f"+{digits}"


def phone_digits(phone: str | None) -> str:
    return "".join(ch for ch in (phone or "") if ch.isdigit())


def merged_metadata(current: Any, note: str) -> dict[str, Any]:
    base = dict(current) if isinstance(current, dict) else {}
    base[NOTE_KEY] = note
    return base


def resume_secret_matches(provided: str | None) -> bool:
    secret = settings.kapso_webhook_secret
    if not secret or not provided:
        return False
    try:
        return hmac.compare_digest(secret, provided)
    except Exception:
        return False


def unresolved(value: str) -> bool:
    return value.startswith("{{") and value.endswith("}}")


def handoff_workflow_definition(public_base_url: str, secret: str) -> dict[str, Any]:
    resume_url = f"{public_base_url.rstrip('/')}/webhooks/kapso/resume"
    return {
        "nodes": [
            {
                "id": "start",
                "type": "flow-node",
                "position": {"x": 80, "y": 120},
                "data": {"node_type": "start", "config": {}, "display_name": "Start"},
            },
            {
                "id": "handoff_empleado",
                "type": "flow-node",
                "position": {"x": 360, "y": 120},
                "data": {
                    "node_type": "handoff",
                    "display_name": "Handoff",
                    "config": {
                        "reason": "{{vars.nota}}",
                        "context_data": {
                            "nota": "{{vars.nota}}",
                            "conversation_ref": "{{vars.conversation_ref}}",
                        },
                    },
                },
            },
            {
                "id": "avisar_resume",
                "type": "flow-node",
                "position": {"x": 640, "y": 120},
                "data": {
                    "node_type": "webhook",
                    "display_name": "Avisar al bot",
                    "config": {
                        "url": resume_url,
                        "method": "POST",
                        "headers": {"X-Resume-Secret": secret},
                        "body_template": {
                            "conversation_ref": "{{vars.conversation_ref}}",
                            "phone": "{{context.phone_number}}",
                        },
                    },
                },
            },
        ],
        "edges": [
            {"source": "start", "target": "handoff_empleado", "label": "next"},
            {"source": "handoff_empleado", "target": "avisar_resume", "label": "next"},
        ],
    }


def _headers() -> dict[str, str]:
    return {"X-API-Key": settings.kapso_api_key, "Content-Type": "application/json"}


def _platform(path: str) -> str:
    return f"{settings.kapso_base_url.rstrip('/')}/platform/v1{path}"


def _data(response_json: Any) -> dict[str, Any]:
    if isinstance(response_json, dict) and isinstance(response_json.get("data"), dict):
        return response_json["data"]
    return {}


async def publish_contact_note(phone: str | None, note: str) -> None:
    digits = phone_digits(phone)
    if not settings.kapso_api_key or not digits:
        logger.info("Nota de handoff omitida: falta API key o teléfono")
        return
    identifier = quote(digits, safe="")
    current = await external_get(
        "kapso",
        "get_contact",
        _platform(f"/whatsapp/contacts/{identifier}"),
        headers=_headers(),
    )
    metadata = _data(current.json()).get("metadata")
    await external_patch(
        "kapso",
        "update_contact",
        _platform(f"/whatsapp/contacts/{identifier}"),
        json={"contact": {"metadata": merged_metadata(metadata, note)}},
        headers=_headers(),
    )
    logger.info("Nota de handoff en el contacto %s", digits)


async def assign_conversation(conversation_ref: str, note: str) -> None:
    user_id = settings.kapso_handoff_user_id.strip()
    if not settings.kapso_api_key or not user_id or not conversation_ref:
        return
    await external_post(
        "kapso",
        "assign_conversation",
        _platform(f"/whatsapp/conversations/{quote(conversation_ref, safe='')}/assignments"),
        json={"assignment": {"user_id": user_id, "notes": note}},
        headers=_headers(),
    )
    logger.info("Hilo %s asignado en Kapso", conversation_ref)


async def start_handoff_execution(phone: str | None, conversation_ref: str, note: str) -> None:
    if not settings.kapso_api_key or not settings.kapso_phone_number_id:
        logger.info("Workflow de handoff omitido: falta API key o phone_number_id")
        return
    number = e164(phone)
    if not number:
        logger.info("Workflow de handoff omitido: sin teléfono conv=%s", conversation_ref)
        return
    workflow_id = await resolve_workflow_id()
    if not workflow_id:
        return
    await external_post(
        "kapso",
        "start_handoff_workflow",
        _platform(f"/workflows/{workflow_id}/executions"),
        json={
            "workflow_execution": {
                "phone_number": number,
                "phone_number_id": settings.kapso_phone_number_id,
                "variables": {
                    "conversation_ref": conversation_ref,
                    "nota": note,
                },
            }
        },
        headers=_headers(),
    )
    logger.info("Workflow de handoff arrancado conv=%s workflow=%s", conversation_ref, workflow_id)


async def resolve_workflow_id() -> str | None:
    public = settings.kapso_public_base_url.strip().rstrip("/")
    configured = settings.kapso_handoff_workflow_id.strip()
    if not public:
        if configured:
            return configured
        logger.info(
            "Workflow de handoff omitido: falta KAPSO_PUBLIC_BASE_URL o KAPSO_HANDOFF_WORKFLOW_ID"
        )
        return None
    if not settings.kapso_webhook_secret:
        logger.info("Workflow de handoff omitido: falta KAPSO_WEBHOOK_SECRET para el resume")
        return None
    cached = _synced_workflow.get(public)
    if cached:
        return cached
    workflow_id = await ensure_handoff_workflow(configured or None, public, settings.kapso_webhook_secret)
    _synced_workflow[public] = workflow_id
    return workflow_id


async def ensure_handoff_workflow(workflow_id: str | None, public_base: str, secret: str) -> str:
    if not workflow_id:
        workflow_id = await _find_workflow_id()
    if not workflow_id:
        workflow_id = await _create_workflow(public_base, secret)
    await _sync_definition(workflow_id, public_base, secret)
    await _ensure_api_trigger(workflow_id)
    return workflow_id


async def _find_workflow_id() -> str | None:
    response = await external_get("kapso", "list_workflows", _platform("/workflows"), headers=_headers())
    payload = response.json()
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, dict) and row.get("slug") == HANDOFF_SLUG and row.get("id"):
            return str(row["id"])
    return None


async def _create_workflow(public_base: str, secret: str) -> str:
    try:
        response = await external_post(
            "kapso",
            "create_handoff_workflow",
            _platform("/workflows"),
            json={
                "workflow": {
                    "name": HANDOFF_NAME,
                    "slug": HANDOFF_SLUG,
                    "description": (
                        "Banner de handoff del inbox. Resume from Handoff avisa a TecnoBot."
                    ),
                    "definition": handoff_workflow_definition(public_base, secret),
                }
            },
            headers=_headers(),
        )
    except ExternalServiceError as exc:
        if exc.status_code != 422:
            raise
        found = await _find_workflow_id()
        if not found:
            raise
        return found
    workflow_id = str(_data(response.json()).get("id") or "")
    if not workflow_id:
        raise ExternalServiceError("kapso", "create_handoff_workflow sin id")
    return workflow_id


async def _sync_definition(workflow_id: str, public_base: str, secret: str) -> None:
    current = await external_get(
        "kapso",
        "get_workflow",
        _platform(f"/workflows/{workflow_id}"),
        headers=_headers(),
    )
    lock_version = _data(current.json()).get("lock_version")
    workflow: dict[str, Any] = {
        "status": "active",
        "inbound_message_read_mode": "disabled",
        "definition": handoff_workflow_definition(public_base, secret),
    }
    if isinstance(lock_version, int):
        workflow["lock_version"] = lock_version
    await external_patch(
        "kapso",
        "update_handoff_workflow",
        _platform(f"/workflows/{workflow_id}"),
        json={"workflow": workflow},
        headers=_headers(),
    )


async def _ensure_api_trigger(workflow_id: str) -> None:
    """Kapso rechaza el arranque si el workflow no tiene un trigger api_call activo."""
    response = await external_get(
        "kapso",
        "list_workflow_triggers",
        _platform(f"/workflows/{workflow_id}/triggers"),
        headers=_headers(),
    )
    payload = response.json()
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        rows = []
    for row in rows:
        if not isinstance(row, dict) or row.get("trigger_type") != "api_call":
            continue
        if row.get("active") is False and row.get("id"):
            await external_patch(
                "kapso",
                "enable_api_trigger",
                _platform(f"/triggers/{row['id']}"),
                json={"trigger": {"active": True}},
                headers=_headers(),
            )
        return
    await external_post(
        "kapso",
        "create_api_trigger",
        _platform(f"/workflows/{workflow_id}/triggers"),
        json={"trigger": {"trigger_type": "api_call", "active": True}},
        headers=_headers(),
    )
