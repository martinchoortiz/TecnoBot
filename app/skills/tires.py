from app.skills.base import Skill


class TiresSkill(Skill):
    name = "neumaticos"
    description = "Presupuesto de compra/colocación o chequeo de neumáticos (futuro)"

    def missing_slots(self, slots: dict, catalog_hint: dict | None = None) -> list[str]:
        return []

    def calculate_quote(self, slots: dict) -> dict:
        raise NotImplementedError("Etapa 2")
