"""대화 turn 별 Neo4j 적재.

매 turn 마다 User/Conversation/Message 노드 + NEXT 시간선 + Expression 관계를 upsert.
Topic 노드는 ending graph (Phase F) 에서 추가.
"""

from __future__ import annotations

import logging
from datetime import datetime

from app.graph_db.neo4j_client import ensure_schema, get_driver

log = logging.getLogger(__name__)


def _writable(tx, conversation_id: str) -> bool:
    # The write acquires a Neo4j lock shared with deletion, including absent conversations.
    row = tx.run("MERGE (f:GraphConversationFence {id:$cid}) "
                 "ON CREATE SET f.deleted=false "
                 "SET f.lock=coalesce(f.lock,0)+1 RETURN f.deleted AS deleted",
                 cid=conversation_id).single()
    return not row['deleted']


def _ingest_tx(
    tx,
    *,
    conversation_id: str,
    user_id: str,
    user_msg_id: str,
    user_text: str,
    language: str | None,
    assistant_msg_id: str,
    ai_text: str,
    english_expression: str | None,
    better_expression: str | None,
    previous_message_id: str | None = None,
    user_created_at: datetime | None = None,
    assistant_created_at: datetime | None = None,
) -> None:
    if not _writable(tx, conversation_id):
        return
    tx.run(
        """
        MERGE (u:User {id: $uid})
        MERGE (c:Conversation {id: $cid})
          ON CREATE SET c.started_at = datetime()
        MERGE (u)-[:HAD]->(c)
        """,
        uid=user_id,
        cid=conversation_id,
    )

    last = None if user_created_at is not None else tx.run(
        """
        MATCH (c:Conversation {id: $cid})-[:CONTAINS]->(m:Message)
        WITH m ORDER BY m.ts DESC LIMIT 1
        RETURN m.id AS id
        """,
        cid=conversation_id,
    ).single()
    last_id = previous_message_id if user_created_at is not None else last["id"] if last else None
    if last_id in {user_msg_id, assistant_msg_id}:
        last_id = None

    tx.run(
        """
        MERGE (m:Message {id: $mid})
          ON CREATE SET m.ts = coalesce($source_ts, datetime())
          SET m.role = 'user', m.lang = $lang, m.text = $text
        WITH m
        MATCH (c:Conversation {id: $cid})
        MERGE (c)-[:CONTAINS]->(m)
        """,
        mid=user_msg_id,
        cid=conversation_id,
        lang=language,
        text=user_text,
        source_ts=user_created_at,
    )

    if last_id:
        tx.run(
            "MATCH (a:Message {id: $aid}), (b:Message {id: $bid}) MERGE (a)-[:NEXT]->(b)",
            aid=last_id,
            bid=user_msg_id,
        )

    if english_expression:
        tx.run(
            """
            MERGE (e:Expression {text: $etext, lang: 'en'})
              ON CREATE SET e.kind = 'translation'
            WITH e
            MATCH (m:Message {id: $mid})
            MERGE (m)-[:LEARNED]->(e)
            """,
            etext=english_expression,
            mid=user_msg_id,
        )

    tx.run(
        """
        MERGE (m:Message {id: $mid})
          ON CREATE SET m.ts = coalesce($source_ts, datetime())
          SET m.role = 'assistant', m.lang = $lang, m.text = $text
        WITH m
        MATCH (c:Conversation {id: $cid})
        MERGE (c)-[:CONTAINS]->(m)
        """,
        mid=assistant_msg_id,
        cid=conversation_id,
        lang=language,
        text=ai_text,
        source_ts=assistant_created_at,
    )

    tx.run(
        "MATCH (a:Message {id: $aid}), (b:Message {id: $bid}) MERGE (a)-[:NEXT]->(b)",
        aid=user_msg_id,
        bid=assistant_msg_id,
    )

    if better_expression:
        tx.run(
            """
            MERGE (e:Expression {text: $etext, lang: 'en'})
              ON CREATE SET e.kind = 'rewrite'
            WITH e
            MATCH (m:Message {id: $mid})
            MERGE (m)-[:LEARNED]->(e)
            """,
            etext=better_expression,
            mid=assistant_msg_id,
        )


