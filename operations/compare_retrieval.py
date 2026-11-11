"""Paired retrieval ablation using frozen topic outputs and actual PostgreSQL/Neo4j."""
import argparse,json,time,statistics,hashlib
from pathlib import Path
from sqlalchemy import create_engine,text
from sqlalchemy.engine import make_url
from neo4j import GraphDatabase
from app.graph_db import recall

DATA=Path('data/test')


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--postgres',required=True)
    p.add_argument('--neo4j',required=True)
    p.add_argument('--run',default='.data/evaluations/20261110T055722Z-21f4ee')
    args=p.parse_args()
    if not (make_url(args.postgres).database or '').endswith('_test'):
        raise ValueError('dedicated test DB required')
    saved=Path(args.run)
    namespace=json.loads((saved/'run.json').read_text())['graph_namespace']
    topics={r['case_id']:r['output']['turns'][0]['topics'] for r in map(json.loads,(saved/'memory.jsonl').read_text().splitlines()) if r['stage']=='generation' and r.get('output',{}).get('turns')}
    sources=[json.loads(f.read_text()) for f in sorted((DATA/'conversations').glob('*.json'))]
    driver=GraphDatabase.driver(args.neo4j,auth=None)
    recall.get_driver=lambda:driver
    recall.ensure_schema=lambda:True
    engine=create_engine(args.postgres)
    results=[]
    with engine.begin() as pg,driver.session() as graph:
        pg.execute(text('CREATE TEMP TABLE retrieval_baseline (uid TEXT, id TEXT, content TEXT, ts TEXT) ON COMMIT DROP'))
        for source in sources:
            uid=namespace+':'+source['set_id']
            original={m['id']:m['content'] for session in source['sessions'] for m in session['messages']}
            rows=graph.run('MATCH (:User {id:$uid})-[:HAD]->(:Conversation)-[:CONTAINS]->(m:Message) RETURN m.id AS id,m.text AS content,toString(m.ts) AS ts',uid=uid).data()
            assert {r['id'].split(':',1)[1]:r['content'] for r in rows}==original, 'fixture differs from canonical source'
            pg.execute(text('INSERT INTO retrieval_baseline VALUES (:uid,:id,:content,:ts)'),[{**r,'uid':uid} for r in rows])
        pg.execute(text('CREATE INDEX ON retrieval_baseline(uid)'))
        for folder in ['single_hop','multi_hop','hallucination']:
            for f in sorted((DATA/folder).glob('*.json')):
                source=json.loads(f.read_text());uid=namespace+':'+source['set_id']
                for case in source['questions']:
                    cid=case['id'];question=case['question'];frozen=topics[cid]
                    terms=recall._query_terms(question,[namespace+'/'+t for t in frozen])
                    required={e['message_id'] for e in case.get('evidence',[])}
                    for mode in ['postgres_text','neo4j_text','neo4j_graph_text']:
                        for repetition in range(3):
                            start=time.perf_counter()
                            if mode=='postgres_text':
                                rows=pg.execute(text('''SELECT id AS message_id,content AS text,ts FROM retrieval_baseline,
                                    LATERAL (SELECT sum(t.weight) AS score FROM jsonb_to_recordset(CAST(:terms AS jsonb)) AS t(term TEXT,weight FLOAT)
                                      WHERE strpos(lower(content),t.term)>0) rank
                                    WHERE uid=:uid AND score>0 ORDER BY score DESC,ts DESC,id DESC LIMIT 8'''),
                                    {'uid':uid,'terms':json.dumps([{'term':t['text'],'weight':t['weight']} for t in terms])}).mappings().all()
                            else:
                                rows=recall.recall_related(uid,[namespace+'/'+t for t in frozen],limit=8,query_text=question,raise_errors=True,use_graph=mode=='neo4j_graph_text')
                            elapsed=(time.perf_counter()-start)*1000
                            found={r['message_id'].split(':',1)[1] for r in rows}
                            results.append({'case_id':cid,'suite':folder,'mode':mode,'repeat':repetition,'ms':round(elapsed,3),
                                'required':len(required),'found':len(required&found),'complete': bool(required) and required<=found,
                                'candidate_ids':sorted(found),'bytes':sum(len(r['text'].encode()) for r in rows)})
    summary=[]
    for mode in ['postgres_text','neo4j_text','neo4j_graph_text']:
        for suite in ['single_hop','multi_hop','hallucination']:
            rows=[r for r in results if r['mode']==mode and r['suite']==suite];one=[r for r in rows if r['repeat']==0]
            times=sorted(r['ms'] for r in rows if r['repeat']>0)
            summary.append({'mode':mode,'suite':suite,'cases':len(one),'complete_evidence':sum(r['complete'] for r in one),
                'evidence_recall':sum(r['found'] for r in one)/sum(r['required'] for r in one) if sum(r['required'] for r in one) else None,
                'warm_p50_ms':round(statistics.median(times),3),'warm_p95_ms':times[int(.95*(len(times)-1))]})
    report={'namespace':namespace,'frozen_run':str(saved),'source_sha256':hashlib.sha256(b''.join(f.read_bytes() for f in sorted(DATA.rglob('*.json')))).hexdigest(),
            'scope':'Retrieval only; same 144 messages, frozen topics, limit 8. Development set, no held-out or answer quality claim. First pass is not OS/database cold cache.',
            'summary':summary,'rows':results}
    Path('operations/experiments/retrieval-ablation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False))
    driver.close();engine.dispose()

if __name__=='__main__': main()
