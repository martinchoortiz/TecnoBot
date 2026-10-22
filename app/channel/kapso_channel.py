from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

from sqlmodel import Session

from app.channel.base import ChannelPort, IncomingMessage
from app.channel.errors import ExternalServiceError
from app.channel.http import external_post
from app.channel.kapso_handoff import (
    assign_conversation,
    publish_contact_note,
    start_handoff_execution,
)
from app.config import settings
from app.db.error_log import record_internal_error
from app.db.session import get_engine

logger = logging.getLogger(__name__)


def _record_handoff_failure(exc: Exception, conversation_ref: str, action: str) -> None:
    logger.exception("Handoff Kapso falló en %s conv=%s", action, conversation_ref)
    try:
        with Session(get_engine()) as session:
            record_internal_error(
                session,
                source="channel",
                exc=exc,
                channel_ref=conversation_ref,
                extra={"action": action},
            )
    except Exception:
        logger.exception("No se pudo persistir el fallo de handoff")


GRAPH_VERSION = "v24.0"


def messages_url(base_url: str, phone_number_id: str) -> str:
    return (
        f"{base_url.rstrip('/')}/meta/whatsapp/{GRAPH_VERSION}/{phone_number_id}/messages"
    )


def verify_kapso_signature(raw_body: bytes, signature: str | None, secret: str) -> bool:
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _text_from_message(message: dict[str, Any]) -> str:
    if not isinstance(message, dict):
        return ""
    kapso = message.get("kapso") or {}
    if isinstance(kapso, dict) and kapso.get("content"):
        return str(kapso["content"])
    text = message.get("text")
    if isinstance(text, dict) and text.get("body"):
        return str(text["body"])
    if message.get("content"):
        return str(message["content"])
    return ""


def extract_incoming_payloads(raw: dict[str, Any]) -> list[dict[str, Any]]:
    if raw.get("batch") is True and isinstance(raw.get("data"), list):
        return [item for item in raw["data"] if isinstance(item, dict)]
    return [raw]


class KapsoChannel(ChannelPort):
    def parse_incoming(self, raw_payload: dict) -> list[IncomingMessage]:
        messages: list[IncomingMessage] = []
        for item in extract_incoming_payloads(raw_payload):
            message = item.get("message") or {}
            conversation = item.get("conversation") or {}
            if not isinstance(message, dict) or not isinstance(conversation, dict):
                continue
            direction = (message.get("kapso") or {}).get("direction")
            if direction == "outbound":
                continue
            text = _text_from_message(message).strip()
            conversation_ref = str(conversation.get("id") or "")
            message_id = str(message.get("id") or "")
            if not conversation_ref or not text:
                continue
            messages.append(
                IncomingMessage(
                    conversation_ref=conversation_ref,
                    text=text,
                    message_id=message_id,
                    from_phone=conversation.get("phone_number") or message.get("from"),
                    from_user_id=conversation.get("business_scoped_user_id")
                    or message.get("from_user_id"),
                )
            )
        return messages

    async def send_text(self, conversation_ref: str, text: str, to: str | None = None) -> None:
        if not settings.kapso_api_key or not settings.kapso_phone_number_id:
            raise ExternalServiceError(
                "kapso",
                "API key o phone_number_id no configurados",
                url=None,
            )
        if not to:
            raise ExternalServiceError("kapso", f"send_text sin teléfono destino (conv={conversation_ref})")
        url = messages_url(settings.kapso_base_url, settings.kapso_phone_number_id)
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }
        headers = {
            "X-API-Key": settings.kapso_api_key,
            "Content-Type": "application/json",
        }
        await external_post("kapso", "send_text", url, json=payload, headers=headers)

    async def handoff_to_human(
        self,
        conversation_ref: str,
        reason: str,
        *,
        phone: str | None = None,
    ) -> None:
        logger.info("Handoff conv=%s reason=%s", conversation_ref, reason)
        if not settings.kapso_api_key:
            return
        steps = (
            ("nota", publish_contact_note(phone, reason)),
            ("asignacion", assign_conversation(conversation_ref, reason)),
            ("workflow", start_handoff_execution(phone, conversation_ref, reason)),
        )
        for action, step in steps:
            try:
                await step
            except Exception as exc:
                _record_handoff_failure(exc, conversation_ref, action)
