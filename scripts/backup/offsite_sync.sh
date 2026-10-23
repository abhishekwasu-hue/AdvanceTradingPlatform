#!/usr/bin/env sh
# Phase AX: off-site copy of the backup volume with rclone (the compose `offsite` service in
# docker-compose.prod.yml). POSIX sh: the rclone image has no bash.
#
#   OFFSITE_REMOTE            rclone remote and bucket, e.g. offsite:atp-backups (the remote named
#                             "offsite" is configured through RCLONE_CONFIG_OFFSITE_* variables)
#   OFFSITE_INTERVAL_SECONDS  default 3600
#   --once                    one pass, exit code = result (for a manual run or a restore drill)
#
# Dumps are *copied*: an off-site file is never deleted by local retention or a mistake on the host. What leaves the
# host is encrypted and sealed only (A1):
#   /backups/atp_*.dump.enc (+ .sha256, .hmac)           -> backups/
#   /backups/pre-migration/atp_*.dump.enc (+ sidecars)   -> backups/pre-migration/
#   /backups/base/<stamp>/*.enc (+ sidecars, start_wal)  -> backups/base/<stamp>/   (point-in-time recovery only)
#   /backups/weekly/, /backups/monthly/ (the tiers)      -> backups-weekly/, backups-monthly/ (own prefixes)
# A plain .dump (no BACKUP_ENCRYPTION_PASSPHRASE) is never copied. The WAL archive is never copied either (review R1:
# it is raw database pages); point-in-time recovery keeps it on the host, and the off-site recovery point is the newest
# encrypted dump. Old objects off-site are removed by offsite_prune.sh (deploy/atp.sh backup), never by this copy.
set -eu
REMOTE="${OFFSITE_REMOTE:?OFFSITE_REMOTE not set (e.g. offsite:atp-backups)}"
INTERVAL="${OFFSITE_INTERVAL_SECONDS:-3600}"
log() { printf '%s offsite: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }

BACKUPS="${BACKUP_DIR:-/backups}"
SEALED='atp_*.dump.enc{,.sha256,.hmac}'

sync_once() {
  # A leading "/" anchors a filter at the source root (without it a pattern matches in every directory).
  rclone copy "$BACKUPS" "$REMOTE/backups" --fast-list --transfers 4 -q \
    --include "/$SEALED" --include "/pre-migration/$SEALED" \
    --include '/base/2*/*.enc{,.sha256,.hmac}' --include '/base/2*/start_wal' || return 1
  for tier in weekly monthly; do
    if [ -d "$BACKUPS/$tier" ]; then
      rclone copy "$BACKUPS/$tier" "$REMOTE/backups-$tier" --fast-list --transfers 4 -q --include "/$SEALED" || return 1
    fi
  done
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
