from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class IncomingMessage:
    conversation_ref: str
    text: str
    message_id: str
    from_phone: str | None = None
    from_user_id: str | None = None


class ChannelPort(ABC):
    """Contrato del canal. El orchestrator no conoce Kapso."""

    @abstractmethod
    def parse_incoming(self, raw_payload: dict) -> list[IncomingMessage]:
        """Normaliza un webhook (uno o varios mensajes)."""

    @abstractmethod
    async def send_text(self, conversation_ref: str, text: str, to: str | None = None) -> None:
        """Envía texto al hilo. `to` es el teléfono si el canal lo necesita."""

    @abstractmethod
    async def handoff_to_human(
        self,
        conversation_ref: str,
        reason: str,
        *,
        phone: str | None = None,
    ) -> None:
        """Pasa el hilo a un empleado. El bot deja de auto-responder."""
