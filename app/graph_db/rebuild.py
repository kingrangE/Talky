"""Requeue canonical history into a verified empty replacement Graph database."""
import argparse
from sqlalchemy import select
from app.db.models import Conversation, GraphSyncEvent, Message, Report
from app.db.postgres import session_scope
from app.graph_db.neo4j_client import get_driver


def enqueue(conversation_id):
    with session_scope() as session:
        conv = session.scalars(select(Conversation).where(Conversation.id == conversation_id).with_for_update()).first()
        if conv is None:
            return 0
        pending = session.scalars(select(GraphSyncEvent).where(GraphSyncEvent.conversation_id == conv.id)).all()
        queued_messages = {e.payload.get('user_message_id') for e in pending if e.kind == 'upsert'}
        messages = session.scalars(select(Message).where(Message.conversation_id == conv.id)
            .order_by(Message.created_at, Message.id)).all()
        if len(messages) % 2 or any(m.role != ('user' if i % 2 == 0 else 'assistant') for i,m in enumerate(messages)):
            raise ValueError('source message order needs review before Graph rebuild')
        count = 0
        previous = None
        for user, assistant in zip(messages[::2], messages[1::2]):
            if str(user.id) not in queued_messages:
                session.add(GraphSyncEvent(conversation_id=conv.id, user_id=conv.user_id, kind='upsert',
                    created_at=user.created_at, payload={'user_message_id':str(user.id),
                    'assistant_message_id':str(assistant.id), 'previous_message_id':previous,
                    'topics':user.memory_topics or []}))
                count += 1
            previous = str(assistant.id)
        report = session.scalars(select(Report).where(Report.conversation_id == conv.id)).first()
        if report and report.memory_topics and not any(e.kind=='topics' and e.payload.get('topics')==report.memory_topics for e in pending):
            session.add(GraphSyncEvent(conversation_id=conv.id, user_id=conv.user_id, kind='topics',
                created_at=report.created_at, payload={'topics':report.memory_topics}))
            count += 1
        return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm-empty-target', action='store_true')
    args = parser.parse_args()
    if not args.confirm_empty_target:
        parser.error('stop writers and workers; verify the replacement URI, then pass --confirm-empty-target')
    driver = get_driver()
    with driver.session() as graph:
        if graph.run('MATCH (n) RETURN count(n) AS count').single()['count']:
            raise ValueError('replacement Graph database must be empty')
    cursor = None
    total = 0
    while True:
        with session_scope() as session:
            query = select(Conversation.id).order_by(Conversation.id).limit(100)
            if cursor is not None:
                query = query.where(Conversation.id > cursor)
            ids = session.scalars(query).all()
        if not ids:
            break
        for cid in ids:
            total += enqueue(cid)
        cursor = ids[-1]
    print(f'Queued {total} source projections. Start Graph workers and verify backlog before reopening writes.')


if __name__ == '__main__':
    main()
