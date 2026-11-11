"""Web readiness: the process is live and its database schema matches this release."""
import urllib.request
import sys
import time
from pathlib import Path
from app.settings import get_settings
from app.schema_contract import check_schema
from app.db.postgres import get_engine


def main():
    if len(sys.argv) > 1:
        role = sys.argv[1]
        if role not in {'worker', 'graph'}:
            raise ValueError('unknown health role')
        age = time.time() - Path(f'/tmp/talky-{role}-heartbeat').stat().st_mtime
        if age > get_settings().MOCK_EXAM_JOB_TIMEOUT_SECONDS + 60:
            raise RuntimeError('worker heartbeat expired')
        return
    with get_engine().connect() as connection:
        check_schema(connection)
    urllib.request.urlopen('http://localhost:8501/_stcore/health', timeout=3).close()


if __name__ == '__main__':
    main()
