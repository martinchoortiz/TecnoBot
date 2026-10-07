from __future__ import annotations

from typing import Any


class ExternalServiceError(Exception):
    """Corte o rechazo al llamar un proveedor externo (Kapso, etc.)."""

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        status_code: int | None = None,
        body: Any = None,
        url: str | None = None,
    ) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.status_code = status_code
        self.body = body
        self.url = url
