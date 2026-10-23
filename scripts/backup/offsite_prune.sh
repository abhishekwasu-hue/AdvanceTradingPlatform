#!/usr/bin/env sh
# A1: retention on the off-site copy (R2). `rclone copy` never deletes (offsite_sync.sh), so without this the bucket
# only grows. POSIX sh: runs in the rclone image (the compose `offsite` service), from deploy/atp.sh backup.
#
#   offsite_prune.sh [--dry-run]
#
# Per prefix, newest first by the UTC stamp in the name, a set (the dump + its .sha256 + .hmac) is removed only when
# BOTH hold: it is past the newest <keep> sets, and it is older than <min days>. <min days> is never below
# OFFSITE_LOCK_DAYS + 1: an object inside the bucket lock is never asked to go (R2 refuses that anyway, and a failed
# delete would fail the backup run). With bucket versioning on, a delete only hides the object; the noncurrent version
# goes by the bucket's own rule. The copy off-site therefore always holds at least what the server holds.
#
#   prefix                   keep                                    min days
#   backups/                 BACKUP_KEEP_MIN (7)                     BACKUP_RETENTION_DAYS (7) + 1
#   backups/pre-migration/   OFFSITE_KEEP_PRE_MIGRATION (6)          the lock floor
#   backups-weekly/          BACKUP_KEEP_WEEKLY (4)                  the lock floor
#   backups-monthly/         BACKUP_KEEP_MONTHLY (6)                 the lock floor
#   files/                   BACKUP_FILES_KEEP (14)                  the lock floor
#   backups/base/<stamp>/    BASE_BACKUP_KEEP (4)                    the lock floor
#   wal_archive/             0 (raw WAL from before A1, never again) the lock floor
#
# Prints one line per prefix; exit 1 when a delete failed (the rest is still tried).
set -eu
REMOTE="${OFFSITE_REMOTE:?OFFSITE_REMOTE not set}"
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1
log() { printf '%s offsite-prune: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }
num() {   # num <value> <default>: a whole number, or the default
  case "${1:-}" in ''|*[!0-9]*) printf '%s' "$2" ;; *) printf '%s' "$1" ;; esac
}
LOCK_DAYS="$(num "${OFFSITE_LOCK_DAYS:-}" 14)"
FLOOR=$((LOCK_DAYS + 1))
NOW="$(date -u +%s)"
FAILED=0
# list runs inside $(...) (a subshell): it reports a failed listing through this file, not a variable.
LIST_FAILED="$(mktemp "${TMPDIR:-/tmp}/atp_prune.XXXXXX")"; rm -f "$LIST_FAILED"
trap 'rm -f "$LIST_FAILED"' EXIT

# The UTC stamp "YYYYmmddTHHMMSSZ" that is <days> days old: anything named before it is older.
cutoff() {
  _d="$1"; [ "$_d" -ge "$FLOOR" ] || _d="$FLOOR"
  date -u -d "@$((NOW - _d * 86400))" +%Y%m%dT%H%M%SZ
}
stamp_of() { printf '%s' "$1" | sed -n 's/.*_\([0-9]\{8\}T[0-9]\{6\}Z\)\..*/\1/p'; }
older() { awk -v a="$1" -v b="$2" 'BEGIN { exit !(a < b) }'; }   # stamp a before stamp b (same fixed format)

# list <rclone lsf arguments...>: the names, one per line. A prefix with nothing under it is "directory not found"
# (exit 3) on a bucket - that is an empty list; any other error is a failure (nothing is removed from that prefix).
list() {
  _rc=0; _out="$(rclone lsf "$@" 2>/dev/null)" || _rc=$?
  case "$_rc" in
    0) printf '%s\n' "$_out" ;;
    3) ;;
    *) : > "$LIST_FAILED"; log "could not list $1 (rclone exit $_rc)" ;;
  esac
}

remove() {   # remove <rclone path>
  if [ "$DRY" = "1" ]; then log "would remove $1"; return 0; fi
  rclone deletefile "$1" -q 2>/dev/null
}

# prune <prefix> <name pattern of the main file> <keep> <min days>
prune() {
  _prefix="$1"; _pat="$2"; _keep="$3"; _cut="$(cutoff "$4")"
  _n=0; _gone=0
  for _name in $(list "$REMOTE/$_prefix" --files-only --include "$_pat" | sort -r); do
    _n=$((_n + 1))
    [ "$_n" -gt "$_keep" ] || continue
    _st="$(stamp_of "$_name")"
    if [ -z "$_st" ] || ! older "$_st" "$_cut"; then continue; fi
    if remove "$REMOTE/$_prefix/$_name"; then
      _gone=$((_gone + 1))
      for _side in sha256 hmac; do remove "$REMOTE/$_prefix/$_name.$_side" || true; done
    else
      FAILED=1; log "$_prefix: could not remove $_name (bucket lock longer than OFFSITE_LOCK_DAYS=$LOCK_DAYS?)"
    fi
  done
  log "$_prefix: $_n off-site, kept $((_n - _gone)), removed $_gone (keep $_keep, none newer than $_cut)"
}

DAILY_KEEP="$(num "${BACKUP_KEEP_MIN:-}" 7)"; [ "$DAILY_KEEP" -ge 1 ] || DAILY_KEEP=1
prune backups 'atp_*.dump.enc' "$DAILY_KEEP" $(( $(num "${BACKUP_RETENTION_DAYS:-}" 7) + 1 ))
prune backups/pre-migration 'atp_*.dump.enc' "$(num "${OFFSITE_KEEP_PRE_MIGRATION:-}" 6)" 0
prune backups-weekly 'atp_*.dump.enc' "$(num "${BACKUP_KEEP_WEEKLY:-}" 4)" 0
prune backups-monthly 'atp_*.dump.enc' "$(num "${BACKUP_KEEP_MONTHLY:-}" 6)" 0
prune files 'atp_files_*.tar.gz.enc' "$(num "${BACKUP_FILES_KEEP:-}" 14)" 0

# Base backups (point-in-time recovery): one directory per stamp.
BASE_KEEP="$(num "${BASE_BACKUP_KEEP:-}" 4)"; BASE_CUT="$(cutoff 0)"
_n=0; _gone=0
for _dir in $(list "$REMOTE/backups/base" --dirs-only | sed 's#/$##' | grep -E '^[0-9]{8}T[0-9]{6}Z$' | sort -r); do
  _n=$((_n + 1))
  [ "$_n" -gt "$BASE_KEEP" ] || continue
  older "$_dir" "$BASE_CUT" || continue
  if [ "$DRY" = "1" ]; then log "would remove backups/base/$_dir/"; _gone=$((_gone + 1))
  elif rclone delete "$REMOTE/backups/base/$_dir" -q 2>/dev/null; then _gone=$((_gone + 1))
  else FAILED=1; log "backups/base: could not remove $_dir"; fi
done
[ "$_n" = 0 ] || log "backups/base: $_n off-site, kept $((_n - _gone)), removed $_gone (keep $BASE_KEEP)"

# Raw WAL segments an older offsite_sync.sh may have copied: they are not encrypted, so they go (outside the lock).
_wal=0
for _name in $(list "$REMOTE/wal_archive" --files-only --min-age "${FLOOR}d"); do
  if remove "$REMOTE/wal_archive/$_name"; then _wal=$((_wal + 1)); else FAILED=1; fi
done
[ "$_wal" = 0 ] || log "wal_archive: removed $_wal raw WAL file(s) (no longer copied off-site)"

[ ! -e "$LIST_FAILED" ] || FAILED=1
[ "$FAILED" = 0 ]
