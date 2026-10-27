from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class LlmLog(SQLModel, table=True):
    """Decisiones del chat y el ida y vuelta con el cotizador. Solo teléfonos de debug."""

    __tablename__ = "llm_logs"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)
    conversation_id: str | None = Field(default=None, index=True)
    phone: str | None = Field(default=None, index=True)
    seq: int = Field(default=0, index=True)
    kind: str = Field(index=True)
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
