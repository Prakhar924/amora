#!/usr/bin/env bash
# Container entrypoint: wait for Postgres, migrate, optionally seed, then exec
# whatever command was passed (uvicorn by default).
set -euo pipefail

PGHOST="${POSTGRES_HOST:-postgres}"
PGPORT="${POSTGRES_PORT:-5432}"

echo "[entrypoint] waiting for postgres at ${PGHOST}:${PGPORT}..."
for attempt in $(seq 1 60); do
  if python - <<'PY'
import os, socket, sys
host = os.environ.get("POSTGRES_HOST", "postgres")
port = int(os.environ.get("POSTGRES_PORT", "5432"))
try:
    with socket.create_connection((host, port), timeout=2):
        sys.exit(0)
except OSError:
    sys.exit(1)
PY
  then
    echo "[entrypoint] postgres is accepting connections"
    break
  fi
  if [ "${attempt}" -eq 60 ]; then
    echo "[entrypoint] postgres did not become ready in time" >&2
    exit 1
  fi
  sleep 1
done

echo "[entrypoint] applying database migrations"
alembic upgrade head

# Opt-in seeding, so restarting a dev stack does not keep re-seeding.
if [ "${SEED_ON_START:-false}" = "true" ]; then
  echo "[entrypoint] seeding development data"
  python -m app.seed || echo "[entrypoint] seeding skipped or already applied"
fi

echo "[entrypoint] starting: $*"
exec "$@"
