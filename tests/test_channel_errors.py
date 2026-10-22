import httpx
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.channel.delivery import record_delivery_failures
from app.channel.errors import ExternalServiceError
from app.channel.http import raise_for_external_response
from app.channel.kapso_channel import KapsoChannel
from app.config import settings
from app.models.errors import InternalError


def test_http_error_becomes_external_service_error() -> None:
    request = httpx.Request("POST", "https://api.kapso.ai/messages")
    response = httpx.Response(403, json={"error": "blocked"}, request=request)
    with pytest.raises(ExternalServiceError) as caught:
        raise_for_external_response("kapso", response, "send_text")
    assert caught.value.status_code == 403
    assert caught.value.provider == "kapso"
    assert "blocked" in str(caught.value)


def test_200_with_error_object_is_failure() -> None:
    request = httpx.Request("POST", "https://api.kapso.ai/messages")
    response = httpx.Response(
        200,
        json={"error": {"message": "This +1 555 number requires display name verification"}},
        request=request,
    )
    with pytest.raises(ExternalServiceError) as caught:
        raise_for_external_response("kapso", response, "send_text")
    assert "display name" in str(caught.value)


@pytest.mark.asyncio
async def test_send_text_without_destination_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "kapso_api_key", "k")
    monkeypatch.setattr(settings, "kapso_phone_number_id", "1")
    with pytest.raises(ExternalServiceError) as caught:
        await KapsoChannel().send_text("conv", "hola", to=None)
    assert "teléfono" in str(caught.value)


def test_delivery_failed_webhook_is_persisted() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    payload = {
        "message": {
            "id": "wamid.789",
            "kapso": {
                "direction": "outbound",
                "status": "failed",
                "statuses": [
                    {
                        "status": "failed",
                        "errors": [
                            {
                                "code": 131047,
                                "title": "Re-engagement message",
                                "message": "More than 24 hours have passed",
                            }
                        ],
                    }
                ],
            },
        },
        "conversation": {"id": "conv_123"},
    }
    with Session(engine) as session:
        assert record_delivery_failures(session, "whatsapp.message.failed", payload) is True
        row = session.exec(select(InternalError)).one()
        assert row.source == "channel"
        assert row.error_type == "KapsoDeliveryFailed"
        assert row.channel_ref == "conv_123"
        assert "24 hours" in row.message
