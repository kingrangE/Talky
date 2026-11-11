import json,time,urllib.request
from pathlib import Path
from langchain_core.messages import SystemMessage,HumanMessage
from app.runtime import bounded_messages
from app.settings import get_settings
cfg=get_settings()
rows=[]
for language, text in [('ko','오늘 회사에서 회의를 마치고 도서관에 들렀습니다. '*120),('en','Today I finished work and visited the library. '*120)]:
    messages=bounded_messages([SystemMessage(content='Answer with exactly OK.'),HumanMessage(content=text+' The evidence code is BLUE42. Return OK.')])
    data={'model':cfg.MODEL_NAME,'stream':False,'messages':[{'role':'system' if m.type=='system' else 'user','content':m.content} for m in messages],'options':{'num_ctx':cfg.LLM_CONTEXT_TOKENS,'num_predict':8,'temperature':0}}
    started=time.monotonic()
    with urllib.request.urlopen(urllib.request.Request(cfg.OLLAMA_BASE_URL+'/api/chat',data=json.dumps(data).encode(),headers={'Content-Type':'application/json'}),timeout=240) as response: result=json.load(response)
    rows.append({'language':language,'input_bytes':sum(len(m.content.encode())+64 for m in messages),'context':cfg.LLM_CONTEXT_TOKENS,'output_reserved':cfg.LLM_MAX_OUTPUT_TOKENS,'prompt_eval_count':result.get('prompt_eval_count'),'eval_count':result.get('eval_count'),'elapsed_seconds':round(time.monotonic()-started,3),'load_seconds':result.get('load_duration',0)/1e9,'done_reason':result.get('done_reason')})
Path('operations/experiments/context-budget.json').write_text(json.dumps({'model':cfg.MODEL_NAME,'note':'Two diagnostic cases; no population latency claim. First request may load model. Output cap 8 for this diagnostic only.','cases':rows},ensure_ascii=False,indent=2)+'\n')
print(json.dumps(rows))
