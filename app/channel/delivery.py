from typing import Any

from sqlmodel import Session

from app.channel.kapso_channel import extract_incoming_payloads
from app.db.error_log import record_internal_error

DELIVERY_FAIL_EVENTS = frozenset({"whatsapp.message.failed"})


def _failures(raw_payload: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for item in extract_incoming_payloads(raw_payload):
        message = item.get("message") if isinstance(item.get("message"), dict) else {}
        conversation = item.get("conversation") if isinstance(item.get("conversation"), dict) else {}
        kapso = message.get("kapso") if isinstance(message.get("kapso"), dict) else {}
        errors: list[Any] = []
        for status in kapso.get("statuses") or []:
            if isinstance(status, dict) and status.get("errors"):
                errors.extend(status["errors"])
        found.append(
            {
                "conversation_ref": conversation.get("id"),
                "message_id": message.get("id"),
                "status": kapso.get("status"),
                "errors": errors,
            }
        )
    return found


def record_delivery_failures(session: Session, event: str, payload: dict[str, Any]) -> bool:
    if event not in DELIVERY_FAIL_EVENTS:
        return False
    for item in _failures(payload):
        errors = item.get("errors") or []
        first = errors[0] if errors and isinstance(errors[0], dict) else {}
        message = str(first.get("message") or first.get("title") or "whatsapp.message.failed")
        record_internal_error(
            session,
            source="channel",
            error_type="KapsoDeliveryFailed",
            message=message,
            channel_ref=item.get("conversation_ref"),
            extra={"event": event, **item},
        )
    return True
