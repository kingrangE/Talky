"""Retry ordered Graph projections from the PostgreSQL transaction outbox."""
import logging
import signal
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import aliased

from app.db.models import Conversation, GraphSyncEvent, Message
from app.db.postgres import session_scope
from app.graph_db.ingest import delete_conversation_memory, ingest_topics, ingest_turn

log = logging.getLogger(__name__)


def process_one() -> bool:
    now = datetime.now(timezone.utc)
    older = aliased(GraphSyncEvent)
    with session_scope() as session:
        predecessor = select(older.id).where(
            older.conversation_id == GraphSyncEvent.conversation_id,
            or_(older.created_at < GraphSyncEvent.created_at,
                and_(older.created_at == GraphSyncEvent.created_at, older.id < GraphSyncEvent.id)),
        ).exists()
        event = session.scalars(select(GraphSyncEvent).where(
            GraphSyncEvent.available_at <= now, ~predecessor,
        ).order_by(GraphSyncEvent.created_at, GraphSyncEvent.id)
          .with_for_update(skip_locked=True).limit(1)).first()
        if event is None:
            return False
        try:
            # Serialize with source deletion; a delayed upsert must never resurrect deleted data.
            conversation = session.scalars(select(Conversation).where(
                Conversation.id == event.conversation_id, Conversation.user_id == event.user_id,
            ).with_for_update()).first()
            if conversation is None or event.kind == 'delete':
                ok = delete_conversation_memory(conversation_id=str(event.conversation_id), user_id=str(event.user_id))
            elif event.kind == 'topics':
                ok = ingest_topics(conversation_id=str(event.conversation_id), topics=event.payload['topics'], event_id=str(event.id))
            else:
                payload = event.payload
                user = session.get(Message, payload['user_message_id'])
                assistant = session.get(Message, payload['assistant_message_id'])
                if not user or not assistant or user.conversation_id != event.conversation_id or assistant.conversation_id != event.conversation_id:
                    raise ValueError('Outbox message ownership mismatch')
                ok = ingest_turn(conversation_id=str(event.conversation_id), user_id=str(event.user_id),
                    user_msg_id=str(user.id), user_text=user.content, language=user.language,
                    assistant_msg_id=str(assistant.id), ai_text=assistant.content,
                    english_expression=user.english_expression, better_expression=assistant.better_expression,
                    previous_message_id=payload.get('previous_message_id'),
                    user_created_at=user.created_at, assistant_created_at=assistant.created_at)
                if ok and user.memory_topics:
                    ok = ingest_topics(conversation_id=str(event.conversation_id), topics=user.memory_topics, event_id=str(event.id))
            if not ok:
                raise RuntimeError('Graph projection unavailable')
        except Exception as exc:
            event.attempts += 1
            event.last_error = type(exc).__name__
            event.available_at = now + timedelta(seconds=min(300, 2 ** min(event.attempts, 8)))
            log.warning('Graph synchronization deferred (%s)', event.last_error)
        else:
            session.delete(event)
    return True


def main():
    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    while not stop.is_set():
        try:
            worked = process_one()
        except Exception:
            log.exception('Graph worker retrying after database failure')
            worked = False
        if not worked:
            stop.wait(1)


if __name__ == '__main__':
    main()
