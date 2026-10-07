from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import JSON, Column, Text
from sqlmodel import Field, Relationship, SQLModel


class ConversationState(StrEnum):
    ACTIVE = "active"
    HANDED_OFF = "handed_off"
    QUOTED = "quoted"


class Conversation(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    channel_ref: str = Field(index=True, unique=True)
    phone: str | None = None
    user_id: str | None = None
    state: str = Field(default=ConversationState.ACTIVE.value, index=True)
    vehicle_slots: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    messages: list["Message"] = Relationship(back_populates="conversation")
    quotes: list["Quote"] = Relationship(back_populates="conversation")


class Message(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    conversation_id: str = Field(foreign_key="conversation.id", index=True)
    role: str
    content: str = Field(sa_column=Column(Text, nullable=False))
    external_id: str | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    conversation: Optional[Conversation] = Relationship(back_populates="messages")


class Quote(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    conversation_id: str = Field(foreign_key="conversation.id", index=True)
    skill: str
    slots: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    price: float | None = None
    detail: str | None = None
    lugar_colocacion: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    conversation: Optional[Conversation] = Relationship(back_populates="quotes")
