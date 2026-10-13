from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, select

from app.channel.base import IncomingMessage
from app.models.conversation import Conversation, ConversationState, Message, Quote


class ConversationStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, conversation_id: str) -> Conversation | None:
        return self.session.get(Conversation, conversation_id)

    def get_or_create(self, incoming: IncomingMessage) -> Conversation:
        conv = self.session.exec(
            select(Conversation).where(Conversation.channel_ref == incoming.conversation_ref)
        ).first()
        if conv:
            if incoming.from_phone and conv.phone != incoming.from_phone:
                conv.phone = incoming.from_phone
            if incoming.from_user_id and conv.user_id != incoming.from_user_id:
                conv.user_id = incoming.from_user_id
            conv.updated_at = datetime.now(UTC)
            self.session.add(conv)
            self.session.commit()
            self.session.refresh(conv)
            return conv
        conv = Conversation(
            channel_ref=incoming.conversation_ref,
            phone=incoming.from_phone,
            user_id=incoming.from_user_id,
            state=ConversationState.ACTIVE.value,
        )
        self.session.add(conv)
        self.session.commit()
        self.session.refresh(conv)
        return conv

    def already_logged(self, external_id: str | None) -> bool:
        if not external_id:
            return False
        existing = self.session.exec(select(Message).where(Message.external_id == external_id)).first()
        return existing is not None

    def add_message(self, conversation_id: str, role: str, content: str, external_id: str | None = None) -> Message:
        msg = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            external_id=external_id,
        )
        self.session.add(msg)
        conv = self.session.get(Conversation, conversation_id)
        if conv:
            conv.updated_at = datetime.now(UTC)
            self.session.add(conv)
        self.session.commit()
        self.session.refresh(msg)
        return msg

    def merge_vehicle_slots(self, conversation_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        conv = self.session.get(Conversation, conversation_id)
        if not conv:
            return {}
        slots = dict(conv.vehicle_slots or {})
        for key, value in updates.items():
            if value is None or value == "":
                continue
            slots[key] = value
        conv.vehicle_slots = slots
        conv.updated_at = datetime.now(UTC)
        flag_modified(conv, "vehicle_slots")
        self.session.add(conv)
        self.session.commit()
        self.session.refresh(conv)
        return dict(conv.vehicle_slots or {})

    def save_quote(
        self,
        conversation_id: str,
        skill: str,
        slots: dict[str, Any],
        price: float | None,
        detail: str | None,
        lugar_colocacion: str | None = None,
    ) -> Quote:
        quote = Quote(
            conversation_id=conversation_id,
            skill=skill,
            slots=slots,
            price=price,
            detail=detail,
            lugar_colocacion=lugar_colocacion,
        )
        self.session.add(quote)
        conv = self.session.get(Conversation, conversation_id)
        if conv:
            conv.state = ConversationState.QUOTED.value
            conv.updated_at = datetime.now(UTC)
            self.session.add(conv)
        self.session.commit()
        self.session.refresh(quote)
        return quote

    def last_quote(self, conversation_id: str) -> Quote | None:
        return self.session.exec(
            select(Quote)
            .where(Quote.conversation_id == conversation_id)
            .order_by(Quote.created_at.desc())
        ).first()

    def set_state(self, conversation_id: str, state: ConversationState) -> None:
        conv = self.session.get(Conversation, conversation_id)
        if not conv:
            return
        conv.state = state.value
        conv.updated_at = datetime.now(UTC)
        self.session.add(conv)
        self.session.commit()

    def set_service_address(self, conversation_id: str, direccion: str, departamento: str) -> None:
        conv = self.session.get(Conversation, conversation_id)
        if not conv:
            return
        slots = dict(conv.vehicle_slots or {})
        slots["direccion"] = direccion
        slots["departamento"] = departamento
        conv.vehicle_slots = slots
        conv.updated_at = datetime.now(UTC)
        flag_modified(conv, "vehicle_slots")
        self.session.add(conv)
        self.session.commit()

    def set_lugar_colocacion(self, conversation_id: str, lugar: str) -> None:
        conv = self.session.get(Conversation, conversation_id)
        if not conv:
            return
        slots = dict(conv.vehicle_slots or {})
        slots["lugar_colocacion"] = lugar
        conv.vehicle_slots = slots
        conv.updated_at = datetime.now(UTC)
        flag_modified(conv, "vehicle_slots")
        self.session.add(conv)
        self.session.commit()

    def recent_messages(self, conversation_id: str, limit: int = 20) -> list[Message]:
        rows = self.session.exec(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        ).all()
        return list(reversed(rows))
