import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlmodel import Session

from app.channel.delivery import record_delivery_failures
from app.channel.kapso_channel import KapsoChannel, verify_kapso_signature
from app.config import settings
from app.conversation.orchestrator import handle_incoming
from app.db.error_log import record_internal_error
from app.db.session import get_engine, init_db

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db(get_engine())
    yield


app = FastAPI(title="TecnoBot", version="0.1.0", lifespan=lifespan)
channel = KapsoChannel()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "tecnobot"}


@app.post("/webhooks/kapso")
async def kapso_webhook(
    request: Request,
    x_webhook_signature: str | None = Header(default=None),
    x_webhook_event: str | None = Header(default=None),
) -> PlainTextResponse:
    raw = await request.body()
    if settings.kapso_webhook_secret:
        if not verify_kapso_signature(raw, x_webhook_signature, settings.kapso_webhook_secret):
            raise HTTPException(status_code=401, detail="Invalid signature")
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid payload")

    if x_webhook_event and x_webhook_event != "whatsapp.message.received":
        with Session(get_engine()) as session:
            if record_delivery_failures(session, x_webhook_event, payload):
                return PlainTextResponse("OK")
        return PlainTextResponse("OK")

    try:
        incoming_list = channel.parse_incoming(payload)
    except Exception as exc:
        with Session(get_engine()) as session:
            record_internal_error(session, source="channel", exc=exc, extra={"stage": "parse_incoming"})
        return PlainTextResponse("OK")
    with Session(get_engine()) as session:
        for incoming in incoming_list:
            try:
                await handle_incoming(channel, incoming, session)
            except Exception as exc:
                record_internal_error(
                    session,
                    source="webhook",
                    exc=exc,
                    channel_ref=incoming.conversation_ref,
                    extra={"message_id": incoming.message_id},
                )
    return PlainTextResponse("OK")
