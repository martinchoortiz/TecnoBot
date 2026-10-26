from sqlmodel import Session, select

from app.db.session import init_db, make_engine
from app.models.conversation import Conversation, Message, Quote
from app.models.errors import InternalError
from scripts.copy_sqlite_to_postgres import copy_database


def test_copy_database_inserts_once_and_skips_existing_channel(tmp_path) -> None:
    source_url = f"sqlite:///{tmp_path / 'source.db'}"
    destination_url = f"sqlite:///{tmp_path / 'dest.db'}"
    source = make_engine(source_url)
    destination = make_engine(destination_url)
    init_db(source)
    init_db(destination)
    with Session(source) as session:
        session.add(Conversation(id="c1", channel_ref="whatsapp-1", phone="264000", state="active"))
        session.add(Message(id="m1", conversation_id="c1", role="user", content="hola"))
        session.add(Quote(id="q1", conversation_id="c1", skill="bateria", price=10, detail="una"))
        session.add(InternalError(id="e1", source="webhook", error_type="Boom", message="fallo"))
        session.commit()
    with Session(destination) as session:
        session.add(Conversation(id="live", channel_ref="whatsapp-2", phone="264111", state="active"))
        session.commit()

    first = copy_database(source_url, destination_url)
    second = copy_database(source_url, destination_url)

    assert first == {"conversation": 1, "message": 1, "quote": 1, "internal_error": 1}
    assert second == {"conversation": 0, "message": 0, "quote": 0, "internal_error": 0}
    with Session(destination) as session:
        refs = set(session.exec(select(Conversation.channel_ref)).all())
        assert refs == {"whatsapp-1", "whatsapp-2"}
        assert session.get(Message, "m1").content == "hola"

    with Session(source) as session:
        session.add(Conversation(id="other", channel_ref="whatsapp-2", phone="264222", state="quoted"))
        session.add(Message(id="m2", conversation_id="other", role="user", content="no copiar"))
        session.commit()
    third = copy_database(source_url, destination_url)
    assert third["conversation"] == 0
    assert third["message"] == 0
    with Session(destination) as session:
        assert session.get(Conversation, "live").phone == "264111"
        assert session.get(Message, "m2") is None

    source.dispose()
    destination.dispose()
