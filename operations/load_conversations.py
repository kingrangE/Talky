"""Isolated burst load against the real durable conversation pipeline.

Poll-visible text is a backend proxy including 1s polling, not browser render latency.
Never target a production DB. Synthetic mode verifies the harness and cannot pass an SLO.
"""
from __future__ import annotations

import argparse
import hashlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
import math
import multiprocessing
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import threading
import time
import uuid

from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app import conversation
from app.db import postgres, repo
from app.db.models import Base, ConversationTurn, GraphSyncEvent, User
from app.settings import get_settings

PROMPTS = [
    'I had a busy day at work. How can I relax this evening?',
    'I went to a bookstore yesterday and found an interesting novel.',
    'I want to practice speaking English before my next work meeting.',
    'I enjoy walking in the park after work. What do you do to unwind?',
    '오늘 회의가 많아서 피곤했어요. 저녁에는 산책하고 싶어요.',
]

MEMORY_PROMPTS = [
    'Which city do I live in?',
    '내 고양이 이름이 뭐였지?',
    'What did I say I do on weekends?',
    '다음 여행은 어디로 가기로 했었지?',
    'What English skill did I want to practice?',
]
SEED_TURNS = [
    ('I live in Seoul and my cat is named Milo.', ['seoul', 'cat']),
    ('I go hiking on Saturdays, and my next trip is to Kyoto.', ['hiking', 'kyoto']),
    ('I want to practice explaining technical problems in English.', ['english practice']),
]


def _graph_worker(dsn, schema, stop, ready, logfile):
    postgres._engine = create_engine(dsn, connect_args={'options':f'-c search_path={schema} -c statement_timeout=30000 -c lock_timeout=5000'})
    postgres._SessionLocal = sessionmaker(bind=postgres._engine, autoflush=False)
    logging.basicConfig(filename=logfile, level=logging.INFO, format='%(asctime)s %(process)d %(name)s %(message)s')
    from app.graph_db.worker import process_one
    from app.graph_db.neo4j_client import get_driver
    from app.runtime import job_budget
    get_driver().verify_connectivity()
    ready.set()
    while not stop.is_set():
        with job_budget(get_settings().MOCK_EXAM_JOB_TIMEOUT_SECONDS):
            worked = process_one()
        if not worked:
            stop.wait(.2)


def _cleanup_graph(rows):
    # Scope cleanup to this disposable run; keep fences against late projections.
    from app.graph_db.neo4j_client import get_driver
    cids = [cid for row in rows for cid in [row['conversation_id'], row.get('source_conversation_id')] if cid]
    with get_driver().session() as graph:
        graph.run('UNWIND $ids AS id MERGE (f:GraphConversationFence {id:id}) SET f.deleted=true', ids=cids).consume()
        graph.run('MATCH (u:User)-[:HAD]->(c:Conversation)-[:CONTAINS]->(m:Message) WHERE u.id IN $uids AND c.id IN $cids DETACH DELETE m', uids=[r['user_id'] for r in rows], cids=cids).consume()
        graph.run('MATCH (u:User)-[:HAD]->(c:Conversation) WHERE u.id IN $uids AND c.id IN $cids DETACH DELETE c', uids=[r['user_id'] for r in rows], cids=cids).consume()
        graph.run('MATCH (u:User) WHERE u.id IN $ids AND NOT (u)-[:HAD]->() DETACH DELETE u', ids=[r['user_id'] for r in rows]).consume()
        graph.run('MATCH (e:GraphSyncMarker) WHERE e.conversation_id IN $ids DETACH DELETE e', ids=cids).consume()


def percentile(values, q):
    if not values:
        return None
    return sorted(values)[max(0, math.ceil(len(values)*q)-1)]


