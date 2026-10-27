from __future__ import annotations

import copy
import json
import logging
import re
import threading
import time
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openai import OpenAI

from app.config import settings
from app.conversation.llm_debug import llm_debug_active, log_quoter
from app.conversation.llm_engine import build_client

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "mechanical_estimator.md"
SEARCH_TIMEOUT_SECONDS = 120.0
# 0 = lo más determinista que ofrece el modelo; la variación entre consultas no se resuelve con creatividad.
ESTIMATOR_TEMPERATURE = 0.0
_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_LOCK = threading.Lock()
ESTIMATE_DISCLAIMER = "Es una estimación y está sujeta a verificación final."
PLUS_TAX = "más impuestos"
WEB_SEARCH_TOOL = {"type": "web_search"}

SLOT_KEYS = (
    "descripcion_problema",
    "valor_hora_mano_de_obra",
    "marca",
    "modelo",
    "anio",
    "generacion",
    "trim",
    "motor",
    "transmision",
)

_FIELD_LABELS = (
    ("generacion", "Generación"),
    ("trim", "Versión comercial"),
    ("motor", "Motor (cilindrada)"),
    ("transmision", "Transmisión"),
)
_REFINE_SLOTS = ("motor", "trim", "generacion", "transmision")
_DETAIL_KEYS = (*_REFINE_SLOTS, "descripcion_problema")
_DETAIL_LABELS = {
    "motor": "el motor",
    "trim": "la versión",
    "generacion": "la generación",
    "transmision": "la transmisión",
    "descripcion_problema": "un detalle más concreto del trabajo (kilometraje o qué incluye)",
}


def load_estimator_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8").strip()


def resolve_hourly_rate(slots: dict[str, Any]) -> float | None:
    """La hora configurada del taller gana sobre el número que mande el chat."""
    configured = float(settings.labor_hourly_rate_ars or 0)
    if configured > 0:
        return configured
    return _positive_number(slots.get("valor_hora_mano_de_obra"))


def build_estimator_input(slots: dict[str, Any]) -> str:
    lines = [
        "Buscá en Mercado Libre Argentina y en páginas de repuestos de Argentina.",
        "No inventes precios.",
        f"Problema: {str(slots.get('descripcion_problema') or '').strip()}",
        f"Marca: {str(slots.get('marca') or '').strip()}",
        f"Modelo: {str(slots.get('modelo') or '').strip()}",
        f"Año: {str(slots.get('anio') or '').strip()}",
    ]
    for key, label in _FIELD_LABELS:
        value = str(slots.get(key) or "").strip()
        if value:
            lines.append(f"{label}: {value}")
    return "\n".join(lines)


