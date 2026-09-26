#!/usr/bin/env sh
# Shared helpers for the backup scripts (POSIX sh: the postgres:*-alpine image has no bash).
#
# Connection: either DATABASE_URL (the app's own setting, "postgresql+asyncpg://..." is accepted
# and normalised) or the libpq PG* variables (PGHOST, PGPORT, PGUSER, PGPASSWORD, PGDATABASE).
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
BACKUP_KEEP_MIN="${BACKUP_KEEP_MIN:-7}"
BACKUP_ENCRYPTION_PASSPHRASE="${BACKUP_ENCRYPTION_PASSPHRASE:-}"

log() { printf '%s backup: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

# Turns the app's SQLAlchemy URL into a libpq one; leaves plain postgres URLs alone.
pg_url() {
  if [ -n "${DATABASE_URL:-}" ]; then
    printf '%s' "$DATABASE_URL" | sed -e 's#^postgresql+asyncpg://#postgresql://#' -e 's#^postgresql+psycopg://#postgresql://#'
  else
    printf 'postgresql://%s:%s@%s:%s/%s' "${PGUSER:-atp_user}" "${PGPASSWORD:-}" "${PGHOST:-postgres}" "${PGPORT:-5432}" "${PGDATABASE:-advance_trading_platform}"
  fi
}

db_name_of() { printf '%s' "$1" | sed -e 's#.*/##' -e 's#?.*##'; }

# Same URL, different database (for the scratch restore).
with_db() { printf '%s' "$1" | sed -e "s#/[^/?]*\(?.*\)\?\$#/$2\1#"; }

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else openssl dgst -sha256 "$1" | sed 's/.*= //'; fi
}

need() { command -v "$1" >/dev/null 2>&1 || die "$1 is required but not installed"; }
