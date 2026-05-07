"""대화 turn 별 Neo4j 적재.

매 turn 마다 User/Conversation/Message 노드 + NEXT 시간선 + Expression 관계를 upsert.
Topic 노드는 ending graph (Phase F) 에서 추가.
"""

from __future__ import annotations

import logging

from app.graph_db.neo4j_client import ensure_schema, get_driver

log = logging.getLogger(__name__)


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
) -> None:
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

    last = tx.run(
        """
        MATCH (c:Conversation {id: $cid})-[:CONTAINS]->(m:Message)
        WITH m ORDER BY m.ts DESC LIMIT 1
        RETURN m.id AS id
        """,
        cid=conversation_id,
    ).single()
    last_id = last["id"] if last else None

    tx.run(
        """
        MERGE (m:Message {id: $mid})
          SET m.role = 'user', m.lang = $lang, m.text = $text, m.ts = datetime()
        WITH m
        MATCH (c:Conversation {id: $cid})
        MERGE (c)-[:CONTAINS]->(m)
        """,
        mid=user_msg_id,
        cid=conversation_id,
        lang=language,
        text=user_text,
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
          SET m.role = 'assistant', m.lang = $lang, m.text = $text, m.ts = datetime()
        WITH m
        MATCH (c:Conversation {id: $cid})
        MERGE (c)-[:CONTAINS]->(m)
        """,
        mid=assistant_msg_id,
        cid=conversation_id,
        lang=language,
        text=ai_text,
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
            )
        return True
    except Exception as exc:
        log.warning("ingest_turn skipped: %s", exc)
        return False


def _ingest_topics_tx(tx, conversation_id: str, topics: list[str]) -> None:
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


def ingest_topics(*, conversation_id: str, topics: list[str]) -> bool:
    cleaned = [t.strip().lower() for t in topics if t and t.strip()]
    if not cleaned or not ensure_schema():
        return False
    try:
        with get_driver().session() as s:
            s.execute_write(_ingest_topics_tx, conversation_id, cleaned)
        return True
    except Exception as exc:
        log.warning("ingest_topics skipped: %s", exc)
        return False
