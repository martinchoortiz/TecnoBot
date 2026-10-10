from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.channel.base import ChannelPort
from app.conversation.state_store import ConversationStore
from app.models.conversation import Conversation, ConversationState
from app.skills.battery_change import BatteryChangeSkill, VEHICLE_SLOTS
from app.skills.mechanical_service import MECHANICAL_SLOTS, MechanicalServiceSkill

logger = logging.getLogger(__name__)

_skill = BatteryChangeSkill()
_mechanical = MechanicalServiceSkill()


def _slot_updates(args: dict[str, Any]) -> dict[str, Any]:
    return {key: args.get(key) for key in VEHICLE_SLOTS}


def _mechanical_updates(args: dict[str, Any]) -> dict[str, Any]:
    return {key: args.get(key) for key in MECHANICAL_SLOTS}


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str, indent=2)


def _quote_for_log(quote: dict[str, Any]) -> dict[str, Any]:
    logged = dict(quote)
    fuentes = logged.get("fuentes")
    if isinstance(fuentes, list):
        logged["fuentes"] = [
            {"title": item.get("title"), "url": item.get("url")}
            for item in fuentes
            if isinstance(item, dict)
        ]
    return logged


def _log_quote_call(name: str, slots: dict[str, Any]) -> None:
    logger.info("Cotizador %s\nargs: %s", name, _dump(slots))


def _log_quote_response(name: str, quote: dict[str, Any]) -> None:
    logger.info("Cotizador %s\nrespuesta: %s", name, _dump(_quote_for_log(quote)))


async def execute_tool(
    name: str,
    args: dict[str, Any],
    *,
    store: ConversationStore,
    conversation: Conversation,
    channel: ChannelPort,
) -> dict[str, Any]:
    conv_id = conversation.id
    if name == "cotizar_bateria":
        slots = store.merge_vehicle_slots(conv_id, _slot_updates(args))
        _log_quote_call(name, slots)
        try:
            quote = _skill.calculate_quote(slots)
        except Exception:
            logger.exception("Cotizador %s falló", name)
            raise
        _log_quote_response(name, quote)
        if quote.get("status") == "ok":
            store.save_quote(
                conv_id,
                _skill.name,
                quote.get("slots") or slots,
                None if quote.get("price") is None else float(quote["price"]),
                quote.get("detail"),
            )
        return quote

    if name == "cotizar_mecanica_ia":
        slots = store.merge_vehicle_slots(conv_id, _mechanical_updates(args))
        _log_quote_call(name, slots)
        try:
            quote = await asyncio.to_thread(_mechanical.calculate_quote, slots)
        except Exception:
            logger.exception("Cotizador %s falló", name)
            raise
        _log_quote_response(name, quote)
        if quote.get("status") == "ok":
            stored_price = quote.get("price")
            if stored_price is None and quote.get("es_rango"):
                stored_price = (float(quote["total_min"]) + float(quote["total_max"])) / 2
            store.save_quote(
                conv_id,
                _mechanical.name,
                quote.get("slots") or slots,
                None if stored_price is None else float(stored_price),
                quote.get("detail"),
            )
        return quote

    if name == "coordinar_turno":
        lugar = str(args.get("lugar_colocacion") or "")
        last_quote = store.last_quote(conv_id)
        if last_quote and last_quote.skill == _mechanical.name:
            lugar = "taller"
        store.set_lugar_colocacion(conv_id, lugar)
        store.set_state(conv_id, ConversationState.HANDED_OFF)
        await channel.handoff_to_human(
            conversation.channel_ref,
            f"turno:{lugar} {args.get('nota') or ''}".strip(),
        )
        return {"status": "handed_off", "motivo": "coordinar_turno", "lugar_colocacion": lugar}

    if name == "derivar_a_humano":
        motivo = str(args.get("motivo") or "cliente")
        store.set_state(conv_id, ConversationState.HANDED_OFF)
        await channel.handoff_to_human(conversation.channel_ref, motivo)
        return {"status": "handed_off", "motivo": motivo}

    logger.warning("Tool desconocida: %s", name)
    return {"status": "error", "error": "unknown_tool"}
