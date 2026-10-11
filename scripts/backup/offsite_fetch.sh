#!/usr/bin/env sh
# H-2: the restore test from the off-site copy (Cloudflare R2, or any OFFSITE_REMOTE). Downloads the newest dump and
# its SHA-256 sidecar from "$OFFSITE_REMOTE/backups" into a directory; verify_backup.sh then restores that file into a
# scratch database (restore.sh checks the sidecar first), so the test proves the copy off the host is usable.
# POSIX sh: runs in the rclone image (the compose `offsite` service).
#
#   offsite_fetch.sh <dest dir>      prints the fetched file's path on stdout
set -eu
REMOTE="${OFFSITE_REMOTE:?OFFSITE_REMOTE not set}"
DEST="${1:?usage: offsite_fetch.sh <dest dir>}"
log() { printf '%s offsite-fetch: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }

# Names carry a UTC stamp (atp_<db>_<YYYYmmddTHHMMSSZ>.dump[.enc], scripts/backup/backup.sh): the last by name is the newest.
NEWEST="$(rclone lsf "$REMOTE/backups" --files-only --include 'atp_*.dump' --include 'atp_*.dump.enc' | sort | tail -n 1)"
[ -n "$NEWEST" ] || { log "no dump under $REMOTE/backups"; exit 1; }
mkdir -p "$DEST"
find "$DEST" -maxdepth 1 -name 'atp_*' -type f -exec rm -f {} + 2>/dev/null || true   # only the previous test copy
rclone copyto "$REMOTE/backups/$NEWEST" "$DEST/$NEWEST"
rclone copyto "$REMOTE/backups/$NEWEST.sha256" "$DEST/$NEWEST.sha256" || log "WARNING: no .sha256 sidecar off-site for $NEWEST"
log "fetched $NEWEST from $REMOTE"
printf '%s\n' "$DEST/$NEWEST"
