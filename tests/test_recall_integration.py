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


def test_paths_do_not_duplicate_messages_or_mix_users(graph):
    rows = recall.recall_related(graph, [graph+'/books', graph+'/reading'], raise_errors=True)
    ids = [row['message_id'] for row in rows]
    assert len(ids) == len(set(ids)) == 2
    assert graph+'-private' not in ids
    assert recall.recall_related(graph, [graph+'/books'], current_conversation_id=graph+'-conversation', raise_errors=True) == []


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
