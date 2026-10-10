from typing import Any

from app.skills.base import Skill
from app.skills.battery_catalog import lookup

VEHICLE_SLOTS = (
    "marca",
    "modelo",
    "anio",
    "generacion",
    "trim",
    "motor",
    "transmision",
    "linea",
    "bateria",
)


class BatteryChangeSkill(Skill):
    name = "cambio_bateria"
    description = "Presupuesto de cambio de batería (taller o domicilio) y luego Coordinar Turno"

    def missing_slots(self, slots: dict[str, Any], catalog_hint: dict[str, Any] | None = None) -> list[str]:
        missing: list[str] = []
        for key in ("marca", "modelo", "anio"):
            if not slots.get(key):
                missing.append(key)
        if missing:
            return missing
        if catalog_hint and catalog_hint.get("discriminators"):
            return [key for key in catalog_hint["discriminators"] if not slots.get(key)]
        result = lookup(slots)
        if result.get("status") == "need_slots":
            return list(result.get("missing") or [])
        return []

    def calculate_quote(self, slots: dict[str, Any]) -> dict[str, Any]:
        result = lookup(slots)
        if result.get("status") != "ok":
            return result
        quote = {
            "status": "ok",
            "price": result["price"],
            "detail": result.get("detail"),
            "bateria": result.get("bateria"),
            "linea": result.get("linea"),
            "slots": {k: slots.get(k) for k in VEHICLE_SLOTS if slots.get(k)},
        }
        if result.get("multiple"):
            quote["multiple"] = True
            quote["opciones"] = result["opciones"]
        return quote
