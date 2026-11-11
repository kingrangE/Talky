"""Summarize saved runs without turning diagnostic samples into SLO qualification."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from operations.load_conversations import percentile


def analyze(directory):
    results=[]
    for file in sorted(directory.glob('users-*/result.json'),key=lambda p: int(p.parent.name.split('-')[1])):
        result=json.loads(file.read_text())
        stages=defaultdict(list);resources=defaultdict(list);graph_resources=defaultdict(list);gates=Counter()
        for logfile in file.parent.glob('*worker*.log'):
            for line in logfile.read_text(errors='replace').splitlines():
                start=line.find('{')
                if start<0: continue
                try: event=json.loads(line[start:])
                except json.JSONDecodeError: continue
                if event.get('event')=='stage': stages[event['stage']].append(event['duration_ms'])
                if event.get('event')=='resource_wait':
                    (graph_resources if logfile.name=='graph-worker.log' else resources)[event['resource']].append(event['duration_ms'])
                if event.get('event')=='memory_gate': gates[event['reason']]+=1
        stats=lambda groups:{name:{'count':len(values),'p50_ms':percentile(values,.5),'p95_ms':percentile(values,.95),'max_ms':max(values)} for name,values in groups.items()}
        summary=result['summary']
        n=summary['attempted']
        results.append({'users':result['users'],**summary,
                        'application_error_rate':(summary['admission_rejections']+summary['terminal_failures'])/n,
                        'stage_latency':stats(stages),'resource_wait':stats(resources),
                        'graph_resource_wait':stats(graph_resources),'memory_gate_decisions':dict(gates),
                        'graph_backlog_at_stop':result.get('graph_backlog_at_stop'),
                        'graph_events_with_retries_at_stop':result.get('graph_events_with_retries_at_stop')})
    report={'runs':results,'notes':[
        'p95 and p99 use nearest-rank across all attempted requests; rejected or unavailable text remains in denominator.',
        'null percentile means unavailable/infinite under this profile, not zero latency.',
        'Harness timeouts are reported separately from terminal application failures.',
        'Worker startup scope is recorded in run.json. Host Ollama may be shared; CPU voice runtime is Linux amd64 on Docker Desktop.',
        'No browser render or HTTP transport measurement; no sustained-user or final p99 qualification.',
    ]}
    (directory/'analysis.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report


def main():
    parser=argparse.ArgumentParser();parser.add_argument('directory',type=Path)
    args=parser.parse_args()
    print(json.dumps(analyze(args.directory),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
