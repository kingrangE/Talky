"""Multi-hop 회상 Cypher.

3-hop 토픽 검색에 사용자별 원문 검색을 보완하고, 질문 관련도와 그래프 거리를
우선해 중복 없는 이전 메시지를 반환한다.
"""

from __future__ import annotations

import logging
import re

from app.graph_db.neo4j_client import ensure_schema, get_driver

log = logging.getLogger(__name__)

_STOP_WORDS = set('a an the is are was were be been do does did i my me you your we our it its this that what which where when who how why in on at to of for and or with from have has had tell remember said say about can could would should please name 은 는 이 가 을 를 의 에 에서 어느 어디 무엇 뭐 무슨 어떤 있나요 인가요 무엇인가요 알려줘 알려주세요 기억 나 내 제가 내가'.split())
_PARTICLES = ('에서는', '으로는', '에게는', '이라고', '이라는', '에서', '으로', '에는', '와의', '은', '는', '이', '가', '을', '를', '의', '에', '와', '과')


def _query_terms(query_text: str, topics: list[str]) -> list[dict]:
    terms = {}
    for text, weight in [(query_text, 2.0)] + [(t.rsplit('/', 1)[-1], 1.0) for t in topics]:
        for token in re.findall(r'[a-z0-9]+|[가-힣]+', text.lower()):
            if token in _STOP_WORDS:
                continue
            if re.fullmatch('[가-힣]+', token):
                for suffix in _PARTICLES:
                    if token.endswith(suffix) and len(token) - len(suffix) >= 2:
                        token = token[:-len(suffix)]
                        break
            if len(token) >= 2 and token not in _STOP_WORDS:
                terms[token] = max(weight, terms.get(token, 0))
    return [{'text': text, 'weight': weight} for text, weight in terms.items()][:32]


def recall_related(
    user_id: str,
    topic_candidates: list[str],
    limit: int = 12,
    current_conversation_id: str | None = None,
    raise_errors: bool = False,
    query_text: str = '',
    use_graph: bool = True,
) -> list[dict]:
    terms = _query_terms(query_text, topic_candidates) if query_text else []
    if not topic_candidates and not terms:
        return []
    if limit < 1:
        return []
    if not ensure_schema():
        if raise_errors:
            raise RuntimeError("Neo4j schema is unavailable")
        return []
    try:
        with get_driver().session() as s:
            rows = []
            if use_graph:
                result = s.run(
                    """
                    MATCH (u:User {id: $uid})-[:HAD]->(:Conversation)-[:ABOUT]->(t:Topic)
                    WHERE t.name IN $topics
                    WITH collect(DISTINCT t) AS seeds
                    UNWIND seeds AS t
                    MATCH path = (t)-[:RELATED_TO*0..3]-(t2:Topic)
                    MATCH (:User {id: $uid})-[:HAD]->(c:Conversation)-[:ABOUT]->(t2)
                    MATCH (c)-[:CONTAINS]->(m:Message)
                    WHERE m.role IN ['user', 'assistant']
                      AND ($current_cid IS NULL OR c.id <> $current_cid)
                    WITH m, c, min(length(path)) AS hops, min(t2.name) AS topic
                    RETURN m.id AS message_id, c.id AS conversation_id, m.text AS text, m.lang AS lang, topic, m.ts AS ts, hops
                    ORDER BY hops ASC, ts DESC, message_id ASC LIMIT $limit
                    """,
                    uid=user_id,
                    topics=topic_candidates,
                    limit=min(max(limit * 5, 60), 200),
                    current_cid=current_conversation_id,
                )
                rows = [dict(r) for r in result]
            if terms:
                # shortcut: user-scoped text scans; add a full-text index when histories outgrow interactive latency.
                result = s.run(
                    """
                    MATCH (:User {id: $uid})-[:HAD]->(c:Conversation)-[:CONTAINS]->(m:Message)
                    WHERE m.role IN ['user', 'assistant']
                      AND ($current_cid IS NULL OR c.id <> $current_cid)
                    WITH c, m, reduce(score=0.0, term IN $terms |
                        score + CASE WHEN toLower(m.text) CONTAINS term.text THEN term.weight ELSE 0.0 END) AS score
                    WHERE score > 0
                    OPTIONAL MATCH (c)-[:ABOUT]->(t:Topic)
                    WITH m, c, score, min(t.name) AS topic
                    RETURN m.id AS message_id, c.id AS conversation_id, m.text AS text, m.lang AS lang,
                           coalesce(topic, 'text match') AS topic, m.ts AS ts, 4 AS hops
                    ORDER BY score DESC, ts DESC, message_id ASC LIMIT $limit
                    """,
                    uid=user_id, current_cid=current_conversation_id, terms=terms,
                    limit=min(max(limit * 5, 60), 200),
                )
                rows.extend(dict(r) for r in result)
            unique = {}
            for row in rows:
                key = row.get('message_id', row['text'])
                if key not in unique or row.get('hops', 4) < unique[key].get('hops', 4):
                    unique[key] = row
            def rank(row):
                text = row['text'].lower()
                score = sum(term['weight'] for term in terms if term['text'] in text)
                return score, -row.get('hops', 4), str(row.get('ts') or '')
            return sorted(unique.values(), key=rank, reverse=True)[:limit]
    except Exception as exc:
        if raise_errors:
            raise
        log.warning("recall_related skipped: %s", exc)
        return []
