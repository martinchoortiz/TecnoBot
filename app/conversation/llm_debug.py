"""Log de debug del chat y del cotizador, solo para los teléfonos del config."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from sqlmodel import Session

from app.models.llm_log import LlmLog

logger = logging.getLogger(__name__)

PHONES_PATH = Path(__file__).resolve().parents[2] / "config" / "llm_debug_phones.json"

# decision_chat: lo que decidió el LLM del chat (texto y tools).
# pedido_cotizador / respuesta_cotizador: lo que se le mandó al otro LLM y lo que contestó.
KIND_CHAT = "decision_chat"
KIND_QUOTER_REQUEST = "pedido_cotizador"
KIND_QUOTER_RESPONSE = "respuesta_cotizador"


@dataclass
class _DebugBuffer:
    session: Session
    conversation_id: str
    phone: str
    pending: list[tuple[int, str, dict[str, Any]]] = field(default_factory=list)
    seq: int = 0


_ctx: ContextVar[_DebugBuffer | None] = ContextVar("llm_debug", default=None)


def phone_keys(phone: str | None) -> set[str]:
    """Dígitos del teléfono, y la variante argentina con o sin el 9 de celular."""
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if not digits:
        return set()
    keys = {digits}
    if digits.startswith("549") and len(digits) > 4:
        keys.add("54" + digits[3:])
    elif digits.startswith("54") and len(digits) > 3:
        keys.add("549" + digits[2:])
    return keys


def configured_debug_phones() -> list[str]:
    try:
        data = json.loads(PHONES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("No se pudo leer %s", PHONES_PATH)
        return []
    phones = data.get("phones") if isinstance(data, dict) else None
    if not isinstance(phones, list):
        return []
    return [str(item).strip() for item in phones if str(item).strip()]


def is_debug_phone(phone: str | None) -> bool:
    keys = phone_keys(phone)
    if not keys:
        return False
    allowed: set[str] = set()
    for item in configured_debug_phones():
        allowed |= phone_keys(item)
    return bool(keys & allowed)


def llm_debug_active() -> bool:
    return _ctx.get() is not None


def _freeze(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        frozen = json.loads(json.dumps(payload, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return {"error": "payload_no_serializable"}
    if isinstance(frozen, dict):
        return frozen
    return {"valor": frozen}


def record_llm_event(kind: str, payload: dict[str, Any]) -> None:
    """Encola el evento. No toca la base: el hilo del cotizador también llama acá."""
    buf = _ctx.get()
    if buf is None:
        return
    buf.seq += 1
    buf.pending.append((buf.seq, kind, _freeze(payload)))


def log_chat_decision(payload: dict[str, Any]) -> None:
    record_llm_event(KIND_CHAT, payload)


def log_quoter(request: dict[str, Any], response: dict[str, Any]) -> None:
    record_llm_event(KIND_QUOTER_REQUEST, request)
    record_llm_event(KIND_QUOTER_RESPONSE, response)


def flush_llm_debug() -> None:
    """Persiste lo encolado. Llamar desde el hilo del request, nunca desde el del cotizador."""
    buf = _ctx.get()
    if buf is None or not buf.pending:
        return
    batch = buf.pending
    buf.pending = []
    try:
        for seq, kind, payload in batch:
            buf.session.add(
                LlmLog(
                    conversation_id=buf.conversation_id,
                    phone=buf.phone,
                    seq=seq,
                    kind=kind,
                    payload=payload,
                )
            )
        buf.session.commit()
    except Exception:
        logger.exception("No se pudo persistir llm_logs")
        try:
            buf.session.rollback()
        except Exception:
            pass


@contextmanager
def llm_debug_scope(session: Session, conversation_id: str, phone: str | None) -> Iterator[None]:
    if not is_debug_phone(phone):
        yield
        return
    token = _ctx.set(
        _DebugBuffer(
            session=session,
            conversation_id=conversation_id,
            phone=str(phone or ""),
        )
    )
    try:
        yield
    finally:
        try:
            flush_llm_debug()
        finally:
            _ctx.reset(token)
