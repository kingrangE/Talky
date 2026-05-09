"""Neo4j 드라이버와 스키마 초기화."""

from __future__ import annotations

import logging
from functools import lru_cache

from neo4j import Driver, GraphDatabase

from app.settings import get_settings

log = logging.getLogger(__name__)

_CONSTRAINTS = [
    "CREATE CONSTRAINT user_id IF NOT EXISTS FOR (u:User) REQUIRE u.id IS UNIQUE",
    "CREATE CONSTRAINT conv_id IF NOT EXISTS FOR (c:Conversation) REQUIRE c.id IS UNIQUE",
    "CREATE CONSTRAINT msg_id IF NOT EXISTS FOR (m:Message) REQUIRE m.id IS UNIQUE",
    "CREATE INDEX topic_name IF NOT EXISTS FOR (t:Topic) ON (t.name)",
    "CREATE INDEX expr_text IF NOT EXISTS FOR (e:Expression) ON (e.text)",
]


@lru_cache(maxsize=1)
def get_driver() -> Driver:
    cfg = get_settings()
    return GraphDatabase.driver(
        cfg.NEO4J_URI, auth=(cfg.NEO4J_USER, cfg.NEO4J_PASSWORD)
    )


@lru_cache(maxsize=1)
def ensure_schema() -> bool:
    """앱 첫 호출 시 1회만 제약/인덱스 생성. Neo4j 미접속이면 False."""
    try:
        with get_driver().session() as s:
            for stmt in _CONSTRAINTS:
                s.run(stmt)
        return True
    except Exception as exc:
        log.warning("Neo4j schema init skipped: %s", exc)
        return False