def parse_estimate_payload(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, count=1, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw, count=1)
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("sin json")
    data = json.loads(raw[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("json no objeto")
    return data


def assemble_quote(slots: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    rate = resolve_hourly_rate(slots)
    if rate is None:
        return {"status": "error", "error": "falta_valor_hora"}

    descripcion = str(_first(payload, "descripcion", "descripcion_arreglo") or "").strip()
    detalle = _detail_text(payload)
    items = _parts_from_items(payload)
    if items is not None:
        parts, detalle, normalized_items = items
    else:
        normalized_items = []
        parts = _span(payload, ("repuestos_ars", "precio_repuestos", "valor_repuestos"), ("repuestos_ars_min",), ("repuestos_ars_max",))
    hours = _span(payload, ("horas_mano_de_obra", "horas"), ("horas_mano_de_obra_min",), ("horas_mano_de_obra_max",))
    if not descripcion or parts is None or hours is None:
        return {"status": "error", "error": "estimacion_invalida"}
    parts_low, parts_high = parts
    hours_low, hours_high = hours
    if (
        parts_low < 0
        or parts_high > 100_000_000
        or hours_low <= 0
        or hours_high > 80
    ):
        return {"status": "error", "error": "estimacion_invalida"}

    rate_pesos = _pesos(rate)
    parts_min = _pesos(parts_low)
    parts_max = _pesos(parts_high)
    labor_min = _pesos(hours_low * rate_pesos)
    labor_max = _pesos(hours_high * rate_pesos)
    total_min = parts_min + labor_min
    total_max = parts_max + labor_max
    is_range = total_min != total_max
    parts_bit = _money_span(parts_min, parts_max)
    if detalle:
        parts_bit = f"{parts_bit} ({detalle})"
    detail = (
        f"{descripcion} "
        f"Repuestos estimados: {parts_bit}. "
        f"Precio de mano de obra: {_hours_span(hours_low, hours_high)} × {_money(rate_pesos)} = {_money_span(labor_min, labor_max)}. "
        f"Total estimado: {_money_span(total_min, total_max)}, {PLUS_TAX}. "
        f"{ESTIMATE_DISCLAIMER}"
    )
    quote: dict[str, Any] = {
        "status": "ok",
        "es_rango": is_range,
        "price": None if is_range else total_min,
        "detail": detail,
        "descripcion": descripcion,
        "detalle_repuestos": detalle,
        "repuestos_items": normalized_items,
        "repuestos_ars_min": parts_min,
        "repuestos_ars_max": parts_max,
        "horas_mano_de_obra_min": hours_low,
        "horas_mano_de_obra_max": hours_high,
        "valor_hora_mano_de_obra": rate_pesos,
        "mano_obra_ars_min": labor_min,
        "mano_obra_ars_max": labor_max,
        "total_min": total_min,
        "total_max": total_max,
        "aviso": ESTIMATE_DISCLAIMER,
        "fuentes": [],
        "slots": _snapshot(slots, rate_pesos),
    }
    if not is_range:
        quote["repuestos_ars"] = parts_min
        quote["horas_mano_de_obra"] = hours_low
        quote["mano_obra_ars"] = labor_min
    return quote


def estimate_repair(
    slots: dict[str, Any],
    *,
    client: OpenAI | None = None,
) -> dict[str, Any]:
    """Sesión nueva de DeepSeek con búsqueda web. No reutiliza el chat con el cliente."""
    debug = llm_debug_active()
    entrada = build_estimator_input(slots)
    request: dict[str, Any] = {
        "slots": slots,
        "entrada": entrada,
        "modelo": settings.llm_estimator_model,
        "busqueda": "web_search",
    }
    logged: dict[str, Any] | None = None

    def finish(result: dict[str, Any], **extra: Any) -> dict[str, Any]:
        nonlocal logged
        if debug:
            if extra.get("cache"):
                request["cache"] = True
            logged = {
                "texto": extra.get("texto"),
                "llamo_modelo": bool(extra.get("llamo_modelo")),
                "resultado": result,
            }
        return result

    try:
        return _estimate_repair(slots, client=client, entrada=entrada, finish=finish)
    except Exception as exc:
        if debug:
            logged = {
                "texto": None,
                "llamo_modelo": False,
                "resultado": {
                    "status": "error",
                    "error": type(exc).__name__,
                    "mensaje": str(exc),
                },
            }
        raise
    finally:
        if debug:
            log_quoter(
                request,
                logged or {"texto": None, "llamo_modelo": False, "resultado": None},
            )


def _estimate_repair(
    slots: dict[str, Any],
    *,
    client: OpenAI | None,
    entrada: str,
    finish: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    cache_key = estimate_cache_key(slots)
    cached = _cache_get(cache_key)
    if cached is not None:
        logger.info("Estimación mecánica servida desde caché: %s", cache_key)
        quote = assemble_quote(slots, cached)
        if quote.get("status") == "ok":
            return finish(quote, cache=True)

    llm = client or build_client()
    kwargs: dict[str, Any] = {
        "model": settings.llm_estimator_model,
        "instructions": load_estimator_prompt(),
        "input": entrada,
        "tools": [WEB_SEARCH_TOOL],
        "tool_choice": WEB_SEARCH_TOOL,
        "temperature": ESTIMATOR_TEMPERATURE,
        "timeout": SEARCH_TIMEOUT_SECONDS,
    }
    if settings.llm_disable_thinking:
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    response = llm.responses.create(**kwargs)
    text = _response_text(response)
    if not text:
        return finish(
            {"status": "error", "error": "estimacion_vacia"},
            llamo_modelo=True,
            texto="",
        )
    try:
        payload = parse_estimate_payload(text)
    except (ValueError, json.JSONDecodeError):
        logger.warning("Estimación mecánica ilegible")
        return finish(
            {"status": "error", "error": "estimacion_invalida"},
            llamo_modelo=True,
            texto=text,
        )
    if payload.get("sin_precio") is True:
        motivo = " ".join(str(payload.get("motivo") or "").split())
        return finish(
            {
                "status": "sin_precio",
                "motivo": motivo or "No encontré un precio publicado para este auto y este trabajo.",
            },
            llamo_modelo=True,
            texto=text,
        )
    if payload.get("requiere_revision") is True:
        motivo = " ".join(str(payload.get("motivo") or "").split())
        return finish(
            _revision(motivo or "El problema necesita revisión del auto antes de estimar."),
            llamo_modelo=True,
            texto=text,
        )
    if payload.get("necesita_detalle") is True:
        return finish(
            _detail_request(slots, payload.get("faltan"), payload.get("motivo")),
            llamo_modelo=True,
            texto=text,
        )
    quote = assemble_quote(slots, payload)
    if quote.get("status") == "ok":
        _cache_put(cache_key, payload)
    return finish(quote, llamo_modelo=True, texto=text)


def estimate_cache_key(slots: dict[str, Any]) -> str:
    """Mismo vehículo + mismo trabajo => misma clave, sin importar mayúsculas, tildes ni espacios."""
    keys = tuple(key for key in SLOT_KEYS if key != "valor_hora_mano_de_obra")
    return "|".join(_normalize_text(slots.get(key)) for key in keys)


def clear_estimate_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^a-z0-9.]+", " ", text).split())


def _cache_get(key: str) -> dict[str, Any] | None:
    ttl = float(settings.estimate_cache_ttl_hours or 0) * 3600
    if ttl <= 0:
        return None
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry is None:
            return None
        if time.monotonic() - entry["at"] > ttl:
            del _CACHE[key]
            return None
        return copy.deepcopy(entry["payload"])


def _cache_put(key: str, payload: dict[str, Any]) -> None:
    if float(settings.estimate_cache_ttl_hours or 0) <= 0:
        return
    with _CACHE_LOCK:
        _CACHE[key] = {
            "at": time.monotonic(),
            "payload": copy.deepcopy(payload),
        }


def _revision(motivo: str) -> dict[str, Any]:
    text = " ".join(str(motivo or "").split())
    return {
        "status": "requiere_revision",
        "motivo": text[:240] or "El problema necesita revisión del auto antes de estimar.",
    }


def _detail_request(slots: dict[str, Any], faltan: Any, motivo: Any) -> dict[str, Any]:
    requested = [str(item).strip() for item in faltan] if isinstance(faltan, list) else []
    if isinstance(faltan, str) and faltan.strip():
        requested = [faltan.strip()]
    missing: list[str] = []
    for key in requested:
        if key not in _DETAIL_KEYS or key in missing:
            continue
        if key != "descripcion_problema" and str(slots.get(key) or "").strip():
            continue
        missing.append(key)
    missing = missing[:3]
    if not missing:
        return {"status": "error", "error": "busqueda_vacia"}
    text = " ".join(str(motivo or "").split())
    return _need_slots(missing, text[:240] or _detail_motivo(missing))


def _need_slots(missing: list[str], motivo: str) -> dict[str, Any]:
    return {"status": "need_slots", "missing": missing, "motivo": motivo}


def _detail_motivo(missing: list[str]) -> str:
    parts = [_DETAIL_LABELS[key] for key in missing]
    if len(parts) == 1:
        pedido = parts[0]
    else:
        pedido = f"{', '.join(parts[:-1])} y {parts[-1]}"
    return f"No encontré precios con estos datos. Para afinar hacen falta {pedido}."


def _snapshot(slots: dict[str, Any], rate: int) -> dict[str, Any]:
    saved: dict[str, Any] = {}
    for key in SLOT_KEYS:
        value = slots.get(key)
        if value is None or value == "":
            continue
        saved[key] = value
    saved["valor_hora_mano_de_obra"] = rate
    return saved


def _first(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


def _detail_text(payload: dict[str, Any]) -> str:
    raw = payload.get("detalle_repuestos")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    alt = payload.get("repuestos")
    if isinstance(alt, str) and alt.strip():
        return alt.strip()
    return ""


def _parts_from_items(
    payload: dict[str, Any],
) -> tuple[tuple[float, float], str, list[dict[str, Any]]] | None:
    """Repuestos por ítem: el modelo da cantidad y precio unitario; el sistema multiplica y suma."""
    raw = payload.get("repuestos_items")
    if not isinstance(raw, list) or not raw:
        return None
    low_total = 0.0
    high_total = 0.0
    labels: list[str] = []
    items: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            return None
        concept = str(row.get("concepto") or "").strip()
        quantity = _positive_number(row.get("cantidad"))
        price = _span(
            row,
            ("precio_unitario_ars", "precio_unitario"),
            ("precio_unitario_ars_min",),
            ("precio_unitario_ars_max",),
        )
        if not concept or quantity is None or quantity > 1000 or price is None or price[0] < 0:
            return None
        unit = str(row.get("unidad") or "").strip()
        low_total += quantity * price[0]
        high_total += quantity * price[1]
        measure = "" if unit.lower() in ("", "u", "un", "unidad", "unidades") else f" {unit}"
        labels.append(f"{concept}: {_hours_label(quantity)}{measure} × {_money_span(*map(_pesos, price))}")
        items.append(
            {
                "concepto": concept,
                "cantidad": quantity,
                "unidad": unit,
                "precio_unitario_ars_min": _pesos(price[0]),
                "precio_unitario_ars_max": _pesos(price[1]),
            }
        )
    return (low_total, high_total), ", ".join(labels), items


def _span(
    payload: dict[str, Any],
    single_keys: tuple[str, ...],
    min_keys: tuple[str, ...],
    max_keys: tuple[str, ...],
) -> tuple[float, float] | None:
    low = _number(_first(payload, *min_keys))
    high = _number(_first(payload, *max_keys))
    single = _number(_first(payload, *single_keys))
    if low is None and high is None:
        if single is None:
            return None
        return single, single
    if low is None:
        low = single if single is not None else high
    if high is None:
        high = single if single is not None else low
    if low is None or high is None:
        return None
    if low > high:
        low, high = high, low
    return low, high


def _positive_number(value: Any) -> float | None:
    number = _number(value)
    if number is None or number <= 0:
        return None
    return number


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        if value != value:
            return None
        return float(value)
    text = str(value).strip().replace("$", "").replace(" ", "")
    if not text:
        return None
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", text):
        text = text.replace(".", "")
    elif "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _pesos(value: float) -> int:
    return int(value + 0.5)


def _money(amount: int) -> str:
    return "$" + f"{amount:,}".replace(",", ".")


def _money_span(low: int, high: int) -> str:
    if low == high:
        return _money(low)
    return f"entre {_money(low)} y {_money(high)}"


def _hours_span(low: float, high: float) -> str:
    left = _hours_label(low)
    right = _hours_label(high)
    if left == right:
        return f"{left} h"
    return f"entre {left} h y {right} h"


def _hours_label(hours: float) -> str:
    text = f"{hours:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def _response_text(response: Any) -> str:
    text = getattr(response, "output_text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()
    chunks: list[str] = []
    for item in getattr(response, "output", None) or []:
        content = item.get("content") if isinstance(item, dict) else getattr(item, "content", None)
        for part in content or []:
            piece = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
            if isinstance(piece, str):
                chunks.append(piece)
    return "\n".join(chunks).strip()
