from app.channel.base import ChannelPort, IncomingMessage


class FakeChannel(ChannelPort):
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str | None]] = []
        self.handoffs: list[tuple[str, str]] = []

    def parse_incoming(self, raw_payload: dict) -> list[IncomingMessage]:
        return []

    async def send_text(self, conversation_ref: str, text: str, to: str | None = None) -> None:
        self.sent.append((conversation_ref, text, to))

    async def handoff_to_human(self, conversation_ref: str, reason: str) -> None:
        self.handoffs.append((conversation_ref, reason))
