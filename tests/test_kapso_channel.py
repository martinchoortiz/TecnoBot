from app.channel.kapso_channel import KapsoChannel, messages_url, verify_kapso_signature


def test_messages_url_includes_graph_version() -> None:
    assert messages_url("https://api.kapso.ai", "1187499001119197") == (
        "https://api.kapso.ai/meta/whatsapp/v24.0/1187499001119197/messages"
    )


def test_verify_signature() -> None:
    body = b'{"message":{"text":{"body":"hola"}}}'
    secret = "wh_test"
    import hashlib
    import hmac

    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_kapso_signature(body, sig, secret)
    assert not verify_kapso_signature(body, "deadbeef", secret)


def test_parse_v2_text() -> None:
    channel = KapsoChannel()
    payload = {
        "message": {
            "id": "wamid.1",
            "type": "text",
            "from": "5491112345678",
            "text": {"body": "se me murió la batería"},
            "kapso": {"direction": "inbound", "content": "se me murió la batería"},
        },
        "conversation": {
            "id": "conv_abc",
            "phone_number": "5491112345678",
            "business_scoped_user_id": "US.1",
        },
        "phone_number_id": "123",
    }
    msgs = channel.parse_incoming(payload)
    assert len(msgs) == 1
    assert msgs[0].conversation_ref == "conv_abc"
    assert msgs[0].text == "se me murió la batería"
    assert msgs[0].from_phone == "5491112345678"


def test_parse_batch_and_skip_empty() -> None:
    channel = KapsoChannel()
    payload = {
        "batch": True,
        "data": [
            {
                "message": {
                    "id": "wamid.2",
                    "text": {"body": "hola"},
                    "kapso": {"direction": "inbound"},
                },
                "conversation": {"id": "conv_1", "phone_number": "1"},
            },
            {
                "message": {"id": "wamid.3", "type": "image", "kapso": {"direction": "inbound"}},
                "conversation": {"id": "conv_1"},
            },
        ],
    }
    msgs = channel.parse_incoming(payload)
    assert [m.text for m in msgs] == ["hola"]
