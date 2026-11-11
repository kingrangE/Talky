"""CPU-only routing diagnostic; never an end-to-end latency claim."""
import json
from pathlib import Path
import time
from app.agents.nodes.memory import recall_decision
from operations.load_conversations import PROMPTS, percentile


def main():
    cases=[
        ('greeting','Hello!',[],False),
        ('thanks','감사합니다!',[],False),
        ('personal_reference','내가 좋아하는 그거 있잖아!',[],True),
        ('context','Thanks',[{'role':'assistant','content':'Do you remember the cafe you mentioned?'}],True),
    ]
    results=[]
    for label,text,history,expected in cases:
        assert recall_decision(text,history)[0] == expected
        for _ in range(100):
            recall_decision(text,history)
        samples=[]
        for _ in range(10000):
            started=time.perf_counter_ns()
            search,reason=recall_decision(text,history)
            samples.append((time.perf_counter_ns()-started)/1000)
        results.append({'case':label,'search':search,'reason':reason,'iterations':len(samples),
                        'p50_us':percentile(samples,.5),'p95_us':percentile(samples,.95),'p99_us':percentile(samples,.99)})
    report={'measurement':'Pure local rule decision CPU microbenchmark, excludes module startup, logging, DB, LLM and end-to-end response. Not a 50-user SLO result.',
            'cases':results,'existing_load_profile_searches':sum(recall_decision(p,[])[0] for p in PROMPTS),
            'existing_load_profile_inputs':len(PROMPTS)}
    Path('operations/experiments/memory-gate-cpu.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':
    main()
