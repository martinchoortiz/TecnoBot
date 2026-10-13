from __future__ import annotations

import json
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import Any

from openai import OpenAI

from app.config import settings

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "system_prompt.md"
MAX_TOOL_ROUNDS = 4

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "cotizar_bateria",
            "description": (
                "Busca precio de cambio de batería. Pasá los datos del auto que ya tengas "
                "(también los que el cliente dio antes en la conversación); "
                "el catálogo indica si faltan campos. Si hay varias baterías que le sirven al auto, "
                "devuelve `opciones` con el total de cada una."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "marca": {"type": "string"},
                    "modelo": {"type": "string"},
                    "anio": {"type": "string", "description": "Año del auto, ej. 2014"},
                    "generacion": {"type": "string"},
                    "trim": {"type": "string", "description": "Versión comercial, ej. Comfortline"},
                    "motor": {"type": "string", "description": "Motorización, ej. 2.0 TSI"},
                    "transmision": {"type": "string"},
                    "linea": {
                        "type": "string",
                        "description": "Solo si el cliente dice qué línea Heliar tiene, ej. Heliar Servicio",
                    },
                    "bateria": {
                        "type": "string",
                        "description": "Solo si el cliente dice qué batería tiene, ej. F70ND",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cotizar_mecanica_ia",
            "description": (
                "Estimá un arreglo mecánico o el cambio de una pieza. "
                "Llamala cuando el cliente tiene un problema en el vehículo o quiere cotizar "
                "ese arreglo o recambio, y el trabajo es concreto. Si es un síntoma sin causa conocida "
                "o el cliente pide una revisión, no la llames: ofrecé turno de revisión. "
                "No la uses para batería ni neumáticos. "
                "Busca en internet y devuelve una estimación, no un precio cerrado. "
                "Si viene un rango, pasalo tal cual al cliente. "
                "El total es siempre más impuestos: decilo así y no aclares el porcentaje ni el importe. "
                "Si devuelve requiere_revision, no des precio: ofrecé revisión en el taller. "
                "Si devuelve need_slots, preguntá solo esos datos y volvé a llamarla; no derives todavía."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "descripcion_problema": {
                        "type": "string",
                        "description": (
                            "El trabajo pedido, corto y neutro, con las palabras del cliente "
                            "(ej. 'cambio de aceite de caja automática'). No repitas marca, modelo, "
                            "año ni motor, y no agregues cantidades ni piezas que el cliente no dijo."
                        ),
                    },
                    "valor_hora_mano_de_obra": {
                        "type": "number",
                        "description": "Pesos argentinos por hora de mano de obra del taller",
                    },
                    "marca": {"type": "string"},
                    "modelo": {"type": "string"},
                    "anio": {"type": "string", "description": "Año del auto, ej. 2014"},
                    "generacion": {"type": "string"},
                    "trim": {"type": "string", "description": "Versión comercial, ej. Comfortline"},
                    "motor": {"type": "string", "description": "Motorización, ej. 1.6"},
                    "transmision": {"type": "string"},
                },
                "required": [
                    "descripcion_problema",
                    "valor_hora_mano_de_obra",
                    "marca",
                    "modelo",
                    "anio",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "coordinar_turno",
            "description": (
                "Pasa el hilo a un empleado para agendar. "
                "Usarla después de cotizar batería, o cuando el cliente confirma una estimación "
                "mecánica y quiere turno: el empleado da el precio exacto y agenda. "
                "Los trabajos mecánicos son siempre en el taller (lugar_colocacion=taller). "
                "Si es a domicilio (lugar_colocacion=domicilio) hace falta `direccion` y `departamento` "
                "del cliente: solo se hacen visitas en el Gran San Juan (San Juan Capital, Rivadavia, "
                "Rawson, Chimbas, Santa Lucía). Si devuelve need_slots, pedí lo que falta; si devuelve "
                "fuera_de_zona, avisá que no se hacen visitas técnicas en esa localidad (no hay handoff) "
                "y ofrecé solamente la atención en el taller. "
                "En taller no se pide dirección ni se valida zona. "
                "Si el auto no arranca o se sospecha de la batería y el cliente quiere que la revisen, "
                "usá tipo_turno=revision_bateria: la revisión puede ser a domicilio (con las mismas "
                "reglas de zona) o en el taller. "
                "Al ofrecer la revisión a domicilio, informá siempre el cargo de revisión técnica "
                "del final de las instrucciones: si el problema no es la batería se abona; "
                "si se cambia la batería, se descuenta del pago final. En el taller no hay ese cargo. "
                "No inventar horarios."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "lugar_colocacion": {
                        "type": "string",
                        "enum": ["taller", "domicilio"],
                    },
                    "direccion": {
                        "type": "string",
                        "description": "Dirección que dio el cliente (calle, número, barrio/localidad). Solo para domicilio.",
                    },
                    "departamento": {
                        "type": "string",
                        "description": (
                            "Departamento o localidad de San Juan de esa dirección, tal como lo dijo el "
                            "cliente o lo deducís con seguridad (ej. 'Rawson', 'Capital'). "
                            "Si no lo sabés, no lo inventes: preguntalo. Solo para domicilio."
                        ),
                    },
                    "tipo_turno": {
                        "type": "string",
                        "enum": ["colocacion", "revision_bateria"],
                        "description": (
                            "`revision_bateria` cuando el turno es para revisar la batería "
                            "(no arranca / se sospecha de la batería). Si no, omitilo."
                        ),
                    },
                    "nota": {"type": "string"},
                },
                "required": ["lugar_colocacion"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "derivar_a_humano",
            "description": "Deriva a un empleado cuando el bot no puede resolver o el cliente lo pide.",
            "parameters": {
                "type": "object",
                "properties": {"motivo": {"type": "string"}},
                "required": ["motivo"],
            },
        },
    },
]


def _pesos(amount: float) -> str:
    return str(int(amount)) if amount == int(amount) else str(amount)


def _labor_note() -> str:
    rate = float(settings.labor_hourly_rate_ars or 0)
    if rate > 0:
        shown = _pesos(rate)
        return (
            f"Hora de mano de obra del taller: {shown} pesos argentinos. "
            f"En `cotizar_mecanica_ia` pasá `valor_hora_mano_de_obra` = {shown}."
        )
    return (
        "Hora de mano de obra del taller: no está configurada. "
        "No llames `cotizar_mecanica_ia` con un valor inventado; derivá a un empleado."
    )


def _revision_fee_note() -> str:
    fee = float(settings.battery_revision_fee_ars or 0)
    if fee > 0:
        shown = _pesos(fee)
        return (
            f"Cargo de revisión técnica a domicilio (batería): {shown} pesos argentinos. "
            "Al ofrecer esa revisión, informalo siempre con 'Importante:', con ese monto. "
            "Si el problema no es la batería, el cliente abona esa asistencia técnica. "
            "Si cambia la batería, ese importe se descuenta del pago final. "
            "En el taller no hay ese cargo. No inventes otro monto."
        )
    return (
        "Cargo de revisión técnica a domicilio (batería): no está configurado. "
        "No inventes un monto. Igual avisá que, si el problema no es la batería, "
        "se abona la asistencia técnica, y que si se cambia la batería ese cargo se descuenta del pago final."
    )


def load_system_prompt() -> str:
    text = PROMPT_PATH.read_text(encoding="utf-8").strip()
    return f"{text}\n\n{_labor_note()}\n{_revision_fee_note()}"


def build_client() -> OpenAI:
    return OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)


def _message_dump(message: Any) -> dict[str, Any]:
    if hasattr(message, "model_dump"):
        dumped = message.model_dump(exclude_none=True)
        return dumped
    return dict(message)


async def run_turn(
    history: list[dict[str, str]],
    execute_tool: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
    client: OpenAI | None = None,
) -> str:
    llm = client or build_client()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": load_system_prompt()},
        *history,
    ]
    for _ in range(MAX_TOOL_ROUNDS):
        kwargs: dict[str, Any] = {
            "model": settings.llm_model,
            "messages": messages,
            "tools": TOOLS,
        }
        if settings.llm_disable_thinking:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        response = llm.chat.completions.create(**kwargs)
        choice = response.choices[0].message
        tool_calls = getattr(choice, "tool_calls", None) or []
        if not tool_calls:
            return (choice.content or "").strip()

        messages.append(_message_dump(choice))
        for call in tool_calls:
            name = call.function.name
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            if not isinstance(args, dict):
                args = {}
            result = await execute_tool(name, args)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

    return "Disculpá, no pude cerrar la consulta. Te derivo con un compañero del taller."
