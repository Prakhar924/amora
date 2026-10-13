#!/usr/bin/env bash
# Deploy Amora to a single Linux host.
#
#   ./deploy/deploy.sh user@your-server.example.com
#
# Copies the repository to the server, builds the image there, applies
# migrations and restarts the stack with zero manual steps. Safe to re-run:
# it is the normal way to ship a change.
#
# Prerequisites on the server (see deploy/provision.sh, which does this):
#   * Docker Engine with the compose plugin
#   * The repository directory, owned by the deploying user
#   * .env.production present in that directory (never copied by this script —
#     secrets are placed once, by hand, and left alone)
set -euo pipefail

TARGET="${1:-}"
REMOTE_DIR="${REMOTE_DIR:-/opt/amora}"
COMPOSE="docker compose -f docker-compose.prod.yml --env-file .env.production"

if [ -z "$TARGET" ]; then
	echo "usage: $0 user@host" >&2
	echo "  e.g. $0 deploy@api.example.com" >&2
	exit 2
fi

log() { printf '\n\033[1;35m==>\033[0m %s\n' "$*"; }

# --- 1. Validate locally before touching the server ------------------------
# A broken config should fail here, not halfway through a restart.
if [ -f .env.production ]; then
	log "Validating local .env.production"
	python deploy/check-config.py .env.production
else
	echo "note: no local .env.production; relying on the copy already on the server"
fi

log "Checking connectivity to $TARGET"
ssh -o BatchMode=yes -o ConnectTimeout=10 "$TARGET" "echo connected; docker --version"

# --- 2. Ship the code ------------------------------------------------------
# rsync rather than git pull: the server needs no deploy key and no network
# access to GitHub, and a dirty working tree deploys exactly as it looks.
log "Syncing repository to $TARGET:$REMOTE_DIR"
ssh "$TARGET" "mkdir -p '$REMOTE_DIR'"
rsync -az --delete \
	--exclude '.git/' \
	--exclude '.env*' \
	--exclude 'node_modules/' \
	--exclude '.venv/' \
	--exclude 'mobile/dist/' \
	--exclude '__pycache__/' \
	--exclude '.pytest_cache/' \
	./ "$TARGET:$REMOTE_DIR/"

# --- 3. Verify the server has its secrets ----------------------------------
log "Checking server configuration"
ssh "$TARGET" "cd '$REMOTE_DIR' && test -f .env.production" || {
	echo "" >&2
	echo "ERROR: $REMOTE_DIR/.env.production is missing on the server." >&2
	echo "Create it once, by hand:" >&2
	echo "  python deploy/generate-secrets.py -o .env.production   # locally" >&2
	echo "  # fill in the REPLACE_ME values, then:" >&2
	echo "  scp .env.production $TARGET:$REMOTE_DIR/" >&2
	echo "  ssh $TARGET 'chmod 600 $REMOTE_DIR/.env.production'" >&2
	exit 1
}
ssh "$TARGET" "cd '$REMOTE_DIR' && chmod 600 .env.production"

# --- 4. Build and start ----------------------------------------------------
log "Building the API image on the server"
ssh "$TARGET" "cd '$REMOTE_DIR' && $COMPOSE build api"

log "Applying database migrations"
# Run migrations as a one-off container before restarting the API, so a
# failed migration never leaves new code running against an old schema.
ssh "$TARGET" "cd '$REMOTE_DIR' && $COMPOSE up -d postgres redis"
ssh "$TARGET" "cd '$REMOTE_DIR' && $COMPOSE run --rm --no-deps --entrypoint '' api alembic upgrade head"

log "Starting the stack"
ssh "$TARGET" "cd '$REMOTE_DIR' && $COMPOSE up -d --remove-orphans"

# --- 5. Verify ------------------------------------------------------------
log "Waiting for the API to report ready"
ssh "$TARGET" "cd '$REMOTE_DIR' && for i in \$(seq 1 30); do
	if $COMPOSE exec -T api curl -fsS http://localhost:8000/health/ready >/dev/null 2>&1; then
		echo 'API is ready'; exit 0
	fi
	sleep 3
done
echo 'ERROR: API did not become ready in 90s' >&2
$COMPOSE logs --tail 60 api >&2
exit 1"

log "Dependency status"
ssh "$TARGET" "cd '$REMOTE_DIR' && $COMPOSE exec -T api curl -fsS http://localhost:8000/health/ready"

log "Stack status"
ssh "$TARGET" "cd '$REMOTE_DIR' && $COMPOSE ps"

DOMAIN=$(ssh "$TARGET" "cd '$REMOTE_DIR' && grep -E '^API_DOMAIN=' .env.production | cut -d= -f2" || true)
log "Deployed."
if [ -n "$DOMAIN" ]; then
	echo "  API:       https://$DOMAIN"
	echo "  Health:    https://$DOMAIN/health"
	echo "  WebSocket: wss://$DOMAIN/ws/chat"
	echo ""
	echo "If this is the first deploy, Caddy needs a minute to obtain a"
	echo "certificate. Confirm with:  curl -I https://$DOMAIN/health"
fi
