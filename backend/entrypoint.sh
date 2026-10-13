#!/usr/bin/env bash
# Container entrypoint: wait for the database, migrate, optionally seed, then
# exec the given command (uvicorn by default).
#
# Works both for the docker-compose stack (discrete POSTGRES_* variables, a
# container that may still be starting) and for managed hosts such as Render
# (a single DATABASE_URL_OVERRIDE pointing at Supabase, already running).
set -euo pipefail

# Render injects $PORT and expects the process to listen on it. Locally it is
# unset and 8000 is used.
export PORT="${PORT:-8000}"

echo "[entrypoint] waiting for the database..."
# Ask the application itself, so this works for any connection style rather
# than only a host/port pair we happen to know about.
python - <<'PY'
import sys
import time

from sqlalchemy import create_engine, text

from app.core.config import settings

# Redact credentials before anything reaches the logs.
url = settings.sync_database_url
display = url.split("@")[-1] if "@" in url else url
print(f"[entrypoint] target: {display}", flush=True)

deadline = time.monotonic() + 120
last_error = None
while time.monotonic() < deadline:
    try:
        engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        print("[entrypoint] database is accepting connections", flush=True)
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        last_error = exc
        time.sleep(2)

print(f"[entrypoint] database did not become ready: {last_error}", file=sys.stderr)
sys.exit(1)
PY

echo "[entrypoint] applying database migrations"
alembic upgrade head

# Opt-in only. `app/seed.py` additionally refuses to run when APP_ENV is
# production, so synthetic profiles cannot reach a real user database.
if [ "${SEED_ON_START:-false}" = "true" ]; then
  echo "[entrypoint] seeding development data"
  python -m app.seed || echo "[entrypoint] seeding skipped or already applied"
fi

echo "[entrypoint] starting: $*"
exec "$@"
