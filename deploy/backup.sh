#!/bin/sh
# Nightly PostgreSQL backup loop.
#
# Runs inside a postgres:16-alpine container that shares the Docker network
# with the database, so it needs no exposed port. Dumps are written to a
# named volume and pruned after BACKUP_RETENTION_DAYS.
#
# IMPORTANT: a backup on the same host as the database protects against
# "someone dropped a table", not against "the VPS died". To be a real backup
# this must be copied off the machine — set S3_BACKUP_* below and the dump is
# also uploaded to object storage.
set -eu

BACKUP_DIR=/backups
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"
# Seconds between runs. 86400 = daily.
INTERVAL="${BACKUP_INTERVAL_SECONDS:-86400}"

mkdir -p "$BACKUP_DIR"

log() {
	echo "[backup] $(date -u '+%Y-%m-%dT%H:%M:%SZ') $*"
}

take_backup() {
	stamp=$(date -u '+%Y%m%dT%H%M%SZ')
	target="$BACKUP_DIR/amora-$stamp.sql.gz"

	log "dumping $PGDATABASE -> $(basename "$target")"
	# --clean --if-exists makes the dump restorable over an existing database.
	if pg_dump --clean --if-exists --no-owner --no-privileges \
		| gzip -9 > "$target.partial"; then
		# Rename only on success, so a half-written dump is never mistaken
		# for a good one.
		mv "$target.partial" "$target"
		log "wrote $(du -h "$target" | cut -f1)"
	else
		log "ERROR: pg_dump failed"
		rm -f "$target.partial"
		return 1
	fi

	# Optional off-site copy. Requires the aws CLI in the image, which the
	# stock postgres image does not have — see deploy/README for the variant
	# that does, or run this step from the host instead.
	if [ -n "${S3_BACKUP_BUCKET:-}" ] && command -v aws >/dev/null 2>&1; then
		log "uploading to s3://$S3_BACKUP_BUCKET/"
		aws s3 cp "$target" "s3://$S3_BACKUP_BUCKET/$(basename "$target")" \
			${S3_BACKUP_ENDPOINT:+--endpoint-url "$S3_BACKUP_ENDPOINT"} \
			|| log "WARNING: off-site upload failed; local copy kept"
	fi

	log "pruning backups older than ${RETENTION_DAYS}d"
	find "$BACKUP_DIR" -name 'amora-*.sql.gz' -mtime "+$RETENTION_DAYS" -delete
}

log "backup loop started (every ${INTERVAL}s, keeping ${RETENTION_DAYS}d)"

while true; do
	take_backup || log "backup run failed; will retry next cycle"
	sleep "$INTERVAL"
done