def summary(rows, *, mode, min_samples=1000):
    # Admission rejection and missing text remain in the latency denominator.
    text = [row.get('text_seconds', math.inf) for row in rows]
    errors = sum(row['outcome'] != 'completed' for row in rows)
    p95, p99 = percentile(text,.95), percentile(text,.99)
    limits_met = bool(rows) and p95 <= 8 and p99 <= 10 and errors/len(rows) < .05
    return {
        'attempted':len(rows),'accepted':sum(row.get('accepted',False) for row in rows),
        'completed':sum(row['outcome']=='completed' for row in rows),
        'error_or_harness_timeout_count':errors,
        'error_or_harness_timeout_rate':errors/len(rows) if rows else None,
        'admission_rejections':sum(row['outcome']=='rejected' for row in rows),
        'terminal_failures':sum(row['outcome']=='failed' for row in rows),
        'harness_timeouts':sum(row['outcome']=='harness_timeout' for row in rows),
        'text_p95_seconds':p95 if p95 is not None and math.isfinite(p95) else None,
        'text_p99_seconds':p99 if p99 is not None and math.isfinite(p99) else None,
        'text_observed_count':sum(math.isfinite(value) for value in text),
        'text_max_observed_seconds':max((v for v in text if math.isfinite(v)),default=None),
        'text_over_10s_or_unavailable':sum(value>10 for value in text),
        'full_completion_p95_seconds':percentile([r['completion_seconds'] for r in rows if r['outcome']=='completed' and 'completion_seconds' in r],.95),
        'audio_completed':sum(row.get('audio_completed',False) for row in rows),
        'correction_degraded':sum(row.get('correction_degraded',False) for row in rows),
        'limits_met_in_sample':limits_met,
        'qualification':'fail' if not limits_met else 'insufficient_samples' if len(rows)<min_samples else 'backend_proxy_only',
        'slo_pass':False,
        'qualification_note':'Browser/HTTP rendering and sustained 50-user sessions require a separate deployment test; synthetic results never qualify.' if mode=='real' else 'Synthetic harness validation only.',
    }


@contextmanager
def isolated_database(dsn):
    url=make_url(dsn)
    if not (url.database or '').endswith('_test'):
        raise ValueError('dedicated database ending in _test required')
    schema='talky_load_'+uuid.uuid4().hex
    control=create_engine(dsn)
    previous=postgres._engine,postgres._SessionLocal
    engine=None
    try:
        with control.begin() as conn:
            conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        engine=create_engine(dsn,connect_args={'options':f'-c search_path={schema} -c statement_timeout=30000 -c lock_timeout=5000'})
        Base.metadata.create_all(engine)
        postgres._engine=engine
        postgres._SessionLocal=sessionmaker(bind=engine,autoflush=False)
        yield schema,engine
    finally:
        postgres._engine,postgres._SessionLocal=previous
        if engine is not None:
            engine.dispose()
        with control.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        control.dispose()


def _worker(dsn,schema,channel,mode,logfile,ready):
    postgres._engine=create_engine(dsn,connect_args={'options':f'-c search_path={schema} -c statement_timeout=30000 -c lock_timeout=5000'})
    postgres._SessionLocal=sessionmaker(bind=postgres._engine,autoflush=False)
    logging.basicConfig(filename=logfile,level=logging.INFO,format='%(asctime)s %(process)d %(name)s %(message)s')
    if mode=='real':
        if ready is not None:
            from app.agents.conversation_graph import build_conversation_graph
            from app.llm.factory import get_chat_llm
            from app.audio.tts_engine import _voice
            build_conversation_graph();get_chat_llm(streaming=False)
            if _voice('en') is None:
                raise RuntimeError('Piper voice prewarm failed')
            ready.set()
        from app.mock_exam.worker import _run_worker
        _run_worker(channel)
    else:
        if ready is not None: ready.set()
        while True:
            channel.send('heartbeat')
            job=conversation.claim_turn()
            if job:
                channel.send('started')
                repo.save_turn(job['conversation_id'],job['user_id'],job['input_text'],'Synthetic reply',request_id=job['id'],expected_attempt=job['attempt'])
                conversation.process_turn(job)
                channel.send('idle')
            time.sleep(.05)


