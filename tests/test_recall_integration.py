"""Exercise retrieval against an explicitly selected disposable Neo4j database."""
import os
import uuid

import pytest
from neo4j import GraphDatabase

from app.graph_db import recall


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
                   (old:Message {id:$old, text:'Blue Bookshop is in Yeonnam-dong.', role:'user', lang:'en', ts:datetime('2025-01-01T00:00:00Z'), test_scope:$scope}),
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
            session.run('MATCH (n {test_scope:$scope}) DETACH DELETE n', scope=scope).consume()
        driver.close()


def test_paths_do_not_duplicate_messages_or_mix_users(graph):
    rows = recall.recall_related(graph, [graph+'/books', graph+'/reading'], raise_errors=True)
    ids = [row['message_id'] for row in rows]
    assert len(ids) == len(set(ids)) == 2
    assert graph+'-private' not in ids
    assert recall.recall_related(graph, [graph+'/books'], current_conversation_id=graph+'-conversation', raise_errors=True) == []
