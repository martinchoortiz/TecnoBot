from collections.abc import Callable
from typing import Any

from app.conversation.llm_debug import log_quoter
from app.skills.base import Skill
from app.skills.mechanical_estimator import SLOT_KEYS, estimate_repair, resolve_hourly_rate

MECHANICAL_SLOTS = SLOT_KEYS
ASK_SLOTS = ("marca", "modelo", "anio", "descripcion_problema")


class MechanicalServiceSkill(Skill):
    name = "atencion_mecanica"
    description = "Estimación de arreglo mecánico o cambio de pieza, sujeta a verificación en el taller"

    def __init__(self, estimator: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> None:
        self._estimator = estimator or estimate_repair

    def missing_slots(self, slots: dict[str, Any], catalog_hint: dict[str, Any] | None = None) -> list[str]:
        missing: list[str] = []
        for key in ASK_SLOTS:
            if not str(slots.get(key) or "").strip():
                missing.append(key)
        return missing

    def calculate_quote(self, slots: dict[str, Any]) -> dict[str, Any]:
        missing = self.missing_slots(slots)
        if missing:
            result = {"status": "need_slots", "missing": missing}
            _log_quoter_without_model(slots, result)
            return result
        rate = resolve_hourly_rate(slots)
        if rate is None:
            result = {"status": "error", "error": "falta_valor_hora"}
            _log_quoter_without_model(slots, result)
            return result
        prepared = dict(slots)
        prepared["valor_hora_mano_de_obra"] = rate
        return self._estimator(prepared)


def _log_quoter_without_model(slots: dict[str, Any], result: dict[str, Any]) -> None:
    log_quoter(
        {
            "slots": slots,
            "consultas": [],
            "resultados": [],
            "entrada": None,
            "cache": False,
        },
        {"texto": None, "llamo_modelo": False, "resultado": result},
    )
