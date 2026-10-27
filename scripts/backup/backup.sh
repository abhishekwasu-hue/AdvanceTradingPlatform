#!/usr/bin/env sh
# Takes one full logical backup of the platform database.
#
#   pg_dump custom format (compressed, restorable table-by-table with pg_restore)
#   -> optional AES-256 encryption (openssl enc, PBKDF2) when BACKUP_ENCRYPTION_PASSPHRASE is set
#   -> SHA-256 sidecar, `latest` pointer, LAST_BACKUP_OK marker for monitoring
#   -> retention: files older than BACKUP_RETENTION_DAYS go, but never below BACKUP_KEEP_MIN files
#   -> A1 tiers: the first dump of each ISO week (UTC) is also kept in weekly/, the first of each month in monthly/
#      (hard links with the same name - the seal covers the name), the newest BACKUP_KEEP_WEEKLY / BACKUP_KEEP_MONTHLY
#      of each; offsite_sync.sh copies them to their own prefixes (backups-weekly/, backups-monthly/)
#
# Exit code non-zero on any failure, and LAST_BACKUP_OK is *not* touched then - so "marker older
# than 26 hours" is the alert condition (docs/OPERATIONS.md 1.2).
. "$(dirname "$0")/common.sh"
need pg_dump

pg_setup
URL="$PG_URL"
DB="$(db_name_of "$URL")"
mkdir -p "$BACKUP_DIR"
# One run at a time, each with its own name: the scheduled loop's first run (30 s after its container starts, e.g. after a
# rollback) and a deploy's pre-migration backup once started in the same second, took the same name, and one run's
# cleanup removed the other's file ("mv: can't rename ...hmac.part"). The lock is released when this shell exits.
if command -v flock >/dev/null 2>&1; then
  exec 9>"$BACKUP_DIR/.backup.lock"
  flock -w "${BACKUP_LOCK_WAIT_SECONDS:-900}" 9 || die "another backup is still running after ${BACKUP_LOCK_WAIT_SECONDS:-900}s"
fi
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
while [ -e "$BACKUP_DIR/atp_${DB}_${STAMP}.dump" ] || [ -e "$BACKUP_DIR/atp_${DB}_${STAMP}.dump.enc" ]; do
  sleep 1; STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
done
BASE="$BACKUP_DIR/atp_${DB}_${STAMP}.dump"
TMP="$BASE.part"
# Z11 / L7: a failed run leaves no half-written file behind.
trap 'rm -f "$TMP" "$BASE.enc.part" "$BASE.enc.hmac.part"' EXIT

log "dumping $DB"
pg_dump --dbname="$URL" --format=custom --compress=6 --no-owner --no-privileges --file="$TMP"
OUT="$BASE"
if [ -n "$BACKUP_ENCRYPTION_PASSPHRASE" ]; then
  need openssl
  OUT="$BASE.enc"
  BACKUP_ENCRYPTION_PASSPHRASE="$BACKUP_ENCRYPTION_PASSPHRASE" \
    openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -in "$TMP" -out "$OUT.part" -pass env:BACKUP_ENCRYPTION_PASSPHRASE
  rm -f "$TMP"
  mv "$OUT.part" "$OUT"
else
  mv "$TMP" "$OUT"
fi
sha256_file "$OUT" > "$OUT.sha256"
# Z11 / N1: every encrypted dump is sealed where it is made (a restore refuses an unsealed one).
if [ -n "$BACKUP_ENCRYPTION_PASSPHRASE" ]; then seal_file "$OUT"; fi
SIZE="$(wc -c < "$OUT" | tr -d ' ')"
ln -sfn "$(basename "$OUT")" "$BACKUP_DIR/latest"
date -u +%Y-%m-%dT%H:%M:%SZ > "$BACKUP_DIR/LAST_BACKUP_OK"
log "wrote $OUT ($SIZE bytes) sha256=$(cat "$OUT.sha256")"

# A1 tiers (grandfather-father-son). <dir>/PERIOD names the week / month the tier last took a dump from; a new period
# takes this dump. Daily retention below only removes the top-level name, so a tier's hard link keeps the data.
tier() {
  _dir="$BACKUP_DIR/$1"; _period="$2"; _keep="$3"
  case "$_keep" in ''|*[!0-9]*) die "$1: keep count '$_keep' is not a number" ;; esac
  [ "$_keep" -gt 0 ] || return 0
  mkdir -p "$_dir"
  if [ "$(cat "$_dir/PERIOD" 2>/dev/null || true)" != "$_period" ]; then
    for _x in "$OUT" "$OUT.sha256" "$OUT.hmac"; do
      if [ -f "$_x" ]; then ln -f "$_x" "$_dir/$(basename "$_x")"; fi
    done
    printf '%s\n' "$_period" > "$_dir/PERIOD"
    log "$1: kept $(basename "$OUT") for $_period"
  fi
  # newest first by the UTC stamp in the name; everything past the newest N goes, with its sidecars
  ls -1 "$_dir"/atp_*.dump "$_dir"/atp_*.dump.enc 2>/dev/null | sort -r | tail -n +"$((_keep + 1))" | while read -r _old; do
    log "$1: removing $(basename "$_old")"
    rm -f "$_old" "$_old.sha256" "$_old.hmac"
  done
}
tier weekly "$(date -u +%G-W%V)" "$BACKUP_KEEP_WEEKLY"
tier monthly "$(date -u +%Y-%m)" "$BACKUP_KEEP_MONTHLY"

# Retention: oldest first, delete those older than the window while keeping the newest N.
COUNT="$(ls -1 "$BACKUP_DIR"/atp_*.dump "$BACKUP_DIR"/atp_*.dump.enc 2>/dev/null | wc -l | tr -d ' ')"
if [ "$COUNT" -gt "$BACKUP_KEEP_MIN" ]; then
  EXCESS=$((COUNT - BACKUP_KEEP_MIN))
  ls -1t "$BACKUP_DIR"/atp_*.dump "$BACKUP_DIR"/atp_*.dump.enc 2>/dev/null | tail -n "$EXCESS" | while read -r f; do
    if [ -n "$(find "$f" -mtime +"$BACKUP_RETENTION_DAYS" 2>/dev/null)" ]; then
      log "retention: removing $(basename "$f")"
      rm -f "$f" "$f.sha256" "$f.hmac"
    fi
  done
fi
log "done"
