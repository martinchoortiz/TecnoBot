from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.channel.base import ChannelPort
from app.config import settings
from app.conversation.state_store import ConversationStore
from app.models.conversation import Conversation, ConversationState
from app.skills.battery_change import BatteryChangeSkill, VEHICLE_SLOTS
from app.skills.mechanical_service import MECHANICAL_SLOTS, MechanicalServiceSkill
from app.skills.service_area import check_service_address

logger = logging.getLogger(__name__)

_skill = BatteryChangeSkill()
_mechanical = MechanicalServiceSkill()

TIPO_REVISION_BATERIA = "revision_bateria"


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


def _battery_turn_block(
    last_quote: Any,
    lugar: str,
    args: dict[str, Any],
) -> dict[str, Any] | None:
    """El turno de batería solo se coordina con marca y modelo, precio aceptado y domicilio."""
    missing: list[str] = []
    confirmed = bool(
        last_quote is not None
        and last_quote.skill == _skill.name
        and last_quote.price is not None
        and last_quote.detail
    )
    if not confirmed:
        missing.append("bateria")
    if args.get("precio_aceptado") is not True:
        missing.append("precio_aceptado")
    if lugar != "domicilio":
        missing.append("domicilio")
    if not missing:
        return None
    return {
        "status": "no_listo",
        "missing": missing,
        "motivo": (
            "Para coordinar el turno tienen que estar confirmados la batería "
            "(marca y modelo), el precio aceptado por el cliente y el domicilio. "
            "Si no hay batería, faltan datos o hubo un error, no preguntes más "
            "y usá derivar_a_humano. No coordines el turno."
        ),
    }


def _revision_fee(applies: bool) -> int | float | None:
    if not applies:
        return None
    fee = float(settings.battery_revision_fee_ars or 0)
    if fee <= 0:
        return None
    return int(fee) if fee == int(fee) else fee


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
        revision_bateria = args.get("tipo_turno") == TIPO_REVISION_BATERIA
        # La revisión de batería puede ser a domicilio aunque antes haya habido una
        # estimación mecánica; solo el trabajo mecánico queda siempre en el taller.
        is_mechanical = not revision_bateria and bool(
            last_quote and last_quote.skill == _mechanical.name
        )
        if is_mechanical:
            lugar = "taller"
        battery_turn = (not is_mechanical) and (
            revision_bateria
            or lugar == "domicilio"
            or bool(last_quote and last_quote.skill == _skill.name)
        )
        if battery_turn:
            blocked = _battery_turn_block(last_quote, lugar, args)
            if blocked is not None:
                logger.info("coordinar_turno sin handoff: %s", _dump(blocked))
                return blocked
            lugar = "domicilio"
        direccion = str(args.get("direccion") or "").strip()
        departamento = ""
        if not is_mechanical and lugar == "domicilio":
            # Solo a domicilio: la dirección tiene que estar dentro del Gran San Juan.
            # En taller no importa el domicilio del cliente.
            check = check_service_address(direccion, args.get("departamento"))
            if check["status"] != "ok":
                logger.info("coordinar_turno sin handoff: %s", _dump(check))
                return check
            departamento = check["departamento"]
            store.set_service_address(conv_id, direccion, departamento)
        store.set_lugar_colocacion(conv_id, lugar)
        store.set_state(conv_id, ConversationState.HANDED_OFF)
        note_parts = [f"turno:{lugar}"]
        if revision_bateria:
            note_parts.append("revisión de batería")
        if battery_turn and last_quote is not None and last_quote.detail and last_quote.price is not None:
            price = int(last_quote.price) if float(last_quote.price) == int(last_quote.price) else last_quote.price
            note_parts.append(f"batería: {last_quote.detail} ${price}")
        if direccion and departamento:
            note_parts.append(f"dirección: {direccion} ({departamento})")
        cargo = _revision_fee(revision_bateria and lugar == "domicilio")
        if cargo is not None:
            note_parts.append(
                f"cargo revisión técnica: {cargo} (se descuenta si cambia la batería)"
            )
        if args.get("nota"):
            note_parts.append(str(args["nota"]))
        await channel.handoff_to_human(
            conversation.channel_ref,
            " ".join(note_parts),
            phone=conversation.phone,
        )
        result: dict[str, Any] = {
            "status": "handed_off",
            "motivo": "coordinar_turno",
            "lugar_colocacion": lugar,
        }
        if cargo is not None:
            result["cargo_revision_ars"] = cargo
            result["aviso"] = (
                f"Si todavía no se lo dijiste, informale con 'Importante:' que la revisión técnica "
                f"a domicilio tiene un cargo de {cargo} pesos: si el problema no es la batería se abona; "
                "si cambia la batería, se descuenta del pago final."
            )
        return result

    if name == "derivar_a_humano":
        motivo = str(args.get("motivo") or "cliente")
        store.set_state(conv_id, ConversationState.HANDED_OFF)
        await channel.handoff_to_human(
            conversation.channel_ref,
            motivo,
            phone=conversation.phone,
        )
        return {"status": "handed_off", "motivo": motivo}

    logger.warning("Tool desconocida: %s", name)
    return {"status": "error", "error": "unknown_tool"}