def run_level(dsn, *, users, workers, horizon, mode, report_dir, warm_workers=False, graph_worker=False, profile="generic"):
    rows=[];processes=[];monitors=[];stop=threading.Event();pool=None
    graph_process=None;graph_stop=None;restarts=[]
    from app.mock_exam.worker import _monitor
    with isolated_database(dsn) as (schema,engine):
        for index in range(users):
            with postgres.session_scope() as session:
                user=User(display_name='load-'+uuid.uuid4().hex)
                session.add(user);session.flush();uid=str(user.id)
            cid=repo.create_conversation(user_id=uid)
            rows.append({'index':index,'user_id':uid,'conversation_id':cid,'request_id':str(uuid.uuid4()),'outcome':'pending'})
        context=multiprocessing.get_context('spawn')
        try:
            if profile == 'mixed-memory':
                from app.graph_db.worker import process_one
                for row in rows:
                    source=repo.create_conversation(user_id=row['user_id'])
                    row['source_conversation_id']=source
                    for text,topics in SEED_TURNS:
                        repo.save_turn(source,row['user_id'],text,'Thanks for telling me.',memory_topics=topics)
                    repo.save_turn(row['conversation_id'],row['user_id'],
                                   'I would like to practice English today.','Sure, what would you like to talk about?')
                while process_one():
                    with postgres.session_scope() as session:
                        if session.scalar(select(func.count()).select_from(GraphSyncEvent).where(GraphSyncEvent.attempts > 0)):
                            raise RuntimeError('fixture graph projection failed')
            if graph_worker:
                graph_stop=context.Event();graph_ready=context.Event()
                graph_process=context.Process(target=_graph_worker,args=(dsn,schema,graph_stop,graph_ready,str(report_dir/'graph-worker.log')))
                graph_process.start()
                if not graph_ready.wait(30):
                    raise RuntimeError('graph worker did not become ready')
            def spawn_worker(index, ready=None):
                receiver,sender=context.Pipe(duplex=False)
                process=context.Process(target=_worker,args=(dsn,schema,sender,mode,str(report_dir/f'worker-{index}.log'),ready))
                process.start();sender.close()
                monitor=threading.Thread(target=_monitor,args=(process,receiver,get_settings().MOCK_EXAM_JOB_TIMEOUT_SECONDS,stop),daemon=True)
                monitor.start()
                return process,receiver,monitor
            ready_events=[]
            for index in range(workers):
                ready=context.Event() if warm_workers else None
                ready_events.append(ready)
                process,receiver,monitor=spawn_worker(index,ready)
                processes.append((process,receiver));monitors.append(monitor)
            if warm_workers:
                deadline=time.monotonic()+120
                for ready in ready_events:
                    if not ready.wait(max(0,deadline-time.monotonic())):
                        raise RuntimeError('workers did not become ready within 120s')
            started=time.monotonic()
            barrier=threading.Barrier(users)
            def submit(row):
                barrier.wait(timeout=30)
                row['_started']=time.monotonic()
                row['user_text']=(PROMPTS+MEMORY_PROMPTS if profile=='mixed-memory' else PROMPTS)[row['index']%(10 if profile=='mixed-memory' else len(PROMPTS))]
                try:
                    conversation.submit_turn(request_id=row['request_id'],conversation_id=row['conversation_id'],user_id=row['user_id'],user_text=row['user_text'])
                    row['accepted']=True
                except Exception as exc:
                    row['accepted']=False;row['outcome']='rejected';row['error_type']=type(exc).__name__
                row['admission_seconds']=time.monotonic()-row['_started']
            pool=ThreadPoolExecutor(max_workers=users)
            submissions=[pool.submit(submit,row) for row in rows]
            peaks={'queued':0,'processing':0};samples=[];resource_samples=[]
            while time.monotonic()-started < horizon:
                for index,(process,receiver) in enumerate(processes):
                    if not process.is_alive():
                        monitors[index].join(12)
                        if monitors[index].is_alive():
                            raise RuntimeError('worker supervisor did not stop')
                        restarts.append({'worker':index,'exitcode':process.exitcode,'elapsed':round(time.monotonic()-started,3)})
                        receiver.close();process.close()
                        process,receiver,monitor=spawn_worker(index)
                        processes[index]=(process,receiver);monitors[index]=monitor
                with postgres.session_scope() as session:
                    backlog=session.scalar(select(func.count()).select_from(GraphSyncEvent)) if graph_worker else None
                    graph_errors=session.scalar(select(func.count()).select_from(GraphSyncEvent).where(GraphSyncEvent.attempts>0)) if graph_worker else None
                    snapshots={str(job.id):conversation._record(job) for job in session.scalars(select(ConversationTurn)).all()}
                counts={state:sum(job['status']==state for job in snapshots.values()) for state in peaks}
                for state,value in counts.items(): peaks[state]=max(peaks[state],value)
                samples.append({'elapsed':round(time.monotonic()-started,3),**counts,'graph_backlog':backlog,'graph_events_with_retries':graph_errors})
                if graph_process is not None and not graph_process.is_alive():
                    raise RuntimeError('graph worker exited during load')
                resource_samples.append({'elapsed':round(time.monotonic()-started,3),'workers':sample_workers(processes)})
                for row in rows:
                    if not row.get('accepted') or row['outcome'] in {'completed','failed'}:
                        continue
                    job=snapshots.get(row['request_id'])
                    if job is None: continue
                    elapsed=time.monotonic()-row['_started']
                    if job['result']:
                        row['result']=job['result']
                    if job['result'] and 'text_seconds' not in row:
                        row['text_seconds']=elapsed
                    if job['status'] in {'completed','failed'}:
                        row['outcome']=job['status'];row['attempts']=job['attempt'];row['error_code']=job['error_code']
                        row['completion_seconds']=elapsed
                        row['audio_completed']=bool(job['output_audio_path'])
                        row['correction_degraded']='표현 교정을 완료하지 못했습니다' in (job['result'].get('better_expression') or '')
                if all(row['outcome'] in {'completed','failed','rejected'} for row in rows): break
                time.sleep(1)
            pool.shutdown(wait=True)
            for future in submissions:
                future.result()
            for row in rows:
                if row['outcome']=='pending': row['outcome']='harness_timeout'
                row.pop('_started',None)
            return {'users':users,'workers':workers,'mode':mode,'horizon_seconds':horizon,'elapsed_seconds':time.monotonic()-started,
                    'worker_restarts':restarts,'graph_worker':graph_worker,'profile':profile,'graph_backlog_at_stop':backlog,
                    'graph_events_with_retries_at_stop':graph_errors,
                    'summary':summary(rows,mode=mode),'queue_peaks':peaks,'queue_samples':samples,'resource_samples':resource_samples,'rows':rows}
        finally:
            if pool is not None:
                pool.shutdown(wait=True,cancel_futures=True)
            stop.set()
            for monitor in monitors: monitor.join(12)
            for process,receiver in processes:
                if process.is_alive(): process.kill();process.join(5)
                receiver.close();process.close()
            if graph_process is not None:
                graph_stop.set();graph_process.join(5)
                if graph_process.is_alive(): graph_process.kill();graph_process.join(5)
                graph_process.close()
            if graph_worker or profile=='mixed-memory':
                _cleanup_graph(rows)


