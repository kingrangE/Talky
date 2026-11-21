"""Exercise retrieval against an explicitly selected disposable Neo4j database."""
import os
import uuid

import pytest
from neo4j import GraphDatabase

from app.graph_db import recall
from app.graph_db import ingest
from datetime import datetime, timedelta, timezone


@pytest.fixture
def graph(monkeypatch):
    uri = os.getenv('TALKY_TEST_NEO4J_URI')
    if not uri:
        pytest.skip('Set TALKY_TEST_NEO4J_URI to a disposable Neo4j database')
    driver = GraphDatabase.driver(uri, auth=None)
    scope = 'recall-test-' + uuid.uuid4().hex
    monkeypatch.setattr(recall, 'get_driver', lambda: driver)
    monkeypatch.setattr(recall, 'ensure_schema', lambda: True)
    with driver.session() as session:
        session.run('''
            CREATE (u:User {id:$scope, test_scope:$scope}),
                   (other:User {id:$other, test_scope:$scope}),
                   (c:Conversation {id:$cid, test_scope:$scope}),
                   (oc:Conversation {id:$ocid, test_scope:$scope}),
                   (a:Topic {name:$a, test_scope:$scope}),
                   (b:Topic {name:$b, test_scope:$scope}),
                   (old:Message {id:$old, text:'파란책방은 연남동에 있어요. Blue Bookshop is in Yeonnam-dong.', role:'user', lang:'en', ts:datetime('2025-01-01T00:00:00Z'), test_scope:$scope}),
                   (recent:Message {id:$recent, text:'I bought coffee today.', role:'user', lang:'en', ts:datetime('2025-01-02T00:00:00Z'), test_scope:$scope}),
                   (private:Message {id:$private, text:'Blue Bookshop is my private memory.', role:'user', lang:'en', ts:datetime(), test_scope:$scope}),
                   (u)-[:HAD]->(c), (other)-[:HAD]->(oc),
                   (c)-[:CONTAINS]->(old), (c)-[:CONTAINS]->(recent), (oc)-[:CONTAINS]->(private),
                   (c)-[:ABOUT]->(a), (c)-[:ABOUT]->(b), (oc)-[:ABOUT]->(a),
                   (a)-[:RELATED_TO]->(b)
        ''', scope=scope, other=scope+'-other', cid=scope+'-conversation', ocid=scope+'-other-conversation', a=scope+'/books', b=scope+'/reading', old=scope+'-old', recent=scope+'-recent', private=scope+'-private').consume()
    try:
        yield scope
    finally:
        with driver.session() as session:
            session.run('MATCH (n) WHERE n.test_scope=$scope OR n.id STARTS WITH $scope OR n.name STARTS WITH $scope OR n.conversation_id STARTS WITH $scope DETACH DELETE n', scope=scope).consume()
        driver.close()


@pytest.fixture
def dense_graph(graph):
    with recall.get_driver().session() as session:
        session.run('''
            MATCH (u:User {id:$scope})-[:HAD]->(:Conversation)-[:ABOUT]->(seed:Topic {name:$scope+'/books'})
            MATCH (other:Conversation {id:$scope+'-other-conversation'})
            CREATE (near:Topic {name:$scope+'/z-near', test_scope:$scope}),
                   (far:Topic {name:$scope+'/a-far', test_scope:$scope}),
                   (third:Topic {name:$scope+'/third', test_scope:$scope}),
                   (fourth:Topic {name:$scope+'/fourth', test_scope:$scope}),
                   (foreign:Topic {name:$scope+'/foreign', test_scope:$scope}),
                   (near)-[:RELATED_TO]->(far)-[:RELATED_TO]->(third)-[:RELATED_TO]->(fourth),
                   (other)-[:ABOUT]->(foreign)-[:RELATED_TO]->(fourth),
                   (u)-[:HAD]->(past:Conversation {id:$scope+'-past', test_scope:$scope}),
                   (u)-[:HAD]->(deep:Conversation {id:$scope+'-deep', test_scope:$scope}),
                   (u)-[:HAD]->(beyond:Conversation {id:$scope+'-beyond', test_scope:$scope}),
                   (past)-[:ABOUT]->(near), (past)-[:ABOUT]->(far),
                   (deep)-[:ABOUT]->(third), (beyond)-[:ABOUT]->(fourth)
            WITH seed, near, past, deep, beyond
            UNWIND [
                {c:past, id:'tie-b', role:'assistant', day:1},
                {c:past, id:'tie-a', role:'user', day:1},
                {c:past, id:'system', role:'system', day:2},
                {c:deep, id:'third-hop', role:'user', day:2},
                {c:beyond, id:'fourth-hop', role:'user', day:2}
            ] AS item
            WITH seed, near, item, item.c AS c
            CREATE (c)-[:CONTAINS]->(:Message {id:$scope+'-'+item.id, text:item.id, role:item.role,
                   lang:'en', ts:datetime({year:2025, month:1, day:item.day}), test_scope:$scope})
            WITH DISTINCT seed, near
            UNWIND range(1, 8) AS i
            CREATE (bridge:Topic {name:$scope+'/bridge-'+toString(i), test_scope:$scope})
            WITH seed, near, collect(bridge) AS bridges
            WITH [seed, near] + bridges AS clique
            UNWIND range(0, size(clique)-2) AS i
            UNWIND range(i+1, size(clique)-1) AS j
            WITH clique[i] AS a, clique[j] AS b
            CREATE (a)-[:RELATED_TO]->(b)
        ''', scope=graph).consume()
    return graph


