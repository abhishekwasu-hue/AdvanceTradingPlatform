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

# Turns the app's SQLAlchemy URL into a libpq one; leaves plain postgres URLs alone. Carries the password -
# use pg_setup / pg_url, which keep it out of argv.
pg_url_raw() {
  if [ -n "${DATABASE_URL:-}" ]; then
    printf '%s' "$DATABASE_URL" | sed -e 's#^postgresql+asyncpg://#postgresql://#' -e 's#^postgresql+psycopg://#postgresql://#'
  else
    printf 'postgresql://%s:%s@%s:%s/%s' "${PGUSER:-atp_user}" "${PGPASSWORD:-}" "${PGHOST:-postgres}" "${PGPORT:-5432}" "${PGDATABASE:-advance_trading_platform}"
  fi
}

# Z11 / L6: exports PGPASSWORD from the URL and sets PG_URL to the same URL WITHOUT the password, so no password
# ever reaches a command line (ps shows argv; libpq and asyncpg read PGPASSWORD from the environment). Call it in the
# script's own shell (not in $(...)) before running psql / pg_dump / pg_restore.
pg_setup() {
  _raw="$(pg_url_raw)"
  _pw="$(printf '%s' "$_raw" | sed -n 's#^postgresql://[^:/@]*:\([^@]*\)@.*#\1#p')"
  if [ -n "$_pw" ]; then PGPASSWORD="$_pw"; export PGPASSWORD; fi
  PG_URL="$(printf '%s' "$_raw" | sed 's#^\(postgresql://[^:/@]*\):[^@]*@#\1@#')"
}

# The connection URL without its password (see pg_setup).
pg_url() { pg_setup; printf '%s' "$PG_URL"; }

db_name_of() { printf '%s' "$1" | sed -e 's#.*/##' -e 's#?.*##'; }

# Same URL, different database (for the scratch restore).
with_db() { printf '%s' "$1" | sed -e "s#/[^/?]*\(?.*\)\?\$#/$2\1#"; }

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else openssl dgst -sha256 "$1" | sed 's/.*= //'; fi
}

need() { command -v "$1" >/dev/null 2>&1 || die "$1 is required but not installed"; }

# Z11 / N1: the seal - HMAC-SHA256 over the (encrypted) file, written as <file>.hmac. openssl enc has no authenticated
# mode, and a .sha256 can be recomputed by whoever changed the file; without the passphrase a seal cannot be.
#   key  = PBKDF2-HMAC-SHA256(BACKUP_ENCRYPTION_PASSPHRASE, salt "atpmacv1", 200000 rounds), 32 bytes - derived by
#          `openssl enc -P` with -pass env: (the passphrase never reaches argv); the 8-byte salt and the label keep this
#          key separate from the encryption key. deploy/atp.sh computes the same value (python hashlib) on the host.
#   HMAC = SHA256((K ^ opad) || SHA256((K ^ ipad) || file)), built here with printf (a shell builtin) and openssl dgst.
_mac_key_hex() {
  BACKUP_ENCRYPTION_PASSPHRASE="$BACKUP_ENCRYPTION_PASSPHRASE" openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -md sha256 \
    -S 6174706d61637631 -pass env:BACKUP_ENCRYPTION_PASSPHRASE -P | sed -n 's/^key=//p' | tr 'A-F' 'a-f'
}
# 64-byte block of the key XOR <pad>, as printf octal escapes.
_mac_pad() {
  _k="$1"; _p="$2"; _out=""; _i=0
  while [ "$_i" -lt 64 ]; do
    if [ "$_i" -lt 32 ]; then _b="$(printf '%s' "$_k" | cut -c$((_i * 2 + 1))-$((_i * 2 + 2)))"; else _b="00"; fi
    _out="$_out\\$(printf '%03o' $(( 0x$_b ^ _p )))"
    _i=$((_i + 1))
  done
  printf '%s' "$_out"
}
hmac_file() {
  [ -n "$BACKUP_ENCRYPTION_PASSPHRASE" ] || die "the seal needs BACKUP_ENCRYPTION_PASSPHRASE"
  need openssl
  _key="$(_mac_key_hex)"
  [ "${#_key}" = 64 ] || die "could not derive the seal key"
  _ipad="$(_mac_pad "$_key" 54)"; _opad="$(_mac_pad "$_key" 92)"
  _inner="$(mktemp "${TMPDIR:-/tmp}/atp_mac.XXXXXX")"
  # shellcheck disable=SC2059  # the format is the octal key block on purpose
  { printf "$_ipad"; cat "$1"; } | openssl dgst -sha256 -binary > "$_inner"
  # shellcheck disable=SC2059
  { printf "$_opad"; cat "$_inner"; } | openssl dgst -sha256 | sed 's/.*= //'
  rm -f "$_inner"
}
seal_file() { hmac_file "$1" > "$1.hmac.part" && mv "$1.hmac.part" "$1.hmac"; }
# ok | missing | mismatch
seal_state() {
  [ -s "$1.hmac" ] || { printf 'missing'; return 0; }
  if [ "$(hmac_file "$1")" = "$(tr -d '\r\n' < "$1.hmac")" ]; then printf 'ok'; else printf 'mismatch'; fi
}
