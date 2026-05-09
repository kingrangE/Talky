#!/usr/bin/env bash
set -e

echo "[talky] waiting for postgres at ${POSTGRES_HOST}:${POSTGRES_PORT}..."
python - <<'PY'
import os, socket, time, sys
host = os.environ.get("POSTGRES_HOST", "postgres")
port = int(os.environ.get("POSTGRES_PORT", "5432"))
for _ in range(120):
    try:
        with socket.create_connection((host, port), timeout=2):
            sys.exit(0)
    except OSError:
        time.sleep(2)
print("postgres not reachable", file=sys.stderr)
sys.exit(1)
PY

echo "[talky] running migrations..."
alembic upgrade head

echo "[talky] seeding default data..."
python -m app.db.seed

echo "[talky] starting Streamlit..."
exec streamlit run main.py --server.address=0.0.0.0 --server.port=8501