def test_dense_paths_keep_minimum_hops_topic_order_and_scope(dense_graph):
    scope = dense_graph
    rows = recall.recall_related(
        scope, [scope+'/books', scope+'/reading', scope+'/foreign'],
        current_conversation_id=scope+'-conversation', raise_errors=True,
    )
    assert [(row['message_id'], row['hops'], row['topic']) for row in rows] == [
        (scope+'-tie-a', 1, scope+'/a-far'),
        (scope+'-tie-b', 1, scope+'/a-far'),
        (scope+'-third-hop', 3, scope+'/third'),
    ]


@pytest.mark.parametrize('question', ['Which neighborhood is Blue Bookshop in?', '파란책방은 어느 동네에 있나요?'])
def test_text_match_recovers_older_evidence_without_matching_topics(graph, question):
    rows = recall.recall_related(graph, ['unmatched-topic'], query_text=question, limit=1, raise_errors=True)
    assert [row['message_id'] for row in rows] == [graph+'-old']
    assert recall.recall_related(graph, [], query_text=question, current_conversation_id=graph+'-conversation', raise_errors=True) == []


def test_relevance_precedes_recency_with_graph_candidates(graph):
    rows = recall.recall_related(graph, [graph+'/books'], query_text='Where is Blue Bookshop?', limit=1, raise_errors=True)
    assert rows[0]['message_id'] == graph+'-old'


def test_replayed_projection_preserves_timestamps_timeline_and_topic_weights(graph, monkeypatch):
    driver=recall.get_driver()
    monkeypatch.setattr(ingest,'get_driver',lambda:driver)
    monkeypatch.setattr(ingest,'ensure_schema',lambda:True)
    cid=graph+'-sync'
    start=datetime(2025,1,1,tzinfo=timezone.utc)
    def write(index,previous):
        return ingest.ingest_turn(conversation_id=cid,user_id=graph,user_msg_id=f'{cid}-u{index}',
            assistant_msg_id=f'{cid}-a{index}',user_text='source',ai_text='reply',language='en',
            previous_message_id=previous,user_created_at=start+timedelta(seconds=index*2),
            assistant_created_at=start+timedelta(seconds=index*2+1))
    assert write(1,None)
    assert write(2,f'{cid}-a1')
    assert write(1,None)
    for _ in range(2):
        assert ingest.ingest_topics(conversation_id=cid,topics=[graph+'/new-a',graph+'/new-b'],event_id=graph+'-event')
    with driver.session() as session:
        edges=[tuple(row.values()) for row in session.run('MATCH (c:Conversation {id:$cid})-[:CONTAINS]->(a)-[:NEXT]->(b) RETURN a.id,b.id',cid=cid)]
        assert set(edges)=={(f'{cid}-u1',f'{cid}-a1'),(f'{cid}-a1',f'{cid}-u2'),(f'{cid}-u2',f'{cid}-a2')}
        ts=session.run('MATCH (m:Message {id:$id}) RETURN m.ts AS ts',id=f'{cid}-u1').single()['ts']
        assert ts.to_native()==start+timedelta(seconds=2)
        assert session.run('MATCH (:Topic {name:$a})-[r:RELATED_TO]-(:Topic {name:$b}) RETURN r.weight AS weight',a=graph+'/new-a',b=graph+'/new-b').single()['weight']==1


def test_delete_tombstone_fences_late_upsert(graph, monkeypatch):
    monkeypatch.setattr(ingest, 'get_driver', recall.get_driver)
    monkeypatch.setattr(ingest, 'ensure_schema', lambda: True)
    cid = graph + '-deleted'
    assert ingest.delete_conversation_memory(conversation_id=cid, user_id=graph)
    assert ingest.ingest_turn(conversation_id=cid, user_id=graph,
        user_msg_id=cid+'-u', assistant_msg_id=cid+'-a',
        user_text='private', ai_text='reply', language='en')
    with recall.get_driver().session() as session:
        assert session.run('MATCH (c:Conversation {id:$cid}) RETURN count(c) AS n', cid=cid).single()['n'] == 0


def test_graph_ablation_keeps_text_retrieval_and_owner_scope(graph):
    rows=recall.recall_related(graph,[graph+'/books'],query_text='Where is Blue Bookshop?',use_graph=False,raise_errors=True)
    assert rows and rows[0]['message_id']==graph+'-old'
    assert graph+'-private' not in {r['message_id'] for r in rows}
