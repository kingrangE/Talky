"""Multi-hop 회상 Cypher.

현재 입력의 토픽 후보가 주어지면, 사용자가 가졌던 대화들에서 같은 토픽 또는
2-hop 까지의 RELATED_TO 토픽이 등장한 메시지를 시간 역순으로 가져온다.
"""

from __future__ import annotations

import logging

from app.graph_db.neo4j_client import ensure_schema, get_driver

log = logging.getLogger(__name__)


def recall_related(
    user_id: str, topic_candidates: list[str], limit: int = 12
) -> list[dict]:
    if not topic_candidates or not ensure_schema():
        return []
    try:
        with get_driver().session() as s:
            result = s.run(
                """
                MATCH (u:User {id: $uid})-[:HAD]->(:Conversation)-[:ABOUT]->(t:Topic)
                WHERE t.name IN $topics
                WITH collect(DISTINCT t) AS seeds
                UNWIND seeds AS t
                MATCH (t)-[:RELATED_TO*0..2]-(t2:Topic)<-[:ABOUT]-(c:Conversation)
                      -[:CONTAINS]->(m:Message)
                WHERE m.role IN ['user', 'assistant']
                RETURN m.text AS text, m.lang AS lang, t2.name AS topic, m.ts AS ts
                ORDER BY ts DESC LIMIT $limit
                """,
                uid=user_id,
                topics=topic_candidates,
                limit=limit,
            )
            return [dict(r) for r in result]
    except Exception as exc:
        log.warning("recall_related skipped: %s", exc)
        return []
