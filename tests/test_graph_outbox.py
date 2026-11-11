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


def test_slow_projection_does_not_lock_source_conversation(conversation, pg_db, monkeypatch):
    import threading
    uid, cid = conversation
    repo.save_turn(cid, uid, 'first', 'reply')
    entered, release = threading.Event(), threading.Event()
    def slow(**_):
        entered.set()
        assert release.wait(4)
        return True
    monkeypatch.setattr(worker, 'ingest_turn', slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(worker.process_one)
        try:
            assert entered.wait(2)
            start = time.monotonic()
            repo.save_turn(cid, uid, 'second', 'reply')
            assert time.monotonic() - start < 1
            repo.delete_conversation(cid, uid)
        finally:
            release.set()
        assert pending.result()


def test_late_correction_does_not_increment_topic_weights_again(conversation,monkeypatch):
    uid,cid=conversation
    user,assistant=repo.save_turn(cid,uid,'Hello','Reply',memory_topics=['books'])
    repo.save_correction(conversation_id=cid,user_id=uid,user_message_id=user,
        assistant_message_id=assistant,better_expression='A better phrase')
    calls=[]
    monkeypatch.setattr(worker,'ingest_turn',lambda **_:True)
    monkeypatch.setattr(worker,'ingest_topics',lambda **kw:calls.append(kw) or True)
    assert worker.process_one() and worker.process_one()
    assert len(calls)==1


@pytest.mark.parametrize('fail_extraction',[False,True])
def test_deferred_topics_survive_failures_without_losing_source(conversation,pg_db,monkeypatch,fail_extraction):
    from app.agents.nodes import memory
    uid,cid=conversation
    user,_=repo.save_turn(cid,uid,'I moved to Busan.','How is it?',defer_topics=True)
    calls=[]
    def extract(text,raise_errors):
        assert raise_errors
        calls.append(text)
        if fail_extraction and len(calls)==1:
            raise TimeoutError()
        return ['relocation']
    monkeypatch.setattr(memory,'_extract_topics',extract)
    monkeypatch.setattr(worker,'ingest_turn',lambda **_:False)
    assert worker.process_one()
    with pg_db[1]() as session:
        job=session.scalars(select(GraphSyncEvent)).one()
        assert job.attempts==1
        assert job.payload['defer_topics'] is fail_extraction
        assert session.get(Message,user).content=='I moved to Busan.'
        job.available_at=datetime.now(timezone.utc)
    monkeypatch.setattr(worker,'ingest_turn',lambda **_:True)
    topics=[]
    monkeypatch.setattr(worker,'ingest_topics',lambda **kw:topics.append(kw) or True)
    assert worker.process_one()
    assert len(calls)==(2 if fail_extraction else 1)
    assert topics[0]['topics']==['relocation']
    with pg_db[1]() as session:
        assert session.get(Message,user).memory_topics==['relocation']
        assert session.scalar(select(func.count()).select_from(GraphSyncEvent))==0


def test_deleted_deferred_turn_never_calls_topic_model(conversation,monkeypatch):
    from app.agents.nodes import memory
    uid,cid=conversation
    repo.save_turn(cid,uid,'private','reply',defer_topics=True)
    repo.delete_conversation(cid,uid)
    monkeypatch.setattr(memory,'_extract_topics',lambda *_a,**_kw:pytest.fail('deleted text indexed'))
    monkeypatch.setattr(worker,'delete_conversation_memory',lambda **_:True)
    assert worker.process_one()
