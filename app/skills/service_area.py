"""Zona de atención de Tecnocar (Gran San Juan) para cambio de batería."""

from __future__ import annotations

import unicodedata
from typing import Any

# Nombre a mostrar -> alias aceptados (normalizados).
SERVICE_AREA_DEPARTMENTS: dict[str, tuple[str, ...]] = {
    "San Juan Capital": ("capital", "san juan", "san juan capital", "ciudad de san juan", "ciudad"),
    "Rivadavia": ("rivadavia",),
    "Rawson": ("rawson",),
    "Chimbas": ("chimbas",),
    "Santa Lucía": ("santa lucia",),
}

SERVICE_AREA_LABEL = "Gran San Juan"


def _normalize(text: str) -> str:
    stripped = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    return " ".join(stripped.lower().replace(".", " ").replace(",", " ").split())


def match_department(value: Any) -> str | None:
    """Devuelve el departamento atendido que corresponde a `value`, o None."""
    if not value:
        return None
    normalized = _normalize(str(value))
    # "Departamento Rawson", "Dpto. de Chimbas", "Rawson, San Juan"...
    for prefix in ("departamento de ", "departamento ", "dpto de ", "dpto "):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
            break
    candidates = [normalized]
    # "Rawson, San Juan" -> "rawson" (la coma ya se normalizó a espacio)
    if normalized.endswith(" san juan"):
        candidates.append(normalized[: -len(" san juan")].strip())
    for candidate in candidates:
        for name, aliases in SERVICE_AREA_DEPARTMENTS.items():
            if candidate in aliases:
                return name
    return None


def service_area_names() -> list[str]:
    return list(SERVICE_AREA_DEPARTMENTS)


def check_service_address(direccion: Any, departamento: Any) -> dict[str, Any]:
    """Valida la dirección del cliente contra la zona de atención.

    Devuelve `ok` (con `departamento` normalizado), `need_slots` (falta dato)
    o `fuera_de_zona` (el departamento no está en la zona de atención).
    """
    missing = []
    if not str(direccion or "").strip():
        missing.append("direccion")
    if not str(departamento or "").strip():
        missing.append("departamento")
    if missing:
        return {
            "status": "need_slots",
            "missing": missing,
            "zona_cobertura": service_area_names(),
            "motivo": "Hace falta la dirección y el departamento (o localidad) para confirmar que está en el Gran San Juan.",
        }
    matched = match_department(departamento)
    if matched is None:
        return {
            "status": "fuera_de_zona",
            "localidad": str(departamento).strip(),
            "zona_cobertura": service_area_names(),
            "alternativa": "taller",
        }
    return {"status": "ok", "departamento": matched}
