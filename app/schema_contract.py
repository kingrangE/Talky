"""Required SQL structure; additive migrations may coexist with older releases."""
from sqlalchemy import inspect
from app.db.models import Base


def check_schema(connection):
    inspector = inspect(connection)
    for table in Base.metadata.sorted_tables:
        columns = {column['name']: column for column in inspector.get_columns(table.name)}
        for required in table.columns:
            actual = columns.get(required.name)
            if actual is None:
                raise RuntimeError(f'database schema missing {table.name}.{required.name}')
            expected_type = required.type.compile(dialect=connection.dialect)
            actual_type = actual['type'].compile(dialect=connection.dialect)
            if actual_type != expected_type:
                raise RuntimeError(f'database schema incompatible {table.name}.{required.name}')
