from app.skills.base import Skill


class MechanicalServiceSkill(Skill):
    name = "atencion_mecanica"
    description = "Presupuesto de atención mecánica (futuro)"

    def missing_slots(self, slots: dict, catalog_hint: dict | None = None) -> list[str]:
        return []

    def calculate_quote(self, slots: dict) -> dict:
        raise NotImplementedError("Etapa 3")
