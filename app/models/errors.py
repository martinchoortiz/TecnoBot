from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, Column, Text
from sqlmodel import Field, SQLModel


class InternalError(SQLModel, table=True):
    """Fallos internos (LLM, Kapso, orchestrator). No se muestran al cliente."""

    __tablename__ = "internal_error"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)
    source: str = Field(index=True)
    error_type: str = Field(index=True)
    http_status: int | None = Field(default=None, index=True)
    message: str = Field(sa_column=Column(Text, nullable=False))
    details: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    conversation_id: str | None = Field(default=None, index=True)
    channel_ref: str | None = Field(default=None, index=True)
