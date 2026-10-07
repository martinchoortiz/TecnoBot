from __future__ import annotations

import logging
import traceback
from typing import Any

from sqlmodel import Session

from app.models.errors import InternalError

logger = logging.getLogger(__name__)

_MAX_TEXT = 4000
_MAX_TRACE = 8000


def _http_status(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    if response is not None:
        code = getattr(response, "status_code", None)
        if isinstance(code, int):
            return code
    return None


def _exc_details(exc: BaseException) -> dict[str, Any]:
    details: dict[str, Any] = {"traceback": traceback.format_exc()[-_MAX_TRACE:]}
    body = getattr(exc, "body", None)
    if body is not None:
        details["body"] = body if not isinstance(body, str) else body[:_MAX_TEXT]
    response = getattr(exc, "response", None)
    if response is not None:
        text = getattr(response, "text", None)
        if isinstance(text, str) and text:
            details["response_text"] = text[:_MAX_TEXT]
    return details


def record_internal_error(
    session: Session,
    *,
    source: str,
    exc: BaseException | None = None,
    message: str | None = None,
    error_type: str | None = None,
    conversation_id: str | None = None,
    channel_ref: str | None = None,
    extra: dict[str, Any] | None = None,
) -> InternalError | None:
    details = dict(extra or {})
    resolved_type = error_type or "Error"
    http_status = None
    text = message or "internal error"
    if exc is not None:
        resolved_type = error_type or type(exc).__name__
        text = str(exc) or text
        http_status = _http_status(exc)
        details.update(_exc_details(exc))
    row = InternalError(
        source=source,
        error_type=resolved_type,
        http_status=http_status,
        message=text[:_MAX_TEXT],
        details=details,
        conversation_id=conversation_id,
        channel_ref=channel_ref,
    )
    try:
        session.add(row)
        session.commit()
        session.refresh(row)
        logger.error(
            "internal_error id=%s source=%s type=%s status=%s msg=%s",
            row.id,
            source,
            resolved_type,
            http_status,
            text[:200],
        )
        return row
    except Exception:
        logger.exception("No se pudo persistir internal_error")
        try:
            session.rollback()
        except Exception:
            pass
        return None
