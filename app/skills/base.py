from abc import ABC, abstractmethod
from typing import Any


class Skill(ABC):
    name: str
    description: str

    @abstractmethod
    def missing_slots(self, slots: dict[str, Any], catalog_hint: dict[str, Any] | None = None) -> list[str]:
        """Slots que todavía hacen falta para un quote unívoco."""

    @abstractmethod
    def calculate_quote(self, slots: dict[str, Any]) -> dict[str, Any]:
        """Precio y detalle. Nunca lo inventa el LLM."""
