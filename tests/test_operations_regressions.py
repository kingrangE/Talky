from types import SimpleNamespace

from app.graph_db import neo4j_client


def test_schema_initialization_retries_after_database_recovers(monkeypatch):
    calls = []

    class Session:
        def __enter__(self):
            calls.append(1)
            if len(calls) == 1:
                raise ConnectionError("temporary outage")
            return self

        def __exit__(self, *_args):
            pass

        def run(self, _query):
            return SimpleNamespace(consume=lambda: None)

    neo4j_client._initialize_schema.cache_clear()
    monkeypatch.setattr(neo4j_client, "get_driver", lambda: SimpleNamespace(session=Session))
    try:
        assert neo4j_client.ensure_schema() is False
        assert neo4j_client.ensure_schema() is True
        assert neo4j_client.ensure_schema() is True
        assert len(calls) == 2
    finally:
        neo4j_client._initialize_schema.cache_clear()
