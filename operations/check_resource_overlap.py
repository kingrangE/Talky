"""Diagnostic real-model comparison: an injected CPU delay and a competing text reply."""
import json
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from sqlalchemy.engine import make_url
from langchain_ollama import ChatOllama
from app.runtime import resource_slot
from app.settings import get_settings


def main():
    url = make_url(os.environ['TALKY_TEST_POSTGRES_DSN'])
    if not (url.database or '').endswith('_test'):
        raise ValueError('dedicated test DB required')
    for key,value in {'HOST':url.host,'PORT':url.port or 5432,'DB':url.database,'USER':url.username,'PASSWORD':url.password}.items():
        os.environ['POSTGRES_'+key] = str(value)
    get_settings.cache_clear()
    cfg=get_settings()
    model=ChatOllama(base_url=cfg.OLLAMA_BASE_URL,model=cfg.MODEL_NAME,num_ctx=cfg.LLM_CONTEXT_TOKENS,num_predict=8,temperature=0)
    model.invoke('Reply OK.')
    results=[]
    for mode in ['whole_job','generation_only']:
        entered=threading.Event()
        def first():
            if mode=='whole_job':
                with resource_slot('inference'):
                    entered.set(); time.sleep(2)
                    model.invoke('Reply OK.')
            else:
                with resource_slot('audio'):
                    entered.set(); time.sleep(2)
                with resource_slot('inference'):
                    model.invoke('Reply OK.')
        def second():
            entered.wait()
            started=time.monotonic()
            with resource_slot('inference'):
                result=model.invoke('Reply OK.')
            return round(time.monotonic()-started,3)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(first); b=pool.submit(second)
            elapsed=b.result();a.result()
        results.append({'mode':mode,'competing_text_seconds':elapsed,'injected_cpu_seconds':2})
    report={'model':cfg.MODEL_NAME,'context':cfg.LLM_CONTEXT_TOKENS,'results':results,
            'limits':'One diagnostic pair, warm model, injected CPU delay, output cap 8; not throughput or p95 estimate.'}
    Path('operations/experiments/resource-overlap.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))

if __name__=='__main__': main()
