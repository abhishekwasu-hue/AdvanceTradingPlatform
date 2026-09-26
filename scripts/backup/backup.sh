#!/usr/bin/env sh
# Takes one full logical backup of the platform database.
#
#   pg_dump custom format (compressed, restorable table-by-table with pg_restore)
#   -> optional AES-256 encryption (openssl enc, PBKDF2) when BACKUP_ENCRYPTION_PASSPHRASE is set
#   -> SHA-256 sidecar, `latest` pointer, LAST_BACKUP_OK marker for monitoring
#   -> retention: files older than BACKUP_RETENTION_DAYS go, but never below BACKUP_KEEP_MIN files
#
# Exit code non-zero on any failure, and LAST_BACKUP_OK is *not* touched then - so "marker older
# than 26 hours" is the alert condition (docs/OPERATIONS.md 1.2).
. "$(dirname "$0")/common.sh"
need pg_dump

URL="$(pg_url)"
DB="$(db_name_of "$URL")"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP_DIR"
BASE="$BACKUP_DIR/atp_${DB}_${STAMP}.dump"
TMP="$BASE.part"

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
SIZE="$(wc -c < "$OUT" | tr -d ' ')"
ln -sfn "$(basename "$OUT")" "$BACKUP_DIR/latest"
date -u +%Y-%m-%dT%H:%M:%SZ > "$BACKUP_DIR/LAST_BACKUP_OK"
log "wrote $OUT ($SIZE bytes) sha256=$(cat "$OUT.sha256")"

# Retention: oldest first, delete those older than the window while keeping the newest N.
COUNT="$(ls -1 "$BACKUP_DIR"/atp_*.dump "$BACKUP_DIR"/atp_*.dump.enc 2>/dev/null | wc -l | tr -d ' ')"
if [ "$COUNT" -gt "$BACKUP_KEEP_MIN" ]; then
  EXCESS=$((COUNT - BACKUP_KEEP_MIN))
  ls -1t "$BACKUP_DIR"/atp_*.dump "$BACKUP_DIR"/atp_*.dump.enc 2>/dev/null | tail -n "$EXCESS" | while read -r f; do
    if [ -n "$(find "$f" -mtime +"$BACKUP_RETENTION_DAYS" 2>/dev/null)" ]; then
      log "retention: removing $(basename "$f")"
      rm -f "$f" "$f.sha256"
    fi
  done
fi
log "done"
