"""Run after migrations as the DB owner; the web runtime receives DML rights only."""
import os
import re
from psycopg import sql
from app.db.postgres import get_engine


def main():
    name = os.environ['RUNTIME_DB_USER']
    password = os.environ['RUNTIME_DB_PASSWORD']
    if not re.fullmatch(r'talky_runtime', name) or not password:
        raise ValueError('expected a dedicated talky_runtime role and a nonempty password')
    engine = get_engine()
    connection = engine.raw_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (name,))
            verb = 'ALTER' if cursor.fetchone() else 'CREATE'
            cursor.execute(sql.SQL(verb+' ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}').format(
                sql.Identifier(name), sql.Literal(password)))
            cursor.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(
                sql.Identifier(engine.url.database), sql.Identifier(name)))
            cursor.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
            cursor.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(name)))
            cursor.execute(sql.SQL('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}').format(sql.Identifier(name)))
            cursor.execute(sql.SQL('REVOKE ALL ON TABLE public.alembic_version FROM {}').format(sql.Identifier(name)))
            cursor.execute(sql.SQL('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}').format(sql.Identifier(name)))
        connection.commit()
    finally:
        connection.close()


if __name__ == '__main__':
    main()
