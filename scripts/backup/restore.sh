#!/usr/bin/env sh
# Restores a backup file into a database.
#
#   restore.sh <backup file or 'latest'> [target postgres URL]
#
# Verifies the SHA-256 sidecar first, decrypts when the file is .enc (BACKUP_ENCRYPTION_PASSPHRASE
# required), then pg_restore --clean --if-exists so the target ends up as the backup, whatever it
# held before. The target defaults to the configured database - so on production this is the
# "we lost the database" button; use verify_backup.sh for a rehearsal into a scratch database.
# Set RESTORE_CONFIRM=yes to skip the interactive confirmation (scripts, verify_backup.sh).
. "$(dirname "$0")/common.sh"
need pg_restore

FILE="${1:-}"; [ -n "$FILE" ] || die "usage: restore.sh <file|latest> [target-url]"
[ "$FILE" = "latest" ] && FILE="$BACKUP_DIR/$(readlink "$BACKUP_DIR/latest")"
[ -f "$FILE" ] || die "no such file: $FILE"
TARGET="${2:-$(pg_url)}"

if [ -f "$FILE.sha256" ]; then
  EXPECTED="$(cat "$FILE.sha256")"; ACTUAL="$(sha256_file "$FILE")"
  [ "$EXPECTED" = "$ACTUAL" ] || die "sha256 mismatch for $FILE (expected $EXPECTED, got $ACTUAL)"
  log "sha256 verified"
else
  log "WARNING: no .sha256 sidecar next to $FILE - integrity not verified"
fi

if [ "${RESTORE_CONFIRM:-}" != "yes" ]; then
  printf 'This will REPLACE the contents of %s with %s. Type the database name to continue: ' "$(db_name_of "$TARGET")" "$(basename "$FILE")" >&2
  read -r answer
  [ "$answer" = "$(db_name_of "$TARGET")" ] || die "aborted"
fi

SRC="$FILE"
CLEANUP=""
case "$FILE" in
  *.enc)
    need openssl
    [ -n "$BACKUP_ENCRYPTION_PASSPHRASE" ] || die "BACKUP_ENCRYPTION_PASSPHRASE is required to restore an encrypted backup"
    SRC="$(mktemp "${TMPDIR:-/tmp}/atp_restore.XXXXXX")"; CLEANUP="$SRC"
    BACKUP_ENCRYPTION_PASSPHRASE="$BACKUP_ENCRYPTION_PASSPHRASE" \
      openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in "$FILE" -out "$SRC" -pass env:BACKUP_ENCRYPTION_PASSPHRASE
    ;;
esac

log "restoring $(basename "$FILE") into $(db_name_of "$TARGET")"
# --clean/--if-exists drop what the dump will recreate; --no-owner/--no-privileges keep the
# restore usable under a different role name than the source.
pg_restore --dbname="$TARGET" --clean --if-exists --no-owner --no-privileges --exit-on-error "$SRC"
[ -n "$CLEANUP" ] && rm -f "$CLEANUP"
log "restore complete"
