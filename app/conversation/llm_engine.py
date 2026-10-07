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
                "Busca precio de cambio de batería. Pasá los datos del auto que ya tengas; "
                "el catálogo indica si faltan campos."
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
                        "description": "Línea Heliar si el catálogo la pide, ej. Heliar Servicio",
                    },
                    "bateria": {
                        "type": "string",
                        "description": "Código Heliar si el catálogo lo pide, ej. F70ND",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "coordinar_turno",
            "description": (
                "Pasa el hilo a un empleado para agendar colocación. "
                "Usar después de cotizar, o si el cliente ya quiere turno. No inventar horarios."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "lugar_colocacion": {
                        "type": "string",
                        "enum": ["taller", "domicilio"],
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


def load_system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8").strip()


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
