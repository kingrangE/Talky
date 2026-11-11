import pytest
from sqlalchemy import text
from app.schema_contract import check_schema


def test_additive_schema_compatible_but_missing_required_column_rejected(pg_db):
    with pg_db[0].begin() as conn:
        check_schema(conn)
        conn.execute(text('ALTER TABLE users ADD COLUMN future_preference TEXT'))
        check_schema(conn)
        conn.execute(text('ALTER TABLE users DROP COLUMN display_name'))
        with pytest.raises(RuntimeError, match='users.display_name'):
            check_schema(conn)
