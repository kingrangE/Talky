"""Inspect selected ablation answers; not an independently scored quality benchmark."""
import json,time
from pathlib import Path
from langchain_ollama import ChatOllama
from app.agents.nodes.en_reply import _build_messages
from app.llm.prompt_registry import builtin_text
from app.runtime import bounded_messages
from app.settings import get_settings


def main():
    root=Path('data/test');report=json.loads(Path('operations/experiments/retrieval-ablation.json').read_text())
    originals={m['id']:m['content'] for f in (root/'conversations').glob('*.json') for s in json.loads(f.read_text())['sessions'] for m in s['messages']}
    cases={q['id']:q for directory in ['single_hop','multi_hop','hallucination'] for f in (root/directory).glob('*.json') for q in json.loads(f.read_text())['questions']}
    hallucination=next(cid for cid in cases if '-ha' in cid or '-hl' in cid)
    selected=['s1-sh01','s2-mh04',hallucination]
    cfg=get_settings();model=ChatOllama(base_url=cfg.OLLAMA_BASE_URL,model=cfg.MODEL_NAME,num_ctx=cfg.LLM_CONTEXT_TOKENS,num_predict=128,temperature=0,client_kwargs={'timeout':120})
    outputs=[]
    for cid in selected:
        case=cases[cid]
        for mode in ['postgres_text','neo4j_graph_text']:
            row=next(r for r in report['rows'] if r['case_id']==cid and r['mode']==mode and r['repeat']==0)
            memory=[{'text':originals[mid],'topic':'frozen candidate'} for mid in row['candidate_ids']]
            messages=bounded_messages(_build_messages({'system_prompt':builtin_text('talky/system'),'user_text':case['question'],'retrieved_memory':memory}))
            started=time.monotonic();response=model.invoke(messages)
            output={'case_id':cid,'mode':mode,'question':case['question'],'reference':case.get('reference_answer'),
                    'candidate_ids':row['candidate_ids'],'reply':response.content,'usage':response.usage_metadata,
                    'seconds':round(time.monotonic()-started,3),'note':'Candidates ordered by source ID identically across modes; output cap 128.'}
            outputs.append(output)
            Path('operations/experiments/retrieval-answers.json').write_text(json.dumps({'model':cfg.MODEL_NAME,'scope':'Selected diagnostic answers only; no independent judge or human validation.','rows':outputs},ensure_ascii=False,indent=2)+'\n')
            print(cid,mode,flush=True)

if __name__=='__main__': main()
