"""Serialize the release migration and runtime grants across deployment runners."""
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from app.db.postgres import get_engine
from app.db.runtime_role import main as grant_runtime_role


def main():
    with get_engine().connect() as connection:
        if not connection.scalar(text('SELECT pg_try_advisory_lock(74201939)')):
            raise RuntimeError('another release migration is running')
        connection.commit()
        try:
            command.upgrade(Config('alembic.ini'), 'head')
            grant_runtime_role()
        finally:
            connection.execute(text('SELECT pg_advisory_unlock(74201939)'))
            connection.commit()


if __name__ == '__main__':
    main()
