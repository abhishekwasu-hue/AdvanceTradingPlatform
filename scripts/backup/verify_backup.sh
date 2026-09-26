#!/usr/bin/env sh
# Backup rehearsal: restores a backup into a fresh scratch database, checks it, drops it.
#
#   verify_backup.sh [backup file | 'latest']
#
# Checks (all must pass, exit code 1 otherwise):
#   * pg_restore completes without error
#   * alembic_version in the copy equals the source's (same schema generation)
#   * tenants / orders / trades / audit_logs row counts are within the source's (a backup taken
#     earlier can legitimately have fewer rows, never more)
#   * the audit hash chain in the copy verifies (python -m app.audit.verify_chain, when the
#     backend code is available - skipped with a notice otherwise)
# Prints a one-line JSON report to stdout for the operator's log / the monthly DR record.
. "$(dirname "$0")/common.sh"
need psql; need pg_restore

FILE="${1:-latest}"
[ "$FILE" = "latest" ] && FILE="$BACKUP_DIR/$(readlink "$BACKUP_DIR/latest")"
[ -f "$FILE" ] || die "no such file: $FILE"

SRC_URL="$(pg_url)"
SRC_DB="$(db_name_of "$SRC_URL")"
SCRATCH="${SRC_DB}_verify_$(date -u +%Y%m%d%H%M%S)"
SCRATCH_URL="$(with_db "$SRC_URL" "$SCRATCH")"
ADMIN_URL="$(with_db "$SRC_URL" postgres)"

psql "$ADMIN_URL" -qAtc "CREATE DATABASE \"$SCRATCH\"" >/dev/null || die "could not create scratch database $SCRATCH"
cleanup() { psql "$ADMIN_URL" -qAtc "DROP DATABASE IF EXISTS \"$SCRATCH\"" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM

STATUS="ok"; NOTES=""
# The first failure names the status; later checks only add notes.
fail() { [ "$STATUS" = "ok" ] && STATUS="$1"; NOTES="$NOTES $2;"; }

RESTORE_LOG="$(mktemp "${TMPDIR:-/tmp}/atp_verify.XXXXXX")"
if RESTORE_CONFIRM=yes "$(dirname "$0")/restore.sh" "$FILE" "$SCRATCH_URL" >"$RESTORE_LOG" 2>&1; then
  sed 's/^/  /' "$RESTORE_LOG" >&2
else
  sed 's/^/  /' "$RESTORE_LOG" >&2
  fail restore_failed "pg_restore failed: $(tail -n 1 "$RESTORE_LOG" | tr -d '"' | cut -c1-160)"
fi
rm -f "$RESTORE_LOG"

# Prints the query result, or "null" when the table is missing - keeps the report valid JSON.
q() { psql "$1" -qAtc "$2" 2>/dev/null || echo null; }
jstr() { case "$1" in null) printf null;; *) printf '"%s"' "$1";; esac; }
SRC_VER="$(q "$SRC_URL" 'select version_num from alembic_version')"
CPY_VER="$(q "$SCRATCH_URL" 'select version_num from alembic_version')"
[ "$SRC_VER" = "$CPY_VER" ] || fail schema_mismatch "alembic $CPY_VER != $SRC_VER"

COUNTS=""
for t in tenants orders trades audit_logs; do
  S="$(q "$SRC_URL" "select count(*) from $t")"; C="$(q "$SCRATCH_URL" "select count(*) from $t")"
  COUNTS="$COUNTS\"$t\":{\"source\":$S,\"copy\":$C},"
  if [ "$C" = null ]; then fail table_missing "$t unreadable in copy"
  elif [ "$S" != null ] && [ "$C" -gt "$S" ]; then fail count_mismatch "$t copy>source"; fi
done
COUNTS="{${COUNTS%,}}"

CHAIN="skipped"
BACKEND_DIR="${BACKEND_DIR:-$(dirname "$0")/../../backend}"
if [ -f "$BACKEND_DIR/app/audit/verify_chain.py" ] && command -v python3 >/dev/null 2>&1; then
  if [ "$STATUS" = "restore_failed" ] || [ "$STATUS" = "table_missing" ]; then
    CHAIN="not_checked"
  elif (cd "$BACKEND_DIR" && DATABASE_URL="$(printf '%s' "$SCRATCH_URL" | sed 's#^postgresql://#postgresql+asyncpg://#')" python3 -m app.audit.verify_chain >/dev/null 2>&1); then
    CHAIN="intact"
  else
    CHAIN="broken"; fail audit_chain_broken "audit chain does not verify in copy"
  fi
fi

printf '{"status":"%s","file":"%s","restored_into":"%s","alembic":{"source":%s,"copy":%s},"counts":%s,"audit_chain":"%s","notes":"%s","checked_at":"%s"}\n' \
  "$STATUS" "$(basename "$FILE")" "$SCRATCH" "$(jstr "$SRC_VER")" "$(jstr "$CPY_VER")" "$COUNTS" "$CHAIN" "$(printf '%s' "$NOTES" | sed 's/^ //')" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
[ "$STATUS" = "ok" ]
