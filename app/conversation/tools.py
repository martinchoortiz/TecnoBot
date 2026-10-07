from __future__ import annotations

import logging
from typing import Any

from app.channel.base import ChannelPort
from app.conversation.state_store import ConversationStore
from app.models.conversation import Conversation, ConversationState
from app.skills.battery_change import BatteryChangeSkill, VEHICLE_SLOTS

logger = logging.getLogger(__name__)

_skill = BatteryChangeSkill()


def _slot_updates(args: dict[str, Any]) -> dict[str, Any]:
    return {key: args.get(key) for key in VEHICLE_SLOTS}


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
        quote = _skill.calculate_quote(slots)
        if quote.get("status") == "ok":
            store.save_quote(
                conv_id,
                _skill.name,
                quote.get("slots") or slots,
                float(quote["price"]),
                quote.get("detail"),
            )
        return quote

    if name == "coordinar_turno":
        lugar = str(args.get("lugar_colocacion") or "")
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