def ingest_turn(
    *,
    conversation_id: str,
    user_id: str,
    user_msg_id: str,
    user_text: str,
    language: str | None,
    assistant_msg_id: str,
    ai_text: str,
    english_expression: str | None = None,
    better_expression: str | None = None,
    previous_message_id: str | None = None,
    user_created_at: datetime | None = None,
    assistant_created_at: datetime | None = None,
) -> bool:
    if not ensure_schema():
        return False
    try:
        with get_driver().session() as s:
            s.execute_write(
                _ingest_tx,
                conversation_id=conversation_id,
                user_id=user_id,
                user_msg_id=user_msg_id,
                user_text=user_text,
                language=language,
                assistant_msg_id=assistant_msg_id,
                ai_text=ai_text,
                english_expression=english_expression,
                better_expression=better_expression,
                previous_message_id=previous_message_id,
                user_created_at=user_created_at,
                assistant_created_at=assistant_created_at,
            )
        return True
    except Exception as exc:
        log.warning("ingest_turn skipped: %s", exc)
        return False


def _ingest_topics_tx(tx, conversation_id: str, topics: list[str], event_id: str | None = None) -> None:
    if not _writable(tx, conversation_id):
        return
    if event_id:
        marker = tx.run('MERGE (e:GraphSyncMarker {id:$id}) ON CREATE SET e.complete=false, e.conversation_id=$cid RETURN e.complete AS complete',
                        id=event_id, cid=conversation_id).single()
        if marker['complete']:
            return
    for name in topics:
        tx.run(
            """
            MERGE (t:Topic {name: $name})
            WITH t
            MATCH (c:Conversation {id: $cid})
            MERGE (c)-[:ABOUT]->(t)
            """,
            name=name,
            cid=conversation_id,
        )
    for i, a in enumerate(topics):
        for b in topics[i + 1 :]:
            tx.run(
                """
                MATCH (x:Topic {name: $a}), (y:Topic {name: $b})
                MERGE (x)-[r:RELATED_TO]-(y)
                  ON CREATE SET r.weight = 1
                  ON MATCH  SET r.weight = r.weight + 1
                """,
                a=a,
                b=b,
            )
    if event_id:
        tx.run('MATCH (e:GraphSyncMarker {id:$id}) SET e.complete=true', id=event_id)


def ingest_topics(*, conversation_id: str, topics: list[str], event_id: str | None = None) -> bool:
    cleaned = [t.strip().lower() for t in topics if t and t.strip()]
    if not cleaned or not ensure_schema():
        return False
    try:
        with get_driver().session() as s:
            s.execute_write(_ingest_topics_tx, conversation_id, cleaned, event_id)
        return True
    except Exception as exc:
        log.warning("ingest_topics skipped: %s", exc)
        return False


def _delete_conversation_tx(tx, conversation_id: str, user_id: str) -> None:
    tx.run("MERGE (f:GraphConversationFence {id:$cid}) SET f.deleted=true", cid=conversation_id).consume()
    # 소유 사용자를 함께 match해 다른 사용자의 그래프를 잘못 삭제하지 않는다.
    tx.run(
        """
        MATCH (:User {id: $uid})-[:HAD]->(c:Conversation {id: $cid})
              -[:CONTAINS]->(m:Message)
        DETACH DELETE m
        """,
        uid=user_id,
        cid=conversation_id,
    )
    tx.run(
        """
        MATCH (:User {id: $uid})-[:HAD]->(c:Conversation {id: $cid})
        DETACH DELETE c
        """,
        uid=user_id,
        cid=conversation_id,
    )
    tx.run("MATCH (t:Topic) WHERE NOT (t)<-[:ABOUT]-() DETACH DELETE t")
    tx.run("MATCH (e:Expression) WHERE NOT ()-[:LEARNED]->(e) DETACH DELETE e")
    tx.run('MATCH (e:GraphSyncMarker {conversation_id:$cid}) DETACH DELETE e', cid=conversation_id)


def delete_conversation_memory(*, conversation_id: str, user_id: str) -> bool:
    """Postgres 대화 삭제 후 대응하는 Neo4j 노드와 고아 메모리를 정리한다."""
    if not ensure_schema():
        return False
    try:
        with get_driver().session() as s:
            s.execute_write(_delete_conversation_tx, conversation_id, user_id)
        return True
    except Exception as exc:
        log.warning("delete_conversation_memory skipped: %s", exc)
        return False
