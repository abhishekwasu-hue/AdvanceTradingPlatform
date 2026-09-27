#!/usr/bin/env sh
# Phase O5: point-in-time recovery into a NEW data directory.
#
#   pitr_restore.sh <base backup dir | latest> "<recovery target time, e.g. 2026-09-28 11:04:00+05:30>" <new data dir>
#
# 1. unpacks the base backup, 2. writes recovery settings pointing restore_command at the WAL
# archive with recovery_target_time, 3. leaves a data directory that Postgres will replay to that
# instant on first start (then promotes). Run it on a stopped database server, never against the
# live data directory: bring the result up on a scratch container first, verify (positions,
# orders, audit chain), then swap. Full runbook: docs/OPERATIONS.md 1.2a.
. "$(dirname "$0")/common.sh"
need tar

BASE="${1:-}"; TARGET_TIME="${2:-}"; NEW_DIR="${3:-}"
[ -n "$BASE" ] && [ -n "$TARGET_TIME" ] && [ -n "$NEW_DIR" ] || die "usage: pitr_restore.sh <base dir|latest> '<target time>' <new data dir>"
[ "$BASE" = "latest" ] && BASE="$BACKUP_DIR/base/$(readlink "$BACKUP_DIR/base/latest")"
[ -f "$BASE/base.tar.gz" ] || die "no base backup at $BASE"
if [ -d "$NEW_DIR" ] && [ -n "$(ls -A "$NEW_DIR" 2>/dev/null)" ]; then die "$NEW_DIR is not empty - refusing to overwrite"; fi
WAL_DIR="${WAL_ARCHIVE_DIR:-/wal_archive}"
[ -d "$WAL_DIR" ] || die "WAL archive $WAL_DIR not mounted"

if [ -f "$BASE/base.tar.gz.sha256" ]; then
  [ "$(cat "$BASE/base.tar.gz.sha256")" = "$(sha256_file "$BASE/base.tar.gz")" ] || die "sha256 mismatch on $BASE/base.tar.gz"
  log "sha256 verified"
fi
mkdir -p "$NEW_DIR" && chmod 700 "$NEW_DIR"
log "unpacking base backup"
tar -xzf "$BASE/base.tar.gz" -C "$NEW_DIR"
if [ -f "$BASE/pg_wal.tar.gz" ]; then mkdir -p "$NEW_DIR/pg_wal"; tar -xzf "$BASE/pg_wal.tar.gz" -C "$NEW_DIR/pg_wal"; fi
rm -f "$NEW_DIR/postmaster.pid"
cat >> "$NEW_DIR/postgresql.auto.conf" <<CONF
# written by pitr_restore.sh
restore_command = 'cp $WAL_DIR/%f %p'
recovery_target_time = '$TARGET_TIME'
recovery_target_action = 'promote'
CONF
touch "$NEW_DIR/recovery.signal"
log "ready: start postgres with PGDATA=$NEW_DIR; it replays WAL to '$TARGET_TIME' and promotes."
log "then verify (alembic current, open trades, python -m app.audit.verify_chain) before pointing the API at it"
