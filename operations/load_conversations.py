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

from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app import conversation
from app.db import postgres, repo
from app.db.models import Base, ConversationTurn, User
from app.settings import get_settings

PROMPTS = [
    'I had a busy day at work. How can I relax this evening?',
    'I went to a bookstore yesterday and found an interesting novel.',
    'I want to practice speaking English before my next work meeting.',
    'I enjoy walking in the park after work. What do you do to unwind?',
    '오늘 회의가 많아서 피곤했어요. 저녁에는 산책하고 싶어요.',
]


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
        'full_completion_p95_seconds':percentile([r['completion_seconds'] for r in rows if 'completion_seconds' in r],.95),
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


def run_level(dsn, *, users, workers, horizon, mode, report_dir, warm_workers=False):
    rows=[];processes=[];monitors=[];stop=threading.Event();pool=None
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
            ready_events=[]
            for index in range(workers):
                ready=context.Event() if warm_workers else None
                ready_events.append(ready)
                receiver,sender=context.Pipe(duplex=False)
                process=context.Process(target=_worker,args=(dsn,schema,sender,mode,str(report_dir/f'worker-{index}.log'),ready))
                process.start();sender.close();processes.append((process,receiver))
                monitor=threading.Thread(target=_monitor,args=(process,receiver,get_settings().MOCK_EXAM_JOB_TIMEOUT_SECONDS,stop),daemon=True)
                monitor.start();monitors.append(monitor)
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
                try:
                    conversation.submit_turn(request_id=row['request_id'],conversation_id=row['conversation_id'],user_id=row['user_id'],user_text=PROMPTS[row['index']%len(PROMPTS)])
                    row['accepted']=True
                except Exception as exc:
                    row['accepted']=False;row['outcome']='rejected';row['error_type']=type(exc).__name__
                row['admission_seconds']=time.monotonic()-row['_started']
            pool=ThreadPoolExecutor(max_workers=users)
            submissions=[pool.submit(submit,row) for row in rows]
            peaks={'queued':0,'processing':0};samples=[];resource_samples=[]
            while time.monotonic()-started < horizon:
                with postgres.session_scope() as session:
                    snapshots={str(job.id):conversation._record(job) for job in session.scalars(select(ConversationTurn)).all()}
                counts={state:sum(job['status']==state for job in snapshots.values()) for state in peaks}
                for state,value in counts.items(): peaks[state]=max(peaks[state],value)
                samples.append({'elapsed':round(time.monotonic()-started,3),**counts})
                resource_samples.append({'elapsed':round(time.monotonic()-started,3),'workers':sample_workers(processes)})
                for row in rows:
                    if not row.get('accepted') or row['outcome'] in {'completed','failed'}:
                        continue
                    job=snapshots.get(row['request_id'])
                    if job is None: continue
                    elapsed=time.monotonic()-row['_started']
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
                    'summary':summary(rows,mode=mode),'queue_peaks':peaks,'queue_samples':samples,'resource_samples':resource_samples,'rows':rows}
        finally:
            if pool is not None:
                pool.shutdown(wait=True,cancel_futures=True)
            stop.set()
            for monitor in monitors: monitor.join(12)
            for process,receiver in processes:
                if process.is_alive(): process.kill();process.join(5)
                receiver.close();process.close()


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
    parser.add_argument('--warm-workers',action='store_true')
    parser.add_argument('--rounds',type=int,default=1)
    parser.add_argument('--horizon',type=int,default=90)
    parser.add_argument('--mode',choices=['real','synthetic'],default='real')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    levels=[int(value) for value in args.levels.split(',')]
    if not levels or min(levels)<1 or max(levels)>100 or not 1<=args.workers<=16 or args.rounds<1 or args.horizon<10:
        parser.error('invalid levels/workers/rounds/horizon')
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
                  'queue_limit':cfg.CONVERSATION_QUEUE_LIMIT,'python':platform.python_version(),'platform':platform.platform(),
                  'profile':'one simultaneous text request per distinct account; short history; 80% English, 20% Korean; real correction and Piper output',
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
                result=run_level(dsn,users=users,workers=args.workers,horizon=args.horizon,mode=args.mode,report_dir=directory,warm_workers=args.warm_workers)
                (directory/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
                reports.append(result)
                print(json.dumps({'users':users,**result['summary']},ensure_ascii=False),flush=True)
        (args.output/'summary.json').write_text(json.dumps([{'users':r['users'],**r['summary']} for r in reports],indent=2)+'\n')

if __name__=='__main__': main()
