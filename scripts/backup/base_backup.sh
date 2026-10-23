#!/usr/bin/env sh
# Phase O5 / section 52: physical base backup for point-in-time recovery.
#
#   base_backup.sh            -> $BACKUP_DIR/base/<UTC stamp>/ (tar, gzip) + LAST_BASE_BACKUP_OK
#   base_backup.sh --stdin    the same from a gzip tar on stdin (deploy/atp.sh base-backup: `pg_basebackup -D - -Ft -z
#                             -X fetch` run inside the postgres container over its local socket, where replication is
#                             allowed; a pg_basebackup from this container would need a replication line in pg_hba)
#
# Together with continuous WAL archiving (Postgres `archive_mode=on`, archive_command copies each segment into the
# `wal_archive` volume; off by default on Hostinger - PG_ARCHIVE_MODE, runbook section 6c) this allows recovery to any
# instant since the base backup, not just to the last nightly pg_dump. Take one weekly (deploy/atp.sh schedules it when
# archiving is on); pitr_restore.sh consumes it. The direct mode needs a replication-capable connection: the postgres
# superuser or a role with REPLICATION.
#
# A1: with BACKUP_ENCRYPTION_PASSPHRASE set (always on the server) every tarball is encrypted like the dumps (AES-256,
# PBKDF2) and sealed (.hmac over "base/<stamp>/<file>" + the ciphertext); the plaintext never stays on disk. The
# backup's first WAL file is kept beside it in `start_wal` (the archive is pruned up to the oldest kept one), so the
# pruning below never needs to decrypt.
. "$(dirname "$0")/common.sh"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="$BACKUP_DIR/base/$STAMP"
mkdir -p "$DEST"
chmod 700 "$DEST"
# A failed run leaves no half-written directory behind.
trap '[ -f "$DEST/.complete" ] || rm -rf "$DEST"' EXIT
if [ "${1:-}" = "--stdin" ]; then
  log "writing the base backup from stdin into $DEST"
  cat > "$DEST/base.tar.gz.part"
  [ -s "$DEST/base.tar.gz.part" ] || die "no base backup arrived on stdin"
  gzip -t "$DEST/base.tar.gz.part" || die "the base backup on stdin is not a complete gzip stream"
  mv "$DEST/base.tar.gz.part" "$DEST/base.tar.gz"
else
  need pg_basebackup
  pg_setup
  log "taking base backup into $DEST"
  pg_basebackup --dbname="$PG_URL" --pgdata="$DEST" --format=tar --gzip --checkpoint=fast --wal-method=stream --progress
fi
[ -f "$DEST/base.tar.gz" ] || die "no base.tar.gz in $DEST"
# The oldest WAL file this backup needs: everything before it in the archive can go once this is the oldest kept one.
START_WAL="$(tar -xzOf "$DEST/base.tar.gz" backup_label 2>/dev/null | sed -n 's/^START WAL LOCATION: .*(file \([0-9A-F]*\)).*/\1/p')"
[ -n "$START_WAL" ] || die "no backup_label in the base backup - not a pg_basebackup tarball"
printf '%s\n' "$START_WAL" > "$DEST/start_wal"

for f in base.tar.gz pg_wal.tar.gz; do
  [ -f "$DEST/$f" ] || continue
  if [ -n "$BACKUP_ENCRYPTION_PASSPHRASE" ]; then
    need openssl
    BACKUP_ENCRYPTION_PASSPHRASE="$BACKUP_ENCRYPTION_PASSPHRASE" \
      openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -in "$DEST/$f" -out "$DEST/$f.enc.part" -pass env:BACKUP_ENCRYPTION_PASSPHRASE
    rm -f "$DEST/$f"
    mv "$DEST/$f.enc.part" "$DEST/$f.enc"
    sha256_file "$DEST/$f.enc" > "$DEST/$f.enc.sha256"
    seal_file "$DEST/$f.enc" "base/$STAMP/$f.enc"
  else
    sha256_file "$DEST/$f" > "$DEST/$f.sha256"
  fi
done
touch "$DEST/.complete"
ln -sfn "$STAMP" "$BACKUP_DIR/base/latest"
date -u +%Y-%m-%dT%H:%M:%SZ > "$BACKUP_DIR/LAST_BASE_BACKUP_OK"
log "base backup complete: $(du -sh "$DEST" | cut -f1), starts at WAL $START_WAL"

# Retention: keep the newest BASE_BACKUP_KEEP (default 4) base backups; WAL older than the oldest
# kept base backup is useless and is pruned with pg_archivecleanup when the archive is mounted.
KEEP="${BASE_BACKUP_KEEP:-4}"
ls -1d "$BACKUP_DIR"/base/2* 2>/dev/null | sort | head -n -"$KEEP" | while read -r old; do
  log "pruning old base backup $old"; rm -rf "$old"
done
if [ -d "${WAL_ARCHIVE_DIR:-/wal_archive}" ] && command -v pg_archivecleanup >/dev/null 2>&1; then
  OLDEST="$(ls -1d "$BACKUP_DIR"/base/2* | sort | head -n 1)"
  OLDEST_WAL=""
  if [ -s "$OLDEST/start_wal" ]; then OLDEST_WAL="$(cat "$OLDEST/start_wal")"
  elif [ -f "$OLDEST/base.tar.gz" ]; then    # a base backup taken before start_wal existed (plain tarball)
    OLDEST_WAL="$(tar -xzOf "$OLDEST/base.tar.gz" backup_label 2>/dev/null | sed -n 's/^START WAL LOCATION: .*(file \([0-9A-F]*\)).*/\1/p')"
  fi
  if [ -n "$OLDEST_WAL" ]; then
    pg_archivecleanup "${WAL_ARCHIVE_DIR:-/wal_archive}" "$OLDEST_WAL" && log "WAL archive pruned before $OLDEST_WAL"
  fi
fi