def sample_workers(processes):
    samples=[]
    for process,_ in processes:
        try:
            root=Path('/proc')/str(process.pid)
            rss=int((root/'statm').read_text().split()[1])*os.sysconf('SC_PAGE_SIZE')
            fields=(root/'stat').read_text().rsplit(')',1)[1].split()
            cpu=(int(fields[11])+int(fields[12]))/os.sysconf('SC_CLK_TCK')
            samples.append({'pid':process.pid,'rss_bytes':rss,'cpu_seconds':cpu})
        except (OSError,ValueError,IndexError):
            continue
    return samples


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--levels',default='1,5,10,25,50')
    parser.add_argument('--workers',type=int,default=4)
    parser.add_argument('--graph-worker',action='store_true')
    parser.add_argument('--profile',choices=['generic','mixed-memory'],default='generic')
    parser.add_argument('--warm-workers',action='store_true')
    parser.add_argument('--rounds',type=int,default=1)
    parser.add_argument('--horizon',type=int,default=90)
    parser.add_argument('--mode',choices=['real','synthetic'],default='real')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    levels=[int(value) for value in args.levels.split(',')]
    if not levels or min(levels)<1 or max(levels)>100 or not 1<=args.workers<=16 or args.rounds<1 or args.horizon<10:
        parser.error('invalid levels/workers/rounds/horizon')
    if args.profile=='mixed-memory' and (not args.graph_worker or args.mode!='real'):
        parser.error('mixed-memory needs --graph-worker and --mode real')
    dsn=os.environ['TALKY_TEST_POSTGRES_DSN']
    if not (make_url(dsn).database or '').endswith('_test'): parser.error('dedicated *_test DB required')
    # Always local inference and synthetic identities, no paid provider or Cloud prompt writes.
    os.environ.update(LANGFUSE_ENABLED='false',LLM_PROVIDER='ollama',AUDIO_S3_BUCKET='',WORKER_WARMUP='false')
    args.output.mkdir(parents=True,exist_ok=False)
    try:
        revision=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
        dirty=subprocess.run(['git','diff','--quiet']).returncode!=0
    except (FileNotFoundError,subprocess.CalledProcessError):
        revision=os.environ.get('TALKY_BENCHMARK_REVISION','unavailable')
        dirty=None
    with tempfile.TemporaryDirectory(prefix='talky-load-audio-') as audio:
        os.environ['MOCK_EXAM_DATA_DIR']=audio
        get_settings.cache_clear();cfg=get_settings()
        metadata={'started_at':datetime.now(timezone.utc).isoformat(),'model':cfg.MODEL_NAME,'context':cfg.LLM_CONTEXT_TOKENS,
                  'max_output_tokens':cfg.LLM_MAX_OUTPUT_TOKENS,'inference_capacity':cfg.INFERENCE_CAPACITY,'audio_capacity':cfg.AUDIO_CAPACITY,
                  'memory_gate_enabled':cfg.MEMORY_GATE_ENABLED,'memory_gate_model':cfg.MEMORY_GATE_MODEL or cfg.MODEL_NAME,
                  'memory_gate_timeout_seconds':cfg.MEMORY_GATE_TIMEOUT_SECONDS,'graph_worker':args.graph_worker,
                  'profile_name':args.profile,'load_prompts':PROMPTS+MEMORY_PROMPTS if args.profile=='mixed-memory' else PROMPTS,'seed_turns':SEED_TURNS if args.profile=='mixed-memory' else [],
                  'queue_limit':cfg.CONVERSATION_QUEUE_LIMIT,'python':platform.python_version(),'platform':platform.platform(),
                  'profile':'one simultaneous text request per distinct account; real correction and Piper output; mixed-memory has 50% memory questions, one prior current turn and three source turns; generic is 80% English, 20% Korean',
                  'measurement':'DB result observed with 1s polls; excludes HTTP, browser rendering, voice input/STT and AWS',
                  'targets':{'concurrent_users':50,'text_p95_seconds':8,'text_p99_seconds':10,'error_rate_strictly_below':.05},
                  'commit':revision,'tracked_tree_dirty':dirty,
                  'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  'worker_startup_included':not args.warm_workers,'cache_state':'model may already be loaded; each worker process starts fresh',
                  'audio_cache':cfg.AUDIO_CACHE_DIR,'host_description':os.environ.get('BENCHMARK_HOST_DESCRIPTION','unspecified'),
                  'resource_measurement':'Linux worker RSS and CPU seconds; host model GPU utilization not measured'}
        (args.output/'run.json').write_text(json.dumps(metadata,indent=2)+'\n')
        reports=[]
        for users in levels:
            for round_index in range(args.rounds):
                directory=args.output/f'users-{users}-round-{round_index+1}';directory.mkdir()
                print(f'Starting {users} users / round {round_index+1}',flush=True)
                result=run_level(dsn,users=users,workers=args.workers,horizon=args.horizon,mode=args.mode,report_dir=directory,warm_workers=args.warm_workers,graph_worker=args.graph_worker,profile=args.profile)
                (directory/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
                reports.append(result)
                print(json.dumps({'users':users,**result['summary']},ensure_ascii=False),flush=True)
        (args.output/'summary.json').write_text(json.dumps([{'users':r['users'],**r['summary']} for r in reports],indent=2)+'\n')

if __name__=='__main__': main()
