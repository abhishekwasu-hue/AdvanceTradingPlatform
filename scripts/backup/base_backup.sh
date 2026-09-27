#!/usr/bin/env sh
# Phase O5 / section 52: physical base backup for point-in-time recovery.
#
#   base_backup.sh            -> $BACKUP_DIR/base/<UTC stamp>/ (tar, gzip) + LAST_BASE_BACKUP_OK
#
# Together with continuous WAL archiving (compose `postgres` service: archive_mode=on,
# archive_command copies each segment into the `wal_archive` volume) this allows recovery to any
# instant since the base backup, not just to the last nightly pg_dump. Take one weekly (or after
# any bulk change); pitr_restore.sh consumes it. Needs a replication-capable connection: the
# postgres superuser or a role with REPLICATION (the compose default user has it).
. "$(dirname "$0")/common.sh"
need pg_basebackup

URL="$(pg_url)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="$BACKUP_DIR/base/$STAMP"
mkdir -p "$DEST"
log "taking base backup into $DEST"
pg_basebackup --dbname="$URL" --pgdata="$DEST" --format=tar --gzip --checkpoint=fast --wal-method=stream --progress
sha256_file "$DEST/base.tar.gz" > "$DEST/base.tar.gz.sha256"
ln -sfn "$STAMP" "$BACKUP_DIR/base/latest"
date -u +%Y-%m-%dT%H:%M:%SZ > "$BACKUP_DIR/LAST_BASE_BACKUP_OK"
log "base backup complete: $(du -sh "$DEST" | cut -f1)"

# Retention: keep the newest BASE_BACKUP_KEEP (default 4) base backups; WAL older than the oldest
# kept base backup is useless and is pruned with pg_archivecleanup when the archive is mounted.
KEEP="${BASE_BACKUP_KEEP:-4}"
ls -1d "$BACKUP_DIR"/base/2* 2>/dev/null | sort | head -n -"$KEEP" | while read -r old; do
  log "pruning old base backup $old"; rm -rf "$old"
done
if [ -d "${WAL_ARCHIVE_DIR:-/wal_archive}" ] && command -v pg_archivecleanup >/dev/null 2>&1; then
  OLDEST="$(ls -1d "$BACKUP_DIR"/base/2* | sort | head -n 1)"
  if [ -n "$OLDEST" ] && [ -f "$OLDEST/base.tar.gz" ]; then
    # The oldest kept base backup's starting WAL file: everything before it can go.
    START_WAL="$(tar -xzOf "$OLDEST/base.tar.gz" backup_label 2>/dev/null | sed -n 's/^START WAL LOCATION: .*(file \([0-9A-F]*\)).*/\1/p')"
    [ -n "$START_WAL" ] && pg_archivecleanup "${WAL_ARCHIVE_DIR:-/wal_archive}" "$START_WAL" && log "WAL archive pruned before $START_WAL"
  fi
fi
