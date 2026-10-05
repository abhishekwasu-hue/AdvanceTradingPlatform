#!/usr/bin/env sh
# Phase AX: off-site copy of the backup volume and the WAL archive with rclone (the compose
# `offsite` service in docker-compose.prod.yml). POSIX sh: the rclone image has no bash.
#
#   OFFSITE_REMOTE            rclone remote and bucket, e.g. spaces:atp-backups (the remote named
#                             "spaces" is configured through RCLONE_CONFIG_SPACES_* variables)
#   OFFSITE_INTERVAL_SECONDS  default 3600
#   --once                    one pass, exit code = result (for a manual run or a restore drill)
#
# Dumps are *copied* (an off-site file is never deleted by local retention or a mistake on the
# host); the WAL archive is *synced* so it follows the base-backup pruning and stays bounded.
set -eu
REMOTE="${OFFSITE_REMOTE:?OFFSITE_REMOTE not set (e.g. spaces:atp-backups)}"
INTERVAL="${OFFSITE_INTERVAL_SECONDS:-3600}"
log() { printf '%s offsite: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }

BACKUPS="${BACKUP_DIR:-/backups}"
WAL="${WAL_ARCHIVE_DIR:-/wal_archive}"

sync_once() {
  rclone copy "$BACKUPS" "$REMOTE/backups" --fast-list --transfers 4 -q || return 1
  if [ -d "$WAL" ]; then rclone sync "$WAL" "$REMOTE/wal_archive" --fast-list --transfers 4 -q || return 1; fi
  return 0
}

if [ "${1:-}" = "--once" ]; then
  sync_once && log "synced to $REMOTE"
  exit $?
fi
log "off-site copy every ${INTERVAL}s to $REMOTE"
sleep "${OFFSITE_INITIAL_DELAY_SECONDS:-120}"
while :; do
  if sync_once; then log "synced to $REMOTE"; else log "sync FAILED - will retry in ${INTERVAL}s"; fi
  sleep "$INTERVAL"
done
