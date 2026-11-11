import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from sqlalchemy import event, func, select

from app.db import repo
from app.db.models import Conversation, GraphSyncEvent, Message, User
from app.graph_db import worker


@pytest.fixture
def conversation(pg_db):
    _, scope = pg_db
    with scope() as session:
        user = User(display_name='outbox-test')
        session.add(user)
        session.flush()
        conv = Conversation(user_id=user.id)
        session.add(conv)
        session.flush()
        return str(user.id), str(conv.id)


def test_outbox_failure_rolls_back_the_source_turn(conversation, pg_db):
    uid, cid = conversation
    def fail(*_): raise RuntimeError('injected outbox failure')
    event.listen(GraphSyncEvent, 'before_insert', fail)
    try:
        with pytest.raises(RuntimeError, match='injected'):
            repo.save_turn(cid,uid,'hello','reply')
    finally:
        event.remove(GraphSyncEvent,'before_insert',fail)
    with pg_db[1]() as session:
        assert session.scalar(select(func.count()).select_from(Message)) == 0
        assert session.scalar(select(func.count()).select_from(GraphSyncEvent)) == 0


def test_graph_failure_retains_work_then_retries_without_new_messages(conversation, pg_db, monkeypatch):
    uid,cid=conversation
    ids=repo.save_turn(cid,uid,'I like reading.','Good to know.',memory_topics=['books'])
    calls=[]
    monkeypatch.setattr(worker,'ingest_turn',lambda **kw: calls.append(kw) or False)
    assert worker.process_one()
    with pg_db[1]() as session:
        job=session.scalars(select(GraphSyncEvent)).one()
        assert job.attempts==1
        job.available_at=datetime.now(timezone.utc)
        assert session.scalar(select(func.count()).select_from(Message))==2
    monkeypatch.setattr(worker,'ingest_turn',lambda **kw: calls.append(kw) or True)
    monkeypatch.setattr(worker,'ingest_topics',lambda **kw: kw['topics']==['books'] and bool(kw['event_id']))
    assert worker.process_one()
    assert calls[0]['user_msg_id']==calls[1]['user_msg_id']==ids[0]
    assert calls[0]['user_created_at']==calls[1]['user_created_at']
    with pg_db[1]() as session:
        assert session.scalar(select(func.count()).select_from(GraphSyncEvent))==0
        assert session.scalar(select(func.count()).select_from(Message))==2


def test_delayed_upsert_cannot_resurrect_deleted_conversation(conversation, pg_db, monkeypatch):
    uid,cid=conversation
    repo.save_turn(cid,uid,'private','reply')
    repo.delete_conversation(cid,uid)
    assert repo.active_conversation_ids(uid,[cid])==set()
    monkeypatch.setattr(worker,'ingest_turn',lambda **_: pytest.fail('deleted source was projected'))
    deletions=[]
    monkeypatch.setattr(worker,'delete_conversation_memory',lambda **kw: deletions.append(kw) or True)
    assert worker.process_one()
    assert worker.process_one()
    assert len(deletions)==2
    assert not worker.process_one()
    assert all(d=={'conversation_id':cid,'user_id':uid} for d in deletions)


def test_parallel_workers_do_not_reorder_a_conversations_events(conversation, pg_db, monkeypatch):
    uid,cid=conversation
    first=repo.save_turn(cid,uid,'first','one')
    second=repo.save_turn(cid,uid,'second','two')
    projected=[]
    def project(**kw):
        time.sleep(.1)
        projected.append(kw)
        return True
    monkeypatch.setattr(worker,'ingest_turn',project)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _: worker.process_one(),range(2)))
    assert sorted(results)==[False,True]
    assert worker.process_one()
    assert [p['user_msg_id'] for p in projected]==[first[0],second[0]]
    assert projected[1]['previous_message_id']==first[1]
    assert repo.active_conversation_ids(str(uuid.uuid4()),[cid])==set()


def test_rebuild_restores_old_history_before_pending_newer_events(conversation, pg_db, monkeypatch):
    from app.graph_db.rebuild import enqueue
    uid, cid = conversation
    first = repo.save_turn(cid, uid, 'older source', 'older reply', memory_topics=['books'])
    with pg_db[1]() as session:
        old = session.scalars(select(GraphSyncEvent)).one()
        session.delete(old)
    second = repo.save_turn(cid, uid, 'newer source', 'newer reply')
    assert enqueue(uuid.UUID(cid)) == 1
    assert enqueue(uuid.UUID(cid)) == 0
    projected = []
    monkeypatch.setattr(worker, 'ingest_turn', lambda **kw: projected.append(kw) or True)
    monkeypatch.setattr(worker, 'ingest_topics', lambda **_: True)
    assert worker.process_one() and worker.process_one()
    assert [p['user_msg_id'] for p in projected] == [first[0], second[0]]
    assert projected[0]['previous_message_id'] is None
    assert projected[1]['previous_message_id'] == first[1]
