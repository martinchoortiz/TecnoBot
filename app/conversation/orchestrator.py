from __future__ import annotations

import logging
from typing import Any

from sqlmodel import Session

from app.channel.base import ChannelPort, IncomingMessage
from app.config import settings
from app.conversation.llm_debug import llm_debug_scope
from app.conversation.llm_engine import run_turn
from app.conversation.state_store import ConversationStore
from app.conversation.tools import execute_tool
from app.db.error_log import record_internal_error
from app.models.conversation import ConversationState

logger = logging.getLogger(__name__)

FALLBACK = "Disculpá, tuve un problema para responder. ¿Me lo repetís?"


def _history(store: ConversationStore, conversation_id: str) -> list[dict[str, str]]:
    rows = store.recent_messages(conversation_id, limit=24)
    history: list[dict[str, str]] = []
    for row in rows:
        if row.role not in ("user", "assistant"):
            continue
        history.append({"role": row.role, "content": row.content})
    return history


async def handle_incoming(
    channel: ChannelPort,
    incoming: IncomingMessage,
    session: Session,
) -> None:
    store = ConversationStore(session)
    conversation = store.get_or_create(incoming)

    if store.already_logged(incoming.message_id):
        logger.info("Mensaje duplicado ignorado: %s", incoming.message_id)
        return

    store.add_message(conversation.id, "user", incoming.text, incoming.message_id)

    if conversation.state == ConversationState.HANDED_OFF.value:
        logger.info("Hilo %s en handoff; el bot no responde", conversation.channel_ref)
        return

    if settings.echo_mode:
        reply = f"Recibí: {incoming.text}"
        store.add_message(conversation.id, "assistant", reply)
        try:
            await channel.send_text(incoming.conversation_ref, reply, to=incoming.from_phone)
        except Exception as exc:
            record_internal_error(
                session,
                source="channel",
                exc=exc,
                conversation_id=conversation.id,
                channel_ref=conversation.channel_ref,
            )
        return

    if not settings.llm_api_key:
        record_internal_error(
            session,
            source="orchestrator",
            message="ECHO_MODE off y falta LLM_API_KEY",
            conversation_id=conversation.id,
            channel_ref=conversation.channel_ref,
        )
        return

    async def _run_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
        current = store.get(conversation.id) or conversation
        try:
            return await execute_tool(name, args, store=store, conversation=current, channel=channel)
        except Exception as exc:
            record_internal_error(
                session,
                source="tool",
                exc=exc,
                conversation_id=conversation.id,
                channel_ref=conversation.channel_ref,
                extra={"tool": name, "args": args},
            )
            return {"status": "error", "error": "tool_failed"}

    try:
        with llm_debug_scope(session, conversation.id, conversation.phone or incoming.from_phone):
            reply = await run_turn(_history(store, conversation.id), _run_tool)
    except Exception as exc:
        record_internal_error(
            session,
            source="llm",
            exc=exc,
            conversation_id=conversation.id,
            channel_ref=conversation.channel_ref,
        )
        reply = FALLBACK

    if not reply:
        return
    store.add_message(conversation.id, "assistant", reply)
    try:
        await channel.send_text(incoming.conversation_ref, reply, to=incoming.from_phone)
    except Exception as exc:
        record_internal_error(
            session,
            source="channel",
            exc=exc,
            conversation_id=conversation.id,
            channel_ref=conversation.channel_ref,
        )
