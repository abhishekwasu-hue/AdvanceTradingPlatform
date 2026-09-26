#!/usr/bin/env sh
# Entrypoint of the compose `backup` service: a backup every BACKUP_INTERVAL_SECONDS (default
# daily), first one shortly after start. Deliberately a loop, not cron - nothing else to install
# in the postgres image, and a crash restarts with the container.
. "$(dirname "$0")/common.sh"
INTERVAL="${BACKUP_INTERVAL_SECONDS:-86400}"
log "scheduled backups every ${INTERVAL}s into $BACKUP_DIR (retention ${BACKUP_RETENTION_DAYS}d, keep >= ${BACKUP_KEEP_MIN})"
sleep "${BACKUP_INITIAL_DELAY_SECONDS:-30}"
while :; do
  if ! "$(dirname "$0")/backup.sh"; then log "backup FAILED - will retry at the next interval"; fi
  sleep "$INTERVAL"
done
