"""Real PostgreSQL queue load; deterministic model output, never a GPU benchmark."""
import json
import os
import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import func, select
from app import conversation as queue
from app.db import repo
from app.db.models import Conversation, ConversationTurn, Message, User
from app.runtime import ModelBusy, inference_slot
from app.settings import get_settings


def test_concurrent_queue_load_has_no_lost_or_duplicate_turns(pg_db, monkeypatch):
    monkeypatch.setenv('INFERENCE_CAPACITY', '2')
    get_settings.cache_clear()
    with pg_db[1]() as session:
        user = User(display_name='operational-load')
        session.add(user)
        session.flush()
        uid = str(user.id)
        conversations = [Conversation(user_id=user.id) for _ in range(32)]
        session.add_all(conversations)
        session.flush()
        ids = [str(c.id) for c in conversations]
    submit_ms, queue_ms, work_ms = [], [], []
    def submit(cid):
        started = time.monotonic()
        result = queue.submit_turn(request_id=str(uuid.uuid4()), conversation_id=cid, user_id=uid,
                                   user_text='Synthetic operational test')
        submit_ms.append((time.monotonic()-started)*1000)
        return result
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = list(pool.map(submit, ids))
    def drain(_):
        while True:
            try:
                with inference_slot(for_exam=True):
                    job = queue.claim_turn()
                    if job is None:
                        return
                    from datetime import datetime, timezone
                    queue_ms.append((datetime.now(timezone.utc)-datetime.fromisoformat(job['created_at'])).total_seconds()*1000)
                    t = time.monotonic()
                    repo.save_turn(job['conversation_id'], uid, 'Synthetic operational test', 'Deterministic reply',
                                   request_id=job['id'], expected_attempt=job['attempt'])
                    queue.process_turn(job)
                    work_ms.append((time.monotonic()-t)*1000)
            except ModelBusy:
                time.sleep(.005)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(drain, range(4)))
    elapsed = time.monotonic()-started
    assert all(queue.get_turn(j['id'], uid)['status']=='completed' for j in jobs)
    with pg_db[1]() as session:
        assert session.scalar(select(func.count()).select_from(Message)) == 64
        assert session.scalar(select(func.count()).select_from(ConversationTurn).where(ConversationTurn.attempts==1)) == 32
    def stats(values):
        ordered = sorted(values)
        return {'p50_ms': round(statistics.median(ordered),2), 'p95_ms': round(ordered[int(.95*(len(ordered)-1))],2)}
    result = {'kind':'synthetic_model_real_postgres','requests':32,'clients':8,'workers':4,'inference_slots':2,
              'completed':32,'messages':64,'elapsed_seconds':round(elapsed,3),
              'submit':stats(submit_ms),'queue_wait':stats(queue_ms),'processing':stats(work_ms)}
    print(json.dumps(result))
    if os.environ.get('TALKY_LOAD_RESULT'):
        Path(os.environ['TALKY_LOAD_RESULT']).write_text(json.dumps(result, indent=2)+'\n')
