#!/usr/bin/env bash
# Z11: the one owner-run command for the Hostinger host (docs/deploy/HOSTINGER_RUNBOOK.md). Run as the non-root user
# (atp) from the clone in /opt/atp:
#
#   /opt/atp/deploy/atp.sh install                 first time: bootstrap checks -> deploy -> health -> backup + verify
#   /opt/atp/deploy/atp.sh deploy [REF]            every update: REF = origin/main (default), a tag or a commit
#   /opt/atp/deploy/atp.sh health                  read-only: containers, API, deep health, TLS, key, flags, backups
#   /opt/atp/deploy/atp.sh rollback [--to SHA] [--restore-db]
#                                                  the previous release's images; --restore-db also puts the database
#                                                  back to the backup taken just before that release migrated it
#   /opt/atp/deploy/atp.sh backup [--verify]       database dump + files archive, encrypted + sealed, copied to R2
#   /opt/atp/deploy/atp.sh restore-check           newest backup from R2 -> scratch database -> checks -> dropped
#   /opt/atp/deploy/atp.sh bootstrap               host checks (as root: runs deploy/hostinger/bootstrap.sh)
#   /opt/atp/deploy/atp.sh schedule | releases | help
#
#   --dry-run (anywhere, or ATP_DRY_RUN=1): prints every step it would take; changes nothing on the host.
#
# Rules it keeps (tests/test_z11_deploy_bundle.py checks them):
#   * secrets are read only from /opt/atp/.env, never created, edited or printed - only key NAMES appear in output, and
#     no secret is ever put on a command line; .env must exist, be mode 600 and hold every required key;
#     SECRETS_ENCRYPTION_KEY must be the one this host first deployed with (a salted scrypt fingerprint is kept in
#     .deploy/) - it is never rotated here;
#   * fails closed: any failed step stops the run with a non-zero exit before the next one changes anything, and the
#     checkout goes back to the release that still runs;
#   * images are built from one commit and tagged with it (deploy/compose.release.yml); a rollback reuses them;
#   * migrations run through backend/scripts/migrate_guard.py after an encrypted, sealed pre-migration backup kept out
#     of the normal retention; it never runs `alembic downgrade`; a code-only rollback is refused past a migration
#     that is not ROLLBACK_SAFE (backend/scripts/migration_rollback_safety.py);
#   * the worker is not restarted while any exchange session is open and positions are open (the app's own calendar,
#     backend/scripts/session_check.py), unless ATP_ALLOW_SESSION_RESTART=1;
#   * feature flags and every LIVE switch are left exactly as they are (compared before and after a deploy - the one
#     change allowed is the release's own Phase 0 first-boot seed of a flag that had no entry: live_trading OFF,
#     phase0_paper_only ON); the host runs with ATP_DEPLOYMENT_PHASE=phase0 (default; .env may name another mode only
#     when the owner ends Phase 0), so a fresh install starts PAPER-only with nothing for the owner to set; `health`
#     and `install` still ask the owner to act (exit 10) if live_trading is on or phase0_paper_only is off; profiles
#     come only from COMPOSE_PROFILES in .env;
#   * idempotent: running a command again converges on the same state.
#
# Exit codes: 0 ok | 1 failed | 2 usage | 3 refused: market session (migration / restart) | 10 owner action needed |
#             20 key changed
set -Eeuo pipefail
umask 077
export TZ=Asia/Kolkata

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ATP_DIR="${ATP_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
STATE_DIR="$ATP_DIR/.deploy"
ENV_FILE="$ATP_DIR/.env"
PROJECT="atp"
API="${ATP_API_URL:-http://127.0.0.1:8000}"
HEALTH_TIMEOUT="${ATP_HEALTH_TIMEOUT:-240}"
POLL="${ATP_POLL_SECONDS:-3}"
KEEP_RELEASES="${ATP_KEEP_RELEASES:-3}"
FILES_BACKUP_DIR="${ATP_FILES_BACKUP_DIR:-/var/backups/atp-files}"
DRY_RUN="${ATP_DRY_RUN:-0}"
COMPOSE_MIN="2.24"
REQUIRED_KEYS=(POSTGRES_PASSWORD JWT_SECRET_KEY SECRETS_ENCRYPTION_KEY METRICS_TOKEN DOMAIN ACME_EMAIL BACKUP_ENCRYPTION_PASSPHRASE)
LIVE_SWITCHES=(LIVE_MARKET_PROTECTION LIVE_UPSTOX_OPTION_STOP_LIMIT LIVE_EXIT_IF_NO_STOP LIVE_STRICT_WING_FILL STATIC_IP_REQUIRED_FOR_LIVE)
APP_IMAGES=(atp-backend atp-worker atp-frontend atp-research-worker atp-backup)

RELEASE=""          # image tag compose uses (ATP_RELEASE)
DB_AHEAD=""         # MIGRATION_ALLOW_DB_AHEAD for compose: the exact database revision an older image may start on
RESTORE_CHECKOUT="" # on a failed exit, the checkout goes back to this commit (the release that still runs)
SCRATCH_DB=""       # a verify copy to drop on exit
VERIFY_ROLE="atp_restore"  # the non-superuser role verify_backup.sh restores the copy as (its VERIFY_RESTORE_ROLE default)
VERIFY_ROLE_LOGIN=0 # 1 while that role can log in for the audit-chain check; NOLOGIN again on exit
WORKER_STARTED=0    # epoch when this run restarted the worker; health then wants a heartbeat newer than that
OWNER_ACTION=""     # what health found that only the owner can do (exit 10)
WARNS=0
FAILS=0

# --- output -----------------------------------------------------------------------------------------------------
step() { printf '\n==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
ok()   { printf '    ok    %s\n' "$*"; }
warn() { WARNS=$((WARNS + 1)); printf '    WARN  %s\n' "$*"; }
bad()  { FAILS=$((FAILS + 1)); printf '    FAIL  %s\n' "$*"; }
die()  { local code="${2:-1}"; printf '\nFAILED: %s\n' "$1" >&2; exit "$code"; }
# shellcheck disable=SC2317,SC2329  # invoked by the ERR trap below (SC2317 on ShellCheck 0.9, SC2329 on 0.10+)
on_err() { [ "$BASHPID" = "$$" ] || return 0; printf '\nFAILED: a command failed at line %s (exit %s) - stopped before the next step.\n' "$1" "$2" >&2; }
# shellcheck disable=SC2317,SC2329  # invoked by the EXIT trap below
on_exit() {
  local rc=$?
  [ "$BASHPID" = "$$" ] || return 0
  if [ "$VERIFY_ROLE_LOGIN" = "1" ]; then verify_role_login "" || true; fi
  if [ -n "$SCRATCH_DB" ]; then
    dc exec -T postgres psql -X -q -v ON_ERROR_STOP=1 -U "$(pg_user)" -d postgres -c "DROP DATABASE IF EXISTS \"$SCRATCH_DB\"" >/dev/null 2>&1 || true
  fi
  if [ "$rc" != "0" ] && [ -n "$RESTORE_CHECKOUT" ] && [ "$DRY_RUN" != "1" ]; then
    if git -C "$ATP_DIR" checkout -q --detach "$RESTORE_CHECKOUT" 2>/dev/null; then
      printf 'checkout put back to %s (the release that still runs)\n' "${RESTORE_CHECKOUT:0:12}" >&2
    fi
  fi
}
trap 'on_err "$LINENO" "$?"' ERR
trap on_exit EXIT

# Mutating command: printed, then run - or only printed under --dry-run. Arguments never carry a secret.
run() {
  printf '    + %s\n' "$*"
  [ "$DRY_RUN" = "1" ] && return 0
  "$@"
}

# --- .env (read-only, never sourced: a value is data, not shell) -------------------------------------------------
# Same reading as docker compose: the last assignment wins; a quoted value is taken up to its closing quote; in an
# unquoted value, whitespace + '#' starts a comment (a '#' inside a word is kept). Parsed once, in bash, into memory
# (never exported, never written anywhere).
declare -A ENVV=()
env_load() {
  [ -f "$ENV_FILE" ] || return 0
  local line key val
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=(.*)$ ]] || continue
    key="${BASH_REMATCH[2]}"; val="${BASH_REMATCH[3]}"
    val="${val#"${val%%[![:space:]]*}"}"
    case "$val" in
      \"*) val="${val#\"}"; val="${val%%\"*}" ;;
      \'*) val="${val#\'}"; val="${val%%\'*}" ;;
      *) val="${val%%[[:space:]]#*}"; val="${val%"${val##*[![:space:]]}"}" ;;
    esac
    ENVV[$key]="$val"
  done < "$ENV_FILE"
}
env_get() { printf '%s' "${ENVV[$1]-}"; }
# A key counts as set when it has a value that is not a placeholder.
env_set() {
  local v; v="$(env_get "$1")"
  case "$v" in ""|CHANGE_ME|change-me|changeme|"<"*">") return 1 ;; esac
  return 0
}
is_true() { case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in 1|true|yes|on) return 0 ;; esac; return 1; }

pg_user() { local v; v="$(env_get POSTGRES_USER)"; printf '%s' "${v:-atp_user}"; }
pg_db()   { local v; v="$(env_get POSTGRES_DB)"; printf '%s' "${v:-advance_trading_platform}"; }
research_on() { case ",$(env_get COMPOSE_PROFILES)," in *,research,*) return 0 ;; esac; return 1; }
# Phase 0 deployment defaults (backend/app/platform/deployment_phase.py): phase0 unless .env names another mode.
deployment_phase() { local v; v="$(env_get ATP_DEPLOYMENT_PHASE)"; printf '%s' "${v:-phase0}"; }

# --- state (.deploy/, never secret) ------------------------------------------------------------------------------
state_init() { [ "$DRY_RUN" = "1" ] || mkdir -p "$STATE_DIR/logs"; }
current_sha() { cat "$STATE_DIR/current" 2>/dev/null || true; }
# Salted scrypt of the key (per-host random salt in .deploy/key_salt): the key reaches python on stdin, never argv.
key_fingerprint() {
  local salt; salt="$(cat "$STATE_DIR/key_salt" 2>/dev/null || true)"
  [ -n "$salt" ] || { printf 'no-salt'; return 0; }
  env_get SECRETS_ENCRYPTION_KEY | python3 -c 'import hashlib, sys
print(hashlib.scrypt(sys.stdin.buffer.read(), salt=bytes.fromhex(sys.argv[1]), n=2 ** 14, r=8, p=1, dklen=32).hex())' "$salt"
}
db_ahead_marker() { [ -s "$STATE_DIR/db_ahead" ]; }

# releases.log: <utc time> <kind> <sha> <previous sha|-> <pre-migration backup|-> <rev before|-> <rev after|->
#   kind: deploy | converge (same release again) | failed (did not become healthy; its backup undoes it) |
#         rollback | restore (--restore-db that kept the code)
# The deploy that made <sha> the running release: the newest `deploy` line for it whose previous release differs.
release_line_for() { awk -v s="$1" '$2 == "deploy" && $3 == s && $4 != s' "$STATE_DIR/releases.log" 2>/dev/null | tail -n 1; }
log_release() { [ "$DRY_RUN" = "1" ] || { state_init; printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$STATE_DIR/releases.log"; }; }

# --- docker compose ----------------------------------------------------------------------------------------------
dc() {
  local ahead="$DB_AHEAD"
  if [ -z "$ahead" ] && db_ahead_marker; then ahead="$(cat "$STATE_DIR/db_ahead")"; fi
  (cd "$ATP_DIR" && ATP_RELEASE="${RELEASE:-latest}" MIGRATION_ALLOW_DB_AHEAD="$ahead" ATP_DEPLOYMENT_PHASE="$(deployment_phase)" \
    docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.hostinger.yml \
    -f deploy/compose.release.yml -p "$PROJECT" "$@")
}
rdc() { printf '    + docker compose %s\n' "$*"; [ "$DRY_RUN" = "1" ] && return 0; dc "$@"; }
# dc with another release's image; RELEASE is restored afterwards.
dc_as() { local keep="$RELEASE" rc=0; RELEASE="$1"; shift; dc "$@" || rc=$?; RELEASE="$keep"; return "$rc"; }

psql_q() {
  dc exec -T postgres psql -X -A -t -q -v ON_ERROR_STOP=1 -U "$(pg_user)" -d "${2:-$(pg_db)}" -c "$1" 2>/dev/null
}
# Z11: lets the verify role log in with a one-run password ($1), or makes it NOLOGIN again ($1 empty). The SQL goes to
# psql on stdin, so the password never reaches an argv.
verify_role_login() {
  if [ -n "$1" ]; then
    printf 'ALTER ROLE "%s" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD '"'"'%s'"'"';\n' "$VERIFY_ROLE" "$1"
  else
    printf 'ALTER ROLE "%s" NOLOGIN PASSWORD NULL;\n' "$VERIFY_ROLE"
  fi | dc exec -T postgres psql -X -q -v ON_ERROR_STOP=1 -U "$(pg_user)" -d postgres >/dev/null 2>&1
}
# "-" = no schema yet; "unknown" = the database could not be read (callers fail closed on it).
db_revision() {
  local db="${1:-$(pg_db)}" reg r
  reg="$(psql_q "select coalesce(to_regclass('public.alembic_version')::text, '')" "$db")" || { printf 'unknown'; return 0; }
  [ -n "$reg" ] || { printf '%s' '-'; return 0; }
  r="$(psql_q 'select version_num from alembic_version order by 1' "$db" | paste -sd, -)" || { printf 'unknown'; return 0; }
  printf '%s' "${r:--}"
}
code_head() { { dc run --rm --no-deps -T backend python -m alembic heads 2>/dev/null || true; } | awk '/\(head\)/ {print $1}' | sort | paste -sd, -; }
# no-table | none (no flags row) | the flags row as stored (JSON; flag names and switches, never a secret) |
# unknown (could not read - a failure, never "unchanged")
flags_snapshot() {
  local reg h
  reg="$(psql_q "select coalesce(to_regclass('public.platform_controls')::text, '')")" || { printf 'unknown'; return 0; }
  [ -n "$reg" ] || { printf 'no-table'; return 0; }
  h="$(psql_q "select value_json from platform_controls where key = 'feature_flags'")" || { printf 'unknown'; return 0; }
  printf '%s' "${h:-none}"
}
# Before/after a deploy: "unchanged", "seeded <names>" (only the Phase 0 first-boot seed added entries that were absent:
# live_trading {"on": false}, phase0_paper_only {"on": true}, no organisation list - the restrictive direction), or
# "changed" (anything else, including an unreadable row).
flags_change() {
  FLAGS_BEFORE="$1" FLAGS_AFTER="$2" python3 -c 'import json, os
SEED = {"live_trading": {"on": False, "tenants": []}, "phase0_paper_only": {"on": True, "tenants": []}}
def load(v):
    if v in ("no-table", "none", ""):
        return {}
    d = json.loads(v)
    if not isinstance(d, dict):
        raise ValueError(v)
    return d
try:
    b, a = load(os.environ["FLAGS_BEFORE"]), load(os.environ["FLAGS_AFTER"])
except Exception:
    print("changed"); raise SystemExit(0)
if a == b:
    print("unchanged"); raise SystemExit(0)
bf, af = dict(b.get("flags") or {}), dict(a.get("flags") or {})
added = sorted(k for k in af if k not in bf)
same_rest = {k: v for k, v in b.items() if k != "flags"} == {k: v for k, v in a.items() if k != "flags"}
kept = all(af.get(k) == v for k, v in bf.items())
if same_rest and kept and added and all(af[k] == SEED.get(k) for k in added):
    print("seeded " + ",".join(added))
else:
    print("changed")'
}
images_exist() {
  local img imgs=(atp-backend atp-worker atp-frontend atp-backup)
  if research_on; then imgs+=(atp-research-worker); fi
  for img in "${imgs[@]}"; do
    docker image inspect "$img:$1" >/dev/null 2>&1 || return 1
  done
}
release_of() { printf '%s' "${1:0:12}"; }
app_services() { printf '%s\n' backend worker; if research_on; then printf '%s\n' research-worker; fi; }
worker_services() { printf '%s\n' worker; if research_on; then printf '%s\n' research-worker; fi; }

# After RELEASE is set: may that release's API start on the database as it is? If the database is ahead of the code,
# the newer image ($1, which knows every revision in between) must confirm it descends from the code's head and that
# every migration in between is ROLLBACK_SAFE; then MIGRATION_ALLOW_DB_AHEAD names that exact revision.
settle_db_ahead() {
  local checker="$1" now_rev rel_head out rc=0
  [ "$DRY_RUN" = "1" ] && { info "would check whether the database is ahead of $RELEASE and whether that is rollback-safe"; return 0; }
  now_rev="$(db_revision)"; rel_head="$(code_head)"
  [ "$now_rev" != "unknown" ] || die "cannot read the database revision" 1
  if [ -z "$rel_head" ] || [ "$now_rev" = "-" ] || [ "$now_rev" = "$rel_head" ]; then
    DB_AHEAD=""; rm -f "$STATE_DIR/db_ahead"; return 0
  fi
  # shellcheck disable=SC2016  # $0 / $1 expand inside the container
  out="$(dc_as "$checker" run --rm --no-deps -T backend sh -c \
    'test -f scripts/migration_rollback_safety.py || { echo "{\"error\": \"no checker in this image\"}"; exit 1; }; python scripts/migration_rollback_safety.py check --db-revision "$0" --target-head "$1"' \
    "$now_rev" "$rel_head" 2>/dev/null | tr -d '\r' | tail -n 1)" || rc=$?
  if [ "$rc" = "0" ]; then
    DB_AHEAD="$now_rev"; state_init; printf '%s\n' "$now_rev" > "$STATE_DIR/db_ahead"
    info "database at $now_rev is ahead of $RELEASE (head $rel_head); every migration in between is ROLLBACK_SAFE - it starts with MIGRATION_ALLOW_DB_AHEAD=$now_rev"
    return 0
  fi
  info "rollback safety: ${out:-no answer}"
  return 1
}

json_get() {   # json_get <dotted.path>  (stdin: JSON); prints "" when absent, "unparseable" on bad JSON
  python3 -c 'import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("unparseable"); sys.exit(0)
for k in sys.argv[1].split("."):
    d = d.get(k) if isinstance(d, dict) else None
print("" if d is None else d)' "$1"
}

# --- backup seals: HMAC-SHA256 over the ciphertext, key derived from the passphrase ------------------------------
# openssl enc has no authenticated mode; the .hmac sidecar makes tampering with an encrypted file detectable (a .sha256
# can be recomputed by whoever changed the file). Dumps are sealed by scripts/backup/backup.sh where they are made
# (common.sh hmac_file - the same derivation: PBKDF2-HMAC-SHA256, salt "atpmacv1", 200000 rounds, 32 bytes, separate
# from the encryption key); this side seals the files archive and checks every seal. The passphrase reaches python
# through its environment only.
# $1 = the file's name: the seal covers name + contents, so a sealed file cannot be replayed under another name.
mac_stream() {
  ATP_MAC_PASS="$(env_get BACKUP_ENCRYPTION_PASSPHRASE)" python3 -c 'import hashlib, hmac, os, sys
key = hashlib.pbkdf2_hmac("sha256", os.environ["ATP_MAC_PASS"].encode(), b"atpmacv1", 200000, 32)
mac = hmac.new(key, os.path.basename(sys.argv[1]).encode() + b"\n", digestmod="sha256")
for chunk in iter(lambda: sys.stdin.buffer.read(1 << 20), b""):
    mac.update(chunk)
print(mac.hexdigest())' "$1"
}
# ok | missing | mismatch, for a dump in the volume ($1 = path under /backups).
dump_mac_state() {
  local f="$1" want got
  want="$(dc run --rm --no-deps -T --entrypoint cat backup "/backups/$f.hmac" 2>/dev/null | tr -d '\r\n' || true)"
  [ -n "$want" ] || { printf 'missing'; return 0; }
  got="$(dc run --rm --no-deps -T --entrypoint cat backup "/backups/$f" | mac_stream "$f")" || { printf 'mismatch'; return 0; }
  if [ "$got" = "$want" ]; then printf 'ok'; else printf 'mismatch'; fi
}
host_mac_state() {
  local f="$1" want
  [ -s "$f.hmac" ] || { printf 'missing'; return 0; }
  want="$(tr -d '\r\n' < "$f.hmac")"
  local name; name="$(basename "$f")"
  if [ "$(mac_stream "$name" < "$f")" = "$want" ]; then printf 'ok'; else printf 'mismatch'; fi
}

# --- R2 / off-site -----------------------------------------------------------------------------------------------
# configured | not_configured:<reason>. Never prints a credential.
offsite_state() {
  local remote; remote="$(env_get OFFSITE_REMOTE)"
  case "$remote" in
    offsite:*)
      local k
      for k in OFFSITE_S3_ENDPOINT OFFSITE_S3_ACCESS_KEY OFFSITE_S3_SECRET_KEY; do
        env_set "$k" || { printf 'not_configured:%s is empty' "$k"; return 0; }
      done
      printf 'configured' ;;
    b2:*)
      if env_set OFFSITE_B2_KEY_ID && env_set OFFSITE_B2_APPLICATION_KEY; then printf 'configured'; return 0; fi
      printf 'not_configured:OFFSITE_B2_KEY_ID / OFFSITE_B2_APPLICATION_KEY are empty' ;;
    /*) printf 'not_configured:OFFSITE_REMOTE is a local directory (not off-site)' ;;
    "") printf 'not_configured:OFFSITE_REMOTE is empty (Hostinger: offsite:atp-backups)' ;;
    *) printf 'not_configured:OFFSITE_REMOTE=%s has no remote on this host (Hostinger: offsite:<bucket> for R2)' "${remote%%:*}:" ;;
  esac
}
restore_dir() { local d; d="$(env_get OFFSITE_RESTORE_DIR)"; printf '%s' "${d:-/var/backups/atp-restore-test}"; }

# --- checks ------------------------------------------------------------------------------------------------------
version_ge() { [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n 1)" = "$2" ]; }

check_tools() {
  local t missing=""
  for t in docker git curl python3 sha256sum flock openssl tar awk; do
    command -v "$t" >/dev/null 2>&1 || missing="$missing $t"
  done
  [ -z "$missing" ] || die "missing on this host:$missing - run the bootstrap block as root first (runbook section 2)" 10
  docker info >/dev/null 2>&1 || die "docker is not reachable as $(id -un) - is the user in the docker group? (log out and in after bootstrap)" 10
  local v; v="$(docker compose version --short 2>/dev/null | sed 's/^v//')"
  [ -n "$v" ] || die "docker compose plugin missing" 10
  version_ge "$v" "$COMPOSE_MIN" || die "docker compose $v is older than $COMPOSE_MIN (the compose files use !override)" 10
  ok "tools present, docker compose $v"
}

check_repo() {
  [ -d "$ATP_DIR/.git" ] || die "$ATP_DIR is not a git clone - run the install block (runbook section 3)" 10
  local dirty
  dirty="$(git -C "$ATP_DIR" status --porcelain --untracked-files=no)"
  [ -z "$dirty" ] || die "tracked files in $ATP_DIR were changed on the server - the deploy refuses to overwrite them:
$dirty
Keep host settings in .env only; 'git -C $ATP_DIR diff' shows the change." 1
  ok "repository $ATP_DIR clean"
}

# Required keys present, no dev default, mode 600, key unchanged. Prints key names only.
#   $1: record (deploy: records the fingerprint on the first run) | report (health: the key check reports, not stops)
check_env() {
  local mode_arg="${1:-}"
  [ -f "$ENV_FILE" ] || die "$ENV_FILE does not exist. Create it yourself on the server (never in chat):
    cp $ATP_DIR/.env.example $ENV_FILE && chmod 600 $ENV_FILE && nano $ENV_FILE
then fill: ${REQUIRED_KEYS[*]} (runbook section 4)." 10
  local mode; mode="$(stat -c '%a' "$ENV_FILE")"
  case "$mode" in 600|400) ;; *) die "$ENV_FILE is mode $mode - it holds secrets: run 'chmod 600 $ENV_FILE' and retry" 10 ;; esac
  [ "$(stat -c '%u' "$ENV_FILE")" = "$(id -u)" ] || die "$ENV_FILE is not owned by $(id -un)" 10
  local k missing=""
  for k in "${REQUIRED_KEYS[@]}"; do env_set "$k" || missing="$missing $k"; done
  [ "$(env_get POSTGRES_PASSWORD)" != "atp_dev_password" ] || missing="$missing POSTGRES_PASSWORD(dev-default)"
  [ "$(env_get JWT_SECRET_KEY)" != "dev-only-insecure-secret-change-me" ] || missing="$missing JWT_SECRET_KEY(dev-default)"
  [ -z "$missing" ] || die "fill these in $ENV_FILE (names only shown):$missing  - see runbook section 4" 10
  local caddy; caddy="$(env_get CADDYFILE)"
  case "${caddy:-Caddyfile}" in Caddyfile|Caddyfile.ip) ;; *) die "CADDYFILE must be Caddyfile (domain) or Caddyfile.ip (server IP)" 10 ;; esac
  ok ".env: mode $mode, every required key set (${#REQUIRED_KEYS[@]})"

  [ "$mode_arg" = "report" ] && return 0      # health reports the key check itself (check_key_live), it does not stop
  if [ -f "$STATE_DIR/secrets_key.fingerprint" ]; then
    [ "$(cat "$STATE_DIR/secrets_key.fingerprint")" = "$(key_fingerprint)" ] || die "SECRETS_ENCRYPTION_KEY in .env is NOT the key this server deployed with.
Every stored broker / AI credential depends on it. Put the previous value back in .env - never rotate it from a
deploy (a rotation is a separate procedure: scripts/reencrypt_secrets.py, docs/OPERATIONS.md)." 20
    ok "SECRETS_ENCRYPTION_KEY unchanged since the first deploy"
  elif [ "$mode_arg" = "record" ]; then
    if [ "$DRY_RUN" = "1" ]; then info "would record a salted fingerprint of SECRETS_ENCRYPTION_KEY (first deploy)"
    else
      state_init
      [ -s "$STATE_DIR/key_salt" ] || { openssl rand -hex 16 > "$STATE_DIR/key_salt"; chmod 600 "$STATE_DIR/key_salt"; }
      key_fingerprint > "$STATE_DIR/secrets_key.fingerprint"; chmod 600 "$STATE_DIR/secrets_key.fingerprint"
      ok "SECRETS_ENCRYPTION_KEY fingerprint recorded (salted scrypt, first deploy) - later runs refuse a different key"
    fi
  else
    info "SECRETS_ENCRYPTION_KEY fingerprint not recorded yet (the first deploy records it)"
  fi
}

report_settings() {
  local profiles; profiles="$(env_get COMPOSE_PROFILES)"
  info "compose profiles: ${profiles:-none} (from .env COMPOSE_PROFILES - never added here)"
  local k on=""
  for k in "${LIVE_SWITCHES[@]}"; do if is_true "$(env_get "$k")"; then on="$on $k"; fi; done
  if [ -n "$on" ]; then warn "LIVE switches set in .env:$on - left as they are (Phase 0 is PAPER only)"; else ok "no LIVE switch set in .env"; fi
  local r2; r2="$(offsite_state)"
  if [ "$r2" = "configured" ]; then ok "off-site backups: R2 configured ($(env_get OFFSITE_REMOTE))"
  else warn "off-site backups: ${r2#not_configured:} - owner blocker: R2 bucket/token in .env (runbook section 6); backups stay on this server until then"; fi
  if env_set HISTORY_OWNER_TENANT_ID && ! env_set HISTORY_OWNER_TENANT_NAME; then
    warn "HISTORY_OWNER_TENANT_ID is set without HISTORY_OWNER_TENANT_NAME - the 1-minute history import refuses until both match"
  fi
  local n=0
  for k in R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY R2_BUCKET R2_ENDPOINT; do if env_set "$k"; then n=$((n + 1)); fi; done
  case "$n" in 0) info "history import (HB0) R2 token: not set (import stays off)";; 4) ok "history import (HB0) R2 token: set (read-only, used only by the importer)";;
    *) warn "history import (HB0): only $n of R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / R2_BUCKET / R2_ENDPOINT set";; esac
}

lock() {
  state_init
  [ "$DRY_RUN" = "1" ] && return 0
  exec 9>"$STATE_DIR/lock"
  flock -w "${1:-0}" 9 || die "another atp.sh run holds $STATE_DIR/lock - wait for it to finish" 1
}

# Market sessions and open positions from the app's own calendar (every exchange family, holidays, IST), read with
# the image of release $1; further arguments go to the script. Prints the JSON line, or nothing when it cannot tell.
session_state() {
  local rel="$1"; shift
  [ -n "$rel" ] || return 0
  # shellcheck disable=SC2016  # "$@" expands inside the container
  dc_as "$rel" run --rm --no-deps -T backend sh -c 'test -f scripts/session_check.py && python scripts/session_check.py "$@"' sh "$@" 2>/dev/null \
    | tr -d '\r' | tail -n 1 || true
}
# The worker restarts only when no session is open with positions open. $2 / $3 = releases whose image may read it.
session_gate() {
  local what="$1" js open pos at
  [ "$DRY_RUN" = "1" ] && { info "would refuse to $what while an exchange session is open with positions open (scripts/session_check.py)"; return 0; }
  js="$(session_state "$2")"
  [ -n "$js" ] || js="$(session_state "${3:-}")"
  if [ -z "$js" ]; then
    if [ "${ATP_ALLOW_SESSION_RESTART:-0}" = "1" ]; then warn "session / position state unknown - continuing (ATP_ALLOW_SESSION_RESTART=1)"; return 0; fi
    die "cannot read the session / position state - refusing to $what (ATP_ALLOW_SESSION_RESTART=1 to override knowingly)" 3
  fi
  read -r open pos at < <(printf '%s' "$js" | python3 -c 'import json, sys
d = json.load(sys.stdin)
print(",".join(d.get("open_sessions") or []) or "none", d.get("open_positions", -1), (d.get("now_ist") or "?").replace(" ", "T"))')
  info "sessions open: $open; open positions: $pos (at $at IST)"
  if [ "$open" != "none" ] && [ "$pos" != "0" ]; then
    if [ "${ATP_ALLOW_SESSION_RESTART:-0}" = "1" ]; then warn "$open open with $pos open position(s) - continuing (ATP_ALLOW_SESSION_RESTART=1)"; return 0; fi
    die "$open session open and $pos position(s) open - refusing to $what under them. Run after the close of every open session (NSE 15:30, MCX 23:30 IST), or set ATP_ALLOW_SESSION_RESTART=1 knowingly." 3
  fi
}

# --- health ------------------------------------------------------------------------------------------------------
# One parse of /health/deep: status database migrations worker redis seconds_since_heartbeat
deep_fields() {
  python3 -c 'import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    d = {}
c = d.get("checks") or {}
w = c.get("worker") or {}
print(d.get("status") or "unparseable", *[(c.get(k) or {}).get("status") or "-" for k in ("database", "migrations", "worker", "redis")],
      w.get("seconds_since_heartbeat", -1))'
}

# Deep health acceptable? ok, or degraded only because the database is ahead after a checked code-only rollback.
deep_ok() {
  local body st db_s mig wk rd hb
  body="$(curl -fsS -m 10 "$API/api/system/health/deep" 2>/dev/null)" || return 1
  read -r st db_s mig wk rd hb < <(printf '%s' "$body" | deep_fields)
  [ "$st" = "ok" ] && return 0
  if [ "$st" = "degraded" ] && { [ -n "$DB_AHEAD" ] || db_ahead_marker; }; then
    [ "$mig" = "behind" ] && [ "$rd" = "ok" ] && [ "$wk" != "stale_market_open" ] && [ "$db_s" = "ok" ] && return 0
  fi
  return 1
}

wait_api() {
  [ "$DRY_RUN" = "1" ] && { info "would wait up to ${HEALTH_TIMEOUT}s for $API/api/system/health/deep"; return 0; }
  local waited=0
  until deep_ok; do
    [ "$waited" -ge "$HEALTH_TIMEOUT" ] && return 1
    sleep "$POLL"; waited=$((waited + POLL))
  done
  ok "API deep health ok after ${waited}s"
}

start_workers() {
  local svcs; mapfile -t svcs < <(worker_services)
  rdc up -d --no-deps --no-build "${svcs[@]}"
  WORKER_STARTED="$(date +%s)"
}

expected_services() {
  printf '%s\n' postgres redis backend worker frontend caddy backup offsite
  if research_on; then printf '%s\n' research-worker; fi
}

health_checks() {
  step "containers"
  local ps svc line state hstate
  ps="$(dc ps --format '{{.Service}}|{{.State}}|{{.Health}}' 2>/dev/null || true)"
  while read -r svc; do
    line="$(printf '%s\n' "$ps" | grep -E "^$svc\|" | head -n 1 || true)"
    state="$(printf '%s' "$line" | cut -d'|' -f2)"; hstate="$(printf '%s' "$line" | cut -d'|' -f3)"
    if [ "$state" != "running" ]; then bad "$svc: ${state:-not running}"
    elif [ -n "$hstate" ] && [ "$hstate" != "healthy" ]; then bad "$svc: running, health $hstate"
    else ok "$svc: running${hstate:+, $hstate}"; fi
  done < <(expected_services)
  local workers; workers="$(printf '%s\n' "$ps" | grep -c '^worker|' || true)"
  [ "${workers:-0}" -le 1 ] || bad "$workers trading worker containers - exactly one may run (ADR-0010)"

  step "API"
  local body st db_s mig wk rd hb
  if body="$(curl -fsS -m 5 "$API/api/system/health" 2>/dev/null)" && [ "$(printf '%s' "$body" | json_get status)" = "ok" ]; then ok "/api/system/health ok"
  else bad "/api/system/health does not answer ok on $API"; fi
  if body="$(curl -sS -m 10 "$API/api/system/health/deep" 2>/dev/null)"; then
    read -r st db_s mig wk rd hb < <(printf '%s' "$body" | deep_fields)
    info "deep: $st (database $db_s, migrations $mig, worker $wk, redis $rd)"
    if [ "$st" = "ok" ]; then ok "deep health ok"
    elif deep_ok; then warn "deep health degraded only because the database is ahead of this code (after a checked code-only rollback) - deploy the newer release when fixed"
    else bad "deep health $st"; fi
    if [ "$WORKER_STARTED" -gt 0 ]; then
      local since=$(( $(date +%s) - WORKER_STARTED ))
      if [ "$wk" = "ok" ] && [ "$hb" -ge 0 ] && [ "$hb" -le "$since" ]; then ok "the restarted worker has reported (heartbeat ${hb}s ago)"
      else bad "the worker restarted ${since}s ago has not reported a heartbeat yet (worker $wk, last ${hb}s ago)"; fi
    fi
  else bad "/api/system/health/deep unreachable"; fi

  step "public HTTPS through Caddy"
  local domain code; domain="$(env_get DOMAIN)"
  code="$(curl -sk -o /dev/null -m 10 -w '%{http_code}' --resolve "$domain:443:127.0.0.1" "https://$domain/api/system/health" 2>/dev/null || true)"
  if [ "$code" = "200" ]; then ok "https://$domain/api/system/health -> 200"; else bad "https://$domain/api/system/health -> ${code:-no answer} (caddy logs: docker compose ... logs caddy)"; fi

  step "release"
  local cur; cur="$(current_sha)"
  info "running: ${cur:0:12}${cur:+ }$(git -C "$ATP_DIR" log -1 --format='%cd %s' --date=format:'%Y-%m-%d %H:%M' "${cur:-HEAD}" 2>/dev/null | cut -c1-80)"
  if [ -n "$cur" ] && [ "$(git -C "$ATP_DIR" rev-parse HEAD)" != "$cur" ]; then warn "checkout $(git -C "$ATP_DIR" rev-parse --short=12 HEAD) differs from the running release ${cur:0:12}"; fi
  if db_ahead_marker; then warn "database is ahead of the running code since a rollback (revision $(cat "$STATE_DIR/db_ahead"))"; fi

  step "secrets key"
  check_key_live
  step "flags and LIVE (read-only)"
  report_flags
  step "backups"
  local last age
  last="$(dc exec -T backup cat /backups/LAST_BACKUP_OK 2>/dev/null || true)"
  if [ -z "$last" ]; then warn "no database backup yet (LAST_BACKUP_OK missing) - run: atp.sh backup"
  else
    age=$(( ($(date -u +%s) - $(date -u -d "$last" +%s 2>/dev/null || echo 0)) / 3600 ))
    if [ "$age" -le 26 ]; then ok "last database backup $last (${age} h ago)"; else warn "last database backup $last is ${age} h old (alert at 26 h)"; fi
  fi
  if [ "$(offsite_state)" = "configured" ]; then
    local sync; sync="$(dc logs --no-color --tail 300 offsite 2>/dev/null | grep -E 'synced|FAILED' | tail -n 1 || true)"
    case "$sync" in *FAILED*) bad "off-site copy failing: $sync" ;; *synced*) ok "off-site: $sync" ;; *) warn "off-site: no sync logged yet" ;; esac
  else warn "off-site: $(offsite_state | cut -d: -f2-)"; fi
  if [ -f "$STATE_DIR/last_restore_check" ]; then info "last restore check: $(cat "$STATE_DIR/last_restore_check")"; fi

  step "host"
  local ports
  ports="$(ss -tlnH 2>/dev/null | awk '{print $4}' | grep -vE '^(127\.|\[::1\]|\[::ffff:127\.)' | sed -E 's/.*:([0-9]+)$/\1/' | sort -un | tr '\n' ' ' || true)"
  local p extra=""
  for p in $ports; do case "$p" in 22|80|443) ;; *) extra="$extra $p" ;; esac; done
  if [ -n "$extra" ]; then bad "public TCP ports besides 22/80/443:$extra (Docker publishes past ufw)"; else ok "public TCP ports: ${ports:-none seen}"; fi
  local used; used="$(df -P "$ATP_DIR" | awk 'NR==2 {gsub("%","",$5); print $5}')"
  if [ "${used:-0}" -ge 95 ]; then bad "disk ${used}% used"; elif [ "${used:-0}" -ge 85 ]; then warn "disk ${used}% used"; else ok "disk ${used}% used"; fi
}

check_key_live() {
  if [ -f "$STATE_DIR/secrets_key.fingerprint" ]; then
    if [ "$(cat "$STATE_DIR/secrets_key.fingerprint")" = "$(key_fingerprint)" ]; then ok "fingerprint matches the first deploy"
    else bad "SECRETS_ENCRYPTION_KEY in .env differs from the one this host deployed with - put the previous value back"; fi
  else warn "no key fingerprint recorded yet (the first deploy records it)"; fi
  if dc exec -T backend test -f scripts/check_secrets_key.py >/dev/null 2>&1; then
    local out
    if out="$(dc exec -T backend python scripts/check_secrets_key.py 2>&1 | tail -n 1)"; then ok "$out"; else bad "$out"; fi
  else info "key check script not in this image (older release) - skipped"; fi
}

# Flags as the running release resolves them (scripts/flag_posture.py, read-only). LIVE reachable = owner action.
report_flags() {
  local js live live_tenants po_known po_on stored
  js="$(dc exec -T backend sh -c 'test -f scripts/flag_posture.py && python scripts/flag_posture.py' 2>/dev/null | tr -d '\r' | tail -n 1 || true)"
  if [ -z "$js" ]; then
    warn "flag posture unknown (API not running, or an image without scripts/flag_posture.py) - check Admin > Controls"
    OWNER_ACTION="${OWNER_ACTION:+$OWNER_ACTION; }confirm in Admin > Controls that live_trading is off"
    report_settings; return 0
  fi
  read -r live live_tenants po_known po_on stored phase < <(printf '%s' "$js" | python3 -c 'import json, sys
d = json.load(sys.stdin)
lt, po = d.get("live_trading") or {}, d.get("phase0_paper_only") or {}
print(int(bool(lt.get("on", True))), int(lt.get("tenants") or 0), int(bool(po.get("known"))), int(bool(po.get("on"))), ",".join(d.get("stored_on") or []) or "none",
      "".join(ch for ch in str(d.get("deployment_phase") or "-") if ch.isalnum() or ch in "_-")[:20] or "-")')
  info "flags stored on: $stored"
  case "$phase" in
    phase0) ok "deployment phase: phase0 (first boot seeds live_trading OFF and phase0_paper_only ON - nothing for the owner to set)" ;;
    -) info "deployment phase: not reported by this release" ;;
    *) warn "deployment phase: $phase (ATP_DEPLOYMENT_PHASE in .env) - the Phase 0 defaults are not seeded on this host" ;;
  esac
  if [ "$live" = "1" ] || [ "$live_tenants" != "0" ]; then
    if [ "$live" = "1" ]; then warn "live_trading is ON for everyone: Phase 0 has no LIVE"; else warn "live_trading is ON for $live_tenants organisation(s): Phase 0 has no LIVE"; fi
    OWNER_ACTION="${OWNER_ACTION:+$OWNER_ACTION; }turn live_trading OFF in Admin > Controls"
  else ok "live_trading is off"; fi
  if [ "$po_known" = "1" ]; then
    if [ "$po_on" = "1" ]; then ok "phase0_paper_only is on (new LIVE entries refused at the order chokepoint)"
    else
      warn "phase0_paper_only is OFF"
      OWNER_ACTION="${OWNER_ACTION:+$OWNER_ACTION; }turn phase0_paper_only ON in Admin > Controls"
    fi
  else info "phase0_paper_only: not in this release"; fi
  report_settings
}

health_summary() {
  printf '\n'
  if [ "$FAILS" -gt 0 ]; then printf 'health: FAILED - %s failure(s), %s warning(s)\n' "$FAILS" "$WARNS"; return 1; fi
  printf 'health: OK - %s warning(s)\n' "$WARNS"
}

# Owner action left (exit 10): flags only the owner may change. Nothing here changes them (the Phase 0 seed is the
# release's own first boot, not this script).
owner_action_exit() {
  [ -n "$OWNER_ACTION" ] || return 0
  printf '\nOWNER ACTION NEEDED: %s (runbook section 4a). This script never changes a flag.\n' "$OWNER_ACTION"
  exit 10
}

# Health after a restart, retried until healthy or the timeout (the restarted worker needs a cycle to report).
health_until_ok() {
  local waited=0
  while :; do
    FAILS=0; WARNS=0; OWNER_ACTION=""
    health_checks > "$STATE_DIR/logs/health-last.txt" 2>&1 || true
    [ "$FAILS" = "0" ] && break
    [ "$waited" -ge "$HEALTH_TIMEOUT" ] && break
    sleep "$POLL"; waited=$((waited + POLL))
  done
  cat "$STATE_DIR/logs/health-last.txt"
}

# --- commands ----------------------------------------------------------------------------------------------------
cmd_health() {
  [ "$(id -u)" != "0" ] || die "run as the deploy user (atp), not root" 2
  check_tools
  check_env report
  RELEASE="$(release_of "$(current_sha)")"
  health_checks
  health_summary || exit 1
  owner_action_exit
}

cmd_bootstrap() {
  if [ "$(id -u)" = "0" ]; then
    [ -f "$SCRIPT_DIR/hostinger/bootstrap.sh" ] || die "deploy/hostinger/bootstrap.sh not found next to this script" 1
    step "host bootstrap as root (deploy/hostinger/bootstrap.sh: user, SSH keys only, ufw 22/80/443, fail2ban, Docker, swap, IST)"
    run bash "$SCRIPT_DIR/hostinger/bootstrap.sh"
    return 0
  fi
  step "bootstrap checks (as $(id -un))"
  check_tools
  local tz; tz="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
  if [ "$tz" = "Asia/Kolkata" ]; then ok "timezone Asia/Kolkata"; else warn "timezone ${tz:-unknown} (bootstrap sets Asia/Kolkata)"; fi
  if swapon --show --noheadings 2>/dev/null | grep -q .; then ok "swap on"; else warn "no swap (bootstrap adds 2 GB)"; fi
  local d local_dir
  local_dir="$(env_get OFFSITE_LOCAL_DIR)"
  for d in "$(restore_dir)" "${local_dir:-/var/backups/atp-offsite}"; do
    if [ -d "$d" ] && [ -w "$d" ]; then ok "$d writable"; else die "$d missing or not writable - run the bootstrap block as root (runbook section 2)" 10; fi
  done
  check_repo
  check_env
  report_settings
}

cmd_deploy() {
  local ref="${1:-origin/main}"
  [ "$(id -u)" != "0" ] || die "run as the deploy user (atp), not root" 2
  lock 0
  step "preflight"
  check_tools
  check_repo
  check_env record
  report_settings

  step "release"
  printf '    + git -C %s fetch -q --tags origin\n' "$ATP_DIR"
  git -C "$ATP_DIR" fetch -q --tags origin \
    || die "git fetch failed - is the read-only deploy key still on GitHub? (ssh -T git@github.com)" 1   # also under --dry-run: only origin/* moves
  local sha prev head_before
  sha="$(git -C "$ATP_DIR" rev-parse --verify -q "$ref^{commit}")" || die "unknown ref '$ref'" 2
  if ! git -C "$ATP_DIR" merge-base --is-ancestor "$sha" origin/main && [ "${ATP_ALLOW_UNMERGED:-0}" != "1" ]; then
    die "$ref ($sha) is not on origin/main - deploy merged code only (ATP_ALLOW_UNMERGED=1 to override knowingly)" 2
  fi
  git -C "$ATP_DIR" cat-file -e "$sha:deploy/atp.sh" 2>/dev/null || die "$ref predates deploy/atp.sh - cannot deploy it with this bundle" 2
  prev="$(current_sha)"
  head_before="$(git -C "$ATP_DIR" rev-parse HEAD)"
  RELEASE="$(release_of "$sha")"
  info "deploying ${sha:0:12} $(git -C "$ATP_DIR" log -1 --format='%s' "$sha" | cut -c1-80)"
  info "running now: ${prev:0:12}${prev:+ }${prev:-nothing recorded (first deploy)}"
  if [ "$prev" = "$sha" ]; then info "same release as running - converging (idempotent re-run)"; fi
  RESTORE_CHECKOUT="${prev:-$head_before}"
  run git -C "$ATP_DIR" checkout -q --detach "$sha"

  step "images atp-*:$RELEASE (built from the commit, reused by a rollback; nothing running is touched)"
  if [ "$DRY_RUN" != "1" ] && images_exist "$RELEASE"; then ok "images for $RELEASE already built"
  else rdc build --pull; fi

  # The session gate runs before anything is (re)started - `up` may recreate Postgres or Redis when their config changed.
  step "session gate"
  if [ "$DRY_RUN" = "1" ]; then session_gate "restart the worker" "$RELEASE"
  elif dc ps --status running --services 2>/dev/null | grep -qx postgres; then
    local running_rev; running_rev="$(db_revision)"
    [ "$running_rev" != "unknown" ] || die "cannot read the database revision - nothing was changed" 1
    if [ "$running_rev" != "-" ]; then session_gate "restart the worker" "$RELEASE" "$(release_of "${prev:-}")"; else info "empty database - nothing to protect"; fi
  else info "Postgres not running yet (first start) - nothing to protect"; fi

  step "database and cache"
  rdc up -d --wait postgres redis

  step "migrations"
  local before="" after="" head="" flags_before backup_file="-"
  if [ "$DRY_RUN" = "1" ]; then
    info "would compare the database revision with the image's alembic head; if behind: encrypted, sealed pre-migration backup, then scripts/migrate_guard.py"
    flags_before="dry-run"
  else
    before="$(db_revision)"; head="$(code_head)"
    [ "$before" != "unknown" ] || die "cannot read the database revision - nothing was changed" 1
    [ -n "$head" ] || die "could not read the alembic head from atp-backend:$RELEASE" 1
    flags_before="$(flags_snapshot)"
    [ "$flags_before" != "unknown" ] || die "cannot read the feature flags - nothing was changed" 1
    info "database at ${before}, code head ${head}"
    if [ "$before" = "$head" ]; then
      ok "schema at head - no migration, no pre-migration backup needed"
    else
      step "pre-migration backup (encrypted, sealed, kept out of the normal retention)"
      rdc run --rm --no-deps -T --entrypoint sh backup /scripts/backup.sh || die "pre-migration backup failed - nothing was migrated or restarted" 1
      local f; f="$(dc run --rm --no-deps -T --entrypoint sh backup -c 'readlink /backups/latest' | tr -d '\r' | tail -n 1)"
      [ -n "$f" ] || die "pre-migration backup not found after backup.sh" 1
      [ "$(dump_mac_state "$f")" = "ok" ] || die "the pre-migration backup $f has no valid seal - nothing was migrated" 1
      # shellcheck disable=SC2016  # $0 expands inside the container
      rdc run --rm --no-deps -T --entrypoint sh backup -c \
        'mkdir -p /backups/pre-migration && for x in "$0" "$0.sha256" "$0.hmac"; do ln -f "/backups/$x" "/backups/pre-migration/$x"; done' "$f" \
        || die "could not keep the pre-migration backup out of retention" 1
      backup_file="pre-migration/$f"
      ok "pre-migration backup: $backup_file"
      if [ "$(offsite_state)" = "configured" ]; then
        if rdc run --rm --no-deps -T offsite --once; then ok "copied to R2"; else warn "R2 copy failed - the local backup stands; the hourly copy retries"; fi
      else warn "R2 not configured - the pre-migration backup is on this server only"; fi
      step "alembic upgrade head (scripts/migrate_guard.py)"
      local rc=0
      rdc run --rm --no-deps -T backend python scripts/migrate_guard.py || rc=$?
      [ "$rc" = "3" ] && die "migration refused: NSE session open. Nothing was restarted; the old release still runs. Retry after the close." 3
      if [ "$rc" != "0" ]; then
        log_release failed "$sha" "${prev:--}" "$backup_file" "$before" "$(db_revision)"
        die "migration failed (exit $rc). The old release still runs; the database may be partly migrated - 'atp.sh rollback --restore-db' puts back $backup_file (runbook 'Troubleshooting')." 1
      fi
      after="$(db_revision)"
      if [ "$after" != "$head" ]; then
        log_release failed "$sha" "${prev:--}" "$backup_file" "$before" "$after"
        die "after migrating the database is at $after, expected $head - 'atp.sh rollback --restore-db' puts back $backup_file" 1
      fi
      ok "database migrated $before -> $after"
    fi
    [ -n "$after" ] || after="$before"
    DB_AHEAD=""; rm -f "$STATE_DIR/db_ahead"        # the database is now exactly at this release's head
  fi

  step "rolling restart: API first"
  rdc up -d --no-deps --no-build backend
  if ! wait_api; then
    printf '    the new API did not pass deep health in %ss:\n' "$HEALTH_TIMEOUT"
    dc logs --no-color --tail 40 backend 2>&1 | sed 's/^/      /' || true
    if [ "$before" != "$after" ]; then log_release failed "$sha" "${prev:--}" "$backup_file" "$before" "$after"; fi
    if [ -n "$prev" ] && images_exist "$(release_of "$prev")"; then
      step "putting the previous API back (${prev:0:12})"
      run git -C "$ATP_DIR" checkout -q --detach "$prev"
      local new_release="$RELEASE"
      RELEASE="$(release_of "$prev")"
      if settle_db_ahead "$new_release"; then
        rdc up -d --no-deps --no-build backend || true
        if wait_api; then info "previous API healthy again - the worker and frontend were not touched"; else info "previous API not healthy either - see runbook 'Troubleshooting'"; fi
      else
        info "the database was migrated past what ${prev:0:12} may safely run on - the new API stays up for inspection."
        info "Undo the migration with: $ATP_DIR/deploy/atp.sh rollback --restore-db"
      fi
    fi
    die "deploy of ${sha:0:12} failed at the API health check" 1
  fi

  step "worker first, then frontend and the rest (exactly one worker)"
  start_workers
  rdc up -d --no-build
  RESTORE_CHECKOUT=""
  if [ "$DRY_RUN" != "1" ]; then
    if [ "$prev" = "$sha" ]; then log_release converge "$sha" "$prev" "$backup_file" "${before:--}" "${after:--}"
    else log_release deploy "$sha" "${prev:--}" "$backup_file" "${before:--}" "${after:--}"; fi
    printf '%s\n' "$sha" > "$STATE_DIR/current"
    rm -f "$STATE_DIR/db_ahead"
    record_images "$sha"
  fi

  step "health"
  if [ "$DRY_RUN" != "1" ]; then
    health_until_ok
    local flags_after change; flags_after="$(flags_snapshot)"
    if [ "$flags_before" = "unknown" ] || [ "$flags_after" = "unknown" ]; then bad "could not read the feature flags before/after the deploy - check Admin > Controls"
    else
      change="$(flags_change "$flags_before" "$flags_after")"
      case "$change" in
        unchanged) ok "feature flags unchanged by the deploy" ;;
        seeded\ *) ok "feature flags unchanged by the deploy, except the Phase 0 first-boot seed of ${change#seeded } (live_trading OFF, phase0_paper_only ON; audited as phase0_flag_seeded)" ;;
        *) bad "feature flags CHANGED during the deploy - check Admin > Controls and the audit log" ;;
      esac
    fi
  else
    info "would run every health check and compare the feature flags before/after"
  fi

  cmd_schedule quiet
  prune_images
  if [ "$DRY_RUN" = "1" ]; then printf '\ndry-run: nothing changed (deploy %s would follow the steps above)\n' "${sha:0:12}"; return 0; fi
  if ! health_summary; then
    printf '\nThe release runs but is not healthy. Roll back with:\n    %s/deploy/atp.sh rollback\n' "$ATP_DIR"
    exit 1
  fi
  printf 'deployed %s\n' "${sha:0:12}"
  if [ -n "$OWNER_ACTION" ]; then printf 'owner action still needed: %s\n' "$OWNER_ACTION"; fi
}

record_images() {
  local img out=""
  for img in postgres:17-alpine redis:7-alpine caddy:2-alpine rclone/rclone:1; do
    out="$out $img=$(docker image inspect --format '{{index .RepoDigests 0}}' "$img" 2>/dev/null | sed 's/.*@//' | cut -c1-19)"
  done
  for img in atp-backend atp-worker atp-frontend atp-backup; do
    out="$out $img=$(docker image inspect --format '{{.Id}}' "$img:$RELEASE" 2>/dev/null | sed 's/^sha256://' | cut -c1-19)"
  done
  printf '%s %s images%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$out" >> "$STATE_DIR/images.log"
}

prune_images() {
  [ -f "$STATE_DIR/releases.log" ] || return 0
  local keep img tag
  keep="$(awk '{print substr($3, 1, 12)}' "$STATE_DIR/releases.log" | tac | awk '!seen[$0]++' | head -n "$KEEP_RELEASES" | tr '\n' ' ')"
  for img in "${APP_IMAGES[@]}"; do
    while read -r tag; do
      [ -n "$tag" ] || continue
      case "$tag" in *[!0-9a-f]*|"") continue ;; esac
      case " $keep " in *" $tag "*) continue ;; esac
      run docker image rm "$img:$tag" >/dev/null 2>&1 || true
    done < <(docker image ls --format '{{.Tag}}' "$img" 2>/dev/null)
  done
  # pre-migration dumps: kept for the last KEEP_RELEASES releases that recorded one, the rest removed
  local keep_dumps listed f
  keep_dumps="$(awk '$5 ~ /^pre-migration\// {sub("pre-migration/", "", $5); print $5}' "$STATE_DIR/releases.log" | tac | awk '!seen[$0]++' | head -n "$KEEP_RELEASES" | tr '\n' ' ')"
  [ "$DRY_RUN" = "1" ] && return 0
  listed="$(dc run --rm --no-deps -T --entrypoint sh backup -c 'ls -1 /backups/pre-migration 2>/dev/null' 2>/dev/null | tr -d '\r' || true)"
  for f in $listed; do
    case "$f" in *.sha256|*.hmac) continue ;; esac
    case " $keep_dumps " in *" $f "*) continue ;; esac
    # shellcheck disable=SC2016  # $0 expands inside the container
    rdc run --rm --no-deps -T --entrypoint sh backup -c 'rm -f "/backups/pre-migration/$0" "/backups/pre-migration/$0.sha256" "/backups/pre-migration/$0.hmac"' "$f" >/dev/null 2>&1 || true
  done
}

# The UTC time a dump was taken, from its name (atp_<db>_<YYYYmmddTHHMMSSZ>.dump[.enc]) -> ISO-8601.
dump_time() { basename "$1" | sed -nE 's/.*_([0-9]{4})([0-9]{2})([0-9]{2})T([0-9]{2})([0-9]{2})([0-9]{2})Z\.dump.*/\1-\2-\3T\4:\5:\6Z/p'; }

cmd_rollback() {
  local to="" restore_db=0
  while [ $# -gt 0 ]; do
    case "$1" in --to) to="${2:-}"; [ -n "$to" ] || die "--to needs a commit" 2; shift 2 ;; --restore-db) restore_db=1; shift ;; *) die "unknown rollback option $1" 2 ;; esac
  done
  [ "$(id -u)" != "0" ] || die "run as the deploy user (atp), not root" 2
  if [ -n "$to" ] && [ "$restore_db" = "1" ]; then die "--restore-db undoes exactly one release: roll back one release at a time (no --to)" 2; fi
  lock 0
  step "preflight"
  check_tools
  check_repo
  check_env
  local cur line target backup rev_before rev_after mode="release"
  cur="$(current_sha)"
  [ -n "$cur" ] || die "no release recorded in $STATE_DIR/current - nothing to roll back" 2
  # A deploy that failed after migrating left the database ahead of the running release: --restore-db undoes that
  # migration and keeps the code (mode "failed").
  local lkind="" lsha="" lprev="" lbackup="" lbefore="" lafter=""
  if [ -s "$STATE_DIR/releases.log" ]; then read -r _ lkind lsha lprev lbackup lbefore lafter < <(tail -n 1 "$STATE_DIR/releases.log"); fi
  if [ "$restore_db" = "1" ] && [ "$lkind" = "failed" ] && [ "$lprev" = "$cur" ] && [ "${lbackup:--}" != "-" ]; then
    mode="failed"; target="$cur"; backup="$lbackup"; rev_before="$lbefore"; rev_after="$lafter"
  else
    line="$(release_line_for "$cur")"
    [ -n "$line" ] || die "no deploy of ${cur:0:12} in $STATE_DIR/releases.log - name the target: rollback --to <sha>" 2
    read -r _ _ _ target backup rev_before rev_after <<<"$line"
    if [ -n "$to" ]; then target="$(git -C "$ATP_DIR" rev-parse --verify -q "$to^{commit}")" || die "unknown commit $to" 2; fi
    if [ -z "$target" ] || [ "$target" = "-" ]; then die "${cur:0:12} was the first release deployed here - there is nothing before it" 2; fi
    [ "$target" != "$cur" ] || die "${cur:0:12} is already running" 2
  fi
  git -C "$ATP_DIR" cat-file -e "$target:deploy/atp.sh" 2>/dev/null || die "${target:0:12} predates deploy/atp.sh - roll back to a release deployed with this bundle" 2

  step "plan"
  if [ "$mode" = "failed" ]; then
    info "the deploy of ${lsha:0:12} failed after migrating $rev_before -> $rev_after; ${cur:0:12} keeps running"
    info "--restore-db puts the database back to $backup (taken before that migration)"
  else
    info "running ${cur:0:12} -> roll back to ${target:0:12}"
    if [ "$rev_before" != "$rev_after" ]; then
      info "${cur:0:12} migrated the schema $rev_before -> $rev_after (pre-migration backup: $backup)"
      if [ "$restore_db" = "1" ]; then
        info "--restore-db: the database goes back to $backup; rows written since then stay only in the kept copy"
      else
        info "code-only: allowed only when every migration in between is ROLLBACK_SAFE (checked below); else use --restore-db"
      fi
    else
      info "${cur:0:12} did not change the schema: code-only rollback"
      if [ "$restore_db" = "1" ]; then die "--restore-db is not needed: ${cur:0:12} did not migrate (no pre-migration backup was taken)" 2; fi
    fi
  fi
  if [ "$restore_db" = "1" ] && [ "$backup" = "-" ]; then die "no pre-migration backup recorded for ${cur:0:12}" 2; fi
  session_gate "restart the worker" "$(release_of "$cur")" "$(release_of "$target")"

  local db; db="$(pg_db)"
  if [ "$restore_db" = "1" ] && [ "$DRY_RUN" != "1" ]; then
    local answer="${ATP_CONFIRM_DB:-}"
    if [ -z "$answer" ]; then
      printf '\nType the database name (%s) to replace its contents with %s: ' "$db" "$backup"
      read -r answer </dev/tty || answer=""
    fi
    [ "$answer" = "$db" ] || die "not confirmed - nothing changed" 1
    # What the restore would orphan: positions open now and orders placed after the backup was taken.
    local since js pos orders
    since="$(dump_time "$backup")"
    [ -n "$since" ] || die "cannot read the time of $backup from its name" 1
    js="$(session_state "$(release_of "$cur")" --orders-since "$since")"
    [ -n "$js" ] || die "cannot list open positions and recent orders - refusing to restore" 1
    read -r pos orders < <(printf '%s' "$js" | python3 -c 'import json, sys; d = json.load(sys.stdin); print(d.get("open_positions", -1), d.get("orders_since", -1))')
    info "open positions now: $pos; orders placed since the backup ($since): $orders"
    if [ "$pos" != "0" ] || [ "$orders" != "0" ]; then
      local ack="${ATP_CONFIRM_ORPHANS:-}"
      if [ -z "$ack" ]; then
        printf 'The restore drops %s open position(s) and %s order(s) from the live database (they stay in the kept copy).\nClose them first, or type ORPHAN to continue: ' "$pos" "$orders"
        read -r ack </dev/tty || ack=""
      fi
      [ "$ack" = "ORPHAN" ] || die "not acknowledged - nothing changed (close the open positions first)" 1
    fi
  fi

  RELEASE="$(release_of "$target")"
  step "code ${target:0:12}"
  RESTORE_CHECKOUT="$cur"
  run git -C "$ATP_DIR" checkout -q --detach "$target"
  if [ "$DRY_RUN" != "1" ] && images_exist "$RELEASE"; then ok "images atp-*:$RELEASE present (no rebuild)"
  else rdc build; fi

  local services; mapfile -t services < <(app_services)
  if [ "$restore_db" = "1" ]; then
    local stamp scratch kept
    stamp="$(date -u +%Y%m%d%H%M%S)"; scratch="${db}_rb_${stamp}"; kept="${db}_pre_rollback_${stamp}"
    step "database back to $backup (restored beside the live one first; the live one keeps serving meanwhile)"
    if [ "$DRY_RUN" != "1" ]; then
      local seal; seal="$(dump_mac_state "$backup")"
      case "$seal" in ok) ok "$backup seal verified" ;;
        missing) die "$backup has no seal - refusing to restore it (every dump is sealed by backup.sh); nothing changed" 1 ;;
        *) die "$backup does not match its seal - it was changed after it was taken; nothing restored" 1 ;; esac
    fi
    rdc run --rm --no-deps -T --entrypoint sh backup /scripts/backup.sh || die "safety backup of the current database failed - nothing changed" 1
    rdc exec -T postgres psql -X -q -v ON_ERROR_STOP=1 -U "$(pg_user)" -d postgres -c "CREATE DATABASE \"$scratch\""
    # shellcheck disable=SC2016  # $0 / $ATP_TARGET_DB expand inside the backup container
    rdc run --rm --no-deps -T -e RESTORE_CONFIRM=yes -e ATP_TARGET_DB="$scratch" --entrypoint sh backup -c \
      '. /scripts/common.sh && pg_setup && sh /scripts/restore.sh "/backups/$0" "$(with_db "$PG_URL" "$ATP_TARGET_DB")"' "$backup" \
      || die "restore of $backup failed - the live database is untouched (scratch $scratch left for inspection)" 1
    if [ "$DRY_RUN" != "1" ]; then
      local got; got="$(db_revision "$scratch")"
      [ "$got" = "$rev_before" ] || die "the restored copy is at revision ${got:-none}, expected $rev_before - live database untouched (scratch $scratch kept)" 1
      ok "restored copy at revision $got"
    fi
    step "swap: stop the app, keep the live database as $kept, put the restored copy in its place (one transaction)"
    rdc stop "${services[@]}"
    if ! rdc exec -T postgres psql -X -q -A -t -v ON_ERROR_STOP=1 -U "$(pg_user)" -d postgres \
      -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN ('$db', '$scratch') AND pid <> pg_backend_pid()" \
      -c "BEGIN; ALTER DATABASE \"$db\" RENAME TO \"$kept\"; ALTER DATABASE \"$scratch\" RENAME TO \"$db\"; COMMIT;"; then
      # Nothing was renamed: the release that ran before this rollback goes back on the unchanged database.
      git -C "$ATP_DIR" checkout -q --detach "$cur" || true
      RESTORE_CHECKOUT=""
      RELEASE="$(release_of "$cur")"
      dc up -d --no-deps --no-build "${services[@]}" || true
      die "database swap failed (nothing renamed) - ${cur:0:12} restarted on the unchanged live database; scratch $scratch left for inspection" 1
    fi
    ok "$db is the restored copy; the previous database is kept as $kept (never dropped by this script: runbook section 9)"
  fi

  # Is the database ahead of the target code? Only past ROLLBACK_SAFE migrations, checked by the newer image.
  if ! settle_db_ahead "$(release_of "$cur")"; then
    die "code-only rollback refused: the database ($rev_after) does not descend from ${target:0:12}'s schema, or a migration in between is not ROLLBACK_SAFE. Use: $ATP_DIR/deploy/atp.sh rollback --restore-db" 1
  fi

  step "rolling restart on ${target:0:12}: API first, then the worker at once"
  rdc up -d --no-deps --no-build backend
  wait_api || die "the rolled-back API is not healthy - logs: docker compose ... logs backend; runbook 'Troubleshooting'" 1
  start_workers
  rdc up -d --no-build
  RESTORE_CHECKOUT=""
  if [ "$DRY_RUN" = "1" ]; then info "would run every health check"; printf '\ndry-run: nothing changed\n'; return 0; fi
  if [ "$mode" = "failed" ]; then log_release restore "$target" "$cur" "$backup" "$rev_after" "$(db_revision)"
  else log_release rollback "$target" "$cur" "-" "$rev_after" "$(db_revision)"; fi
  printf '%s\n' "$target" > "$STATE_DIR/current"
  step "health"
  health_until_ok
  health_summary || exit 1
  printf 'rolled back to %s\n' "${target:0:12}"
}

cmd_backup() {
  local verify=0 files_only=0 scheduled=0
  while [ $# -gt 0 ]; do
    case "$1" in --verify) verify=1 ;; --files-only) files_only=1 ;; --scheduled) scheduled=1 ;; *) die "unknown backup option $1" 2 ;; esac
    shift
  done
  [ "$(id -u)" != "0" ] || die "run as the deploy user (atp), not root" 2
  lock "$([ "$scheduled" = "1" ] && echo 1800 || echo 0)"
  check_tools
  check_env
  RELEASE="$(release_of "$(current_sha)")"
  local r2; r2="$(offsite_state)"

  if [ "$files_only" = "0" ]; then
    step "database dump (pg_dump, AES-256, SHA-256 + HMAC seal, retention ${BACKUP_RETENTION_DAYS:-14} days)"
    local fresh=0
    if [ "$scheduled" = "1" ] && [ "$DRY_RUN" != "1" ]; then
      local last; last="$(dc exec -T backup cat /backups/LAST_BACKUP_OK 2>/dev/null || true)"
      if [ -n "$last" ] && [ $(( $(date -u +%s) - $(date -u -d "$last" +%s 2>/dev/null || echo 0) )) -lt 72000 ]; then fresh=1; fi
    fi
    if [ "$fresh" = "1" ]; then ok "the backup service dumped within 20 h - not dumping twice"
    else rdc run --rm --no-deps -T --entrypoint sh backup /scripts/backup.sh || die "database backup failed" 1; fi
    if [ "$DRY_RUN" != "1" ]; then
      local f; f="$(dc run --rm --no-deps -T --entrypoint sh backup -c 'readlink /backups/latest' 2>/dev/null | tr -d '\r' | tail -n 1 || true)"
      if [ -n "$f" ]; then
        if [ "$(dump_mac_state "$f")" = "ok" ]; then ok "$f sealed"; else bad "$f has no valid seal"; fi
      fi
    fi
  fi

  step "files archive (deploy state + BACKUP_EXTRA_DIRS), encrypted + sealed"
  backup_files

  step "off-site copy (R2)"
  if [ "$r2" = "configured" ]; then
    rdc run --rm --no-deps -T offsite --once || die "copy of the database backups to R2 failed" 1
    # shellcheck disable=SC2016  # $OFFSITE_REMOTE expands inside the rclone container
    rdc run --rm --no-deps -T -v "$FILES_BACKUP_DIR:/files:ro" --entrypoint sh offsite -c \
      'rclone copy /files "$OFFSITE_REMOTE/files" --include "atp_files_*" -q' || die "copy of the files archive to R2 failed" 1
    ok "database dumps, WAL and files archive copied to $(env_get OFFSITE_REMOTE)"
  else
    warn "R2 not configured yet (${r2#not_configured:}) - the backups stay on this server. Owner blocker: R2 bucket + token in .env (runbook section 6)"
  fi
  [ "$DRY_RUN" = "1" ] || printf '%s r2=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${r2%%:*}" > "$STATE_DIR/last_backup"
  if [ "$verify" = "1" ]; then cmd_restore_check_inner 0; fi
  if [ "$FAILS" -gt 0 ]; then printf '\nbackup: FAILED - %s failure(s)\n' "$FAILS"; exit 1; fi
  printf '\nbackup: done (%s warning(s))\n' "$WARNS"
}

backup_files() {
  local dir="$FILES_BACKUP_DIR"
  if [ ! -d "$dir" ]; then
    run mkdir -p "$dir" 2>/dev/null || { dir="$HOME/atp-backups/files"; FILES_BACKUP_DIR="$dir"; run mkdir -p "$dir"; }
  fi
  local paths=(".deploy") members=(-C "$ATP_DIR" .deploy) extra p
  extra="$(env_get BACKUP_EXTRA_DIRS)"
  for p in $extra; do
    case "$p" in /*) ;; *) warn "BACKUP_EXTRA_DIRS entry '$p' is not an absolute path - skipped"; continue ;; esac
    if [ -d "$p" ]; then paths+=("$p"); members+=(-C / "${p#/}"); else warn "BACKUP_EXTRA_DIRS entry $p does not exist - skipped"; fi
  done
  local out; out="$dir/atp_files_$(date -u +%Y%m%dT%H%M%SZ).tar.gz.enc"
  info "archive: ${paths[*]} -> $out"
  [ "$DRY_RUN" = "1" ] && return 0
  local pass; pass="$(env_get BACKUP_ENCRYPTION_PASSPHRASE)"
  # The passphrase reaches openssl through its environment only (-pass env:), never argv or output.
  if ! tar --exclude='.deploy/lock' --exclude='.deploy/logs' -czf - "${members[@]}" \
    | ATP_FILES_PASS="$pass" openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:ATP_FILES_PASS -out "$out.part"; then
    rm -f "$out.part"
    die "files archive failed" 1
  fi
  mv "$out.part" "$out"
  sha256sum "$out" | cut -d' ' -f1 > "$out.sha256"
  local name; name="$(basename "$out")"
  mac_stream "$name" < "$out" > "$out.hmac"
  ok "$(basename "$out") ($(wc -c < "$out" | tr -d ' ') bytes, sealed)"
  local keep="${BACKUP_FILES_KEEP:-14}" old
  while read -r old; do
    [ -n "$old" ] || continue
    rm -f "$old" "$old.sha256" "$old.hmac"; info "retention: removed $(basename "$old")"
  done < <(find "$dir" -maxdepth 1 -name 'atp_files_*.tar.gz.enc' | sort -r | tail -n +"$((keep + 1))")   # UTC stamp in the name: newest first
  find "$dir" -maxdepth 1 -name 'atp_files_*.part' -mmin +60 -delete 2>/dev/null || true
}

# Checks the seal, decrypts a files archive and lists it (proves the passphrase and the archive). $1 = local path.
verify_files_archive() {
  local f="$1" pass seal
  [ -f "$f" ] || { bad "files archive $f missing"; return 0; }
  if [ -f "$f.sha256" ] && [ "$(sha256sum "$f" | cut -d' ' -f1)" != "$(cat "$f.sha256")" ]; then bad "$(basename "$f"): sha256 mismatch"; return 0; fi
  seal="$(host_mac_state "$f")"
  case "$seal" in mismatch) bad "$(basename "$f"): does not match its seal (changed after it was written)"; return 0 ;;
    missing) warn "$(basename "$f"): no seal (older archive)" ;; esac
  pass="$(env_get BACKUP_ENCRYPTION_PASSPHRASE)"
  local n
  if n="$(ATP_FILES_PASS="$pass" openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:ATP_FILES_PASS -in "$f" | tar -tzf - | wc -l)"; then
    ok "$(basename "$f"): seal $seal, decrypts, $n entries"
  else bad "$(basename "$f"): does not decrypt / unpack"; fi
}

cmd_restore_check() {
  local local_only=0 require=0 scheduled=0
  while [ $# -gt 0 ]; do
    case "$1" in --local) local_only=1 ;; --require-offsite) require=1 ;; --scheduled) scheduled=1 ;; *) die "unknown restore-check option $1" 2 ;; esac
    shift
  done
  [ "$(id -u)" != "0" ] || die "run as the deploy user (atp), not root" 2
  lock "$([ "$scheduled" = "1" ] && echo 1800 || echo 0)"
  check_tools
  check_env
  RELEASE="$(release_of "$(current_sha)")"
  cmd_restore_check_inner "$local_only" "$require"
  printf '\n'
  if [ "$FAILS" -gt 0 ]; then printf 'restore-check: FAILED - %s failure(s)\n' "$FAILS"; exit 1; fi
  printf 'restore-check: OK - %s warning(s)\n' "$WARNS"
}

cmd_restore_check_inner() {
  local local_only="${1:-0}" require="${2:-0}" r2 src file report status seal="missing"
  r2="$(offsite_state)"
  if [ "$local_only" = "0" ] && [ "$r2" = "configured" ]; then
    src="R2"
    step "newest database backup from R2 -> $(restore_dir)"
    if [ "$DRY_RUN" = "1" ]; then rdc run --rm --no-deps -T -e OFFSITE_REQUIRE_ENCRYPTED=1 --entrypoint sh offsite /scripts/offsite_fetch.sh /restore; file="/restore/<newest>"
    else
      # only encrypted dumps (offsite_fetch.sh), and below only sealed ones: anyone with write access to the bucket could
      # otherwise hand pg_restore a dump of their choosing
      file="$(dc run --rm --no-deps -T -e OFFSITE_REQUIRE_ENCRYPTED=1 --entrypoint sh offsite /scripts/offsite_fetch.sh /restore | tr -d '\r' | tail -n 1)" || { bad "could not fetch a backup from R2"; return 0; }
      case "$file" in *.dump.enc) ;; *) bad "R2 handed back $file, not an encrypted dump - refused"; return 0 ;; esac
      ok "fetched $(basename "$file")"
      seal="$(host_mac_state "$(restore_dir)/$(basename "$file")")"
    fi
  else
    src="local"
    if [ "$r2" != "configured" ]; then
      if [ "$require" = "1" ]; then bad "R2 not configured (${r2#not_configured:}) - --require-offsite"; return 0; fi
      warn "R2 not configured yet (${r2#not_configured:}) - verifying the LOCAL copy only (owner blocker: R2 in .env)"
    fi
    file="latest"
    if [ "$DRY_RUN" != "1" ]; then
      local name; name="$(dc run --rm --no-deps -T --entrypoint sh backup -c 'readlink /backups/latest' 2>/dev/null | tr -d '\r' | tail -n 1 || true)"
      if [ -n "$name" ]; then seal="$(dump_mac_state "$name")"; fi
    fi
  fi
  if [ "$DRY_RUN" != "1" ]; then
    case "$seal" in ok) ok "seal (HMAC) verified" ;;
      missing) bad "backup has no seal - refused (every dump is sealed by backup.sh; an unsealed one did not come from it)"; return 0 ;;
      *) bad "backup does not match its seal - changed after it was taken"; return 0 ;; esac
  fi

  step "restore into a scratch database, compare row counts, check the audit chain, drop it"
  if [ "$DRY_RUN" = "1" ]; then rdc run --rm --no-deps -T -e VERIFY_KEEP_SCRATCH=1 --entrypoint sh backup /scripts/verify_backup.sh "$file"; return 0; fi
  report="$(dc run --rm --no-deps -T -e VERIFY_KEEP_SCRATCH=1 --entrypoint sh backup /scripts/verify_backup.sh "$file" 2>"$STATE_DIR/logs/verify-stderr.txt" | tr -d '\r' | tail -n 1)" || true
  local copy_rev
  read -r status SCRATCH_DB copy_rev < <(printf '%s' "$report" | python3 -c 'import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    d = {}
print(d.get("status") or "none", d.get("restored_into") or "-", (d.get("alembic") or {}).get("copy") or "-")')
  [ "$SCRATCH_DB" != "-" ] || SCRATCH_DB=""
  case "$status" in
    ok) ok "$src backup $(printf '%s' "$report" | json_get file) restored: alembic $copy_rev" ;;
    schema_mismatch)
      if awk -v r="$copy_rev" '$2 == "deploy" && $6 == r' "$STATE_DIR/releases.log" 2>/dev/null | grep -q .; then
        warn "the newest $src backup predates the last migration (copy at $copy_rev) - the restore itself worked; run 'atp.sh backup --verify' for a current restore point"
        status="ok_older_schema"
      else bad "$src backup restored at an unexpected schema revision $copy_rev"; fi ;;
    *) bad "$src backup did not verify: ${status} $(printf '%s' "$report" | json_get notes) (stderr: $STATE_DIR/logs/verify-stderr.txt)" ;;
  esac
  printf '%s' "$report" | python3 -c 'import json, sys
try:
    c = json.load(sys.stdin).get("counts") or {}
except Exception:
    c = {}
if c:
    print("    rows (copy / live): " + ", ".join("%s %s/%s" % (t, v.get("copy"), v.get("source")) for t, v in c.items()))'
  local chain="not_run" verify_pw
  if [ -n "$SCRATCH_DB" ] && [ "$status" != "restore_failed" ] && [ "$status" != "none" ]; then
    # Z11: the chain check reads the copy logged in AS the role that restored it (owner of every object in it), never as
    # the superuser: a view or function the archive created runs with the reader's rights. The role gets a fresh password
    # for this check only (handed to the container through the environment, not argv) and is NOLOGIN again right after.
    verify_pw="$(openssl rand -hex 24)"
    VERIFY_ROLE_LOGIN=1   # before the call: if it half-succeeds (or the run stops there), on_exit still sets NOLOGIN
    if verify_role_login "$verify_pw"; then
      # shellcheck disable=SC2016  # expands inside the backend container (its own DATABASE_URL, never printed)
      if ATP_VERIFY_PW="$verify_pw" dc run --rm --no-deps -T -e ATP_VERIFY_DB="$SCRATCH_DB" -e ATP_VERIFY_ROLE="$VERIFY_ROLE" -e ATP_VERIFY_PW \
        backend sh -c 'u="${DATABASE_URL%/*}"; case "$u" in *@*) ;; *) exit 1 ;; esac
          DATABASE_URL="${u%%://*}://$ATP_VERIFY_ROLE@${u##*@}/$ATP_VERIFY_DB" PGPASSWORD="$ATP_VERIFY_PW" python -m app.audit.verify_chain' >/dev/null 2>&1; then
        chain="intact"; ok "audit hash chain intact in the restored copy (read as $VERIFY_ROLE)"
      else chain="broken"; bad "audit hash chain does NOT verify in the restored copy"; fi
      if verify_role_login ""; then VERIFY_ROLE_LOGIN=0; else warn "could not make $VERIFY_ROLE NOLOGIN again - retried on exit"; fi
    else
      chain="not_checked"; bad "audit chain: could not let $VERIFY_ROLE log in to read the restored copy - not checked as the superuser"
      if verify_role_login ""; then VERIFY_ROLE_LOGIN=0; fi
    fi
  else info "audit chain: not run (nothing restored)"; fi
  if [ -n "$SCRATCH_DB" ]; then
    dc exec -T postgres psql -X -q -v ON_ERROR_STOP=1 -U "$(pg_user)" -d postgres -c "DROP DATABASE IF EXISTS \"$SCRATCH_DB\"" >/dev/null 2>&1 || true
    SCRATCH_DB=""
  fi
  printf '%s source=%s status=%s chain=%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$src" "$status" "$chain" "$report" >> "$STATE_DIR/restore-checks.log"
  printf '%s %s %s chain=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$src" "$status" "$chain" > "$STATE_DIR/last_restore_check"

  step "files archive"
  local newest
  if [ "$src" = "R2" ]; then
    # shellcheck disable=SC2016  # $OFFSITE_REMOTE expands inside the rclone container
    newest="$(dc run --rm --no-deps -T --entrypoint sh offsite -c \
      'f="$(rclone lsf "$OFFSITE_REMOTE/files" --files-only --include "atp_files_*.enc" | sort | tail -n 1)"; [ -n "$f" ] && for x in "$f" "$f.sha256" "$f.hmac"; do rclone copyto "$OFFSITE_REMOTE/files/$x" "/restore/$x" 2>/dev/null; done; echo "$f"' \
      | tr -d '\r' | tail -n 1 || true)"
    if [ -n "$newest" ]; then verify_files_archive "$(restore_dir)/$newest"; else warn "no files archive in R2 yet (atp.sh backup puts one there)"; fi
  else
    newest="$(find "$FILES_BACKUP_DIR" -maxdepth 1 -name 'atp_files_*.tar.gz.enc' 2>/dev/null | sort | tail -n 1 || true)"
    if [ -n "$newest" ]; then verify_files_archive "$newest"; else warn "no files archive yet (atp.sh backup makes one)"; fi
  fi
}

cmd_schedule() {
  local quiet="${1:-}"
  if [ "$quiet" != "quiet" ]; then lock 0; check_tools; fi
  step "schedule (crontab of $(id -un); host time zone, IST after bootstrap)"
  command -v crontab >/dev/null 2>&1 || { warn "crontab not installed - scheduled files backup and weekly restore check are off"; return 0; }
  local self="$ATP_DIR/deploy/atp.sh" logs="$STATE_DIR/logs" block current
  block="# BEGIN atp.sh managed block - replaced by 'deploy/atp.sh schedule'; edit outside it
PATH=/usr/local/bin:/usr/bin:/bin
17 2 * * * $self backup --scheduled >> $logs/backup.log 2>&1
43 3 * * 0 $self restore-check --scheduled >> $logs/restore-check.log 2>&1
# END atp.sh managed block"
  current="$(crontab -l 2>/dev/null || true)"
  local others; others="$(printf '%s\n' "$current" | awk '/^# BEGIN atp.sh managed block/{skip=1} !skip{print} /^# END atp.sh managed block/{skip=0}' | sed '/^$/d')"
  local wanted; wanted="$(printf '%s\n%s\n' "$others" "$block" | sed '/^$/d')"
  if [ "$(printf '%s\n' "$current" | sed '/^$/d')" = "$wanted" ]; then ok "crontab already current (daily 02:17 backup + R2 copy, Sunday 03:43 restore check)"; return 0; fi
  if [ "$DRY_RUN" = "1" ]; then info "would install the crontab block (daily 02:17 backup + R2 copy, Sunday 03:43 restore check)"; return 0; fi
  state_init; mkdir -p "$logs"
  printf '%s\n' "$wanted" | crontab -
  ok "crontab installed: daily 02:17 backup + R2 copy, Sunday 03:43 restore check (logs in $logs)"
}

cmd_releases() {
  [ -f "$STATE_DIR/releases.log" ] || { echo "no release recorded yet"; return 0; }
  printf '%-20s %-8s %-12s %-12s %-54s %s\n' "time (UTC)" kind release previous "pre-migration backup" "schema"
  awk '{printf "%-20s %-8s %-12s %-12s %-54s %s -> %s\n", $1, $2, substr($3,1,12), substr($4,1,12), $5, $6, $7}' "$STATE_DIR/releases.log"
  printf 'running: %s\n' "$(current_sha | cut -c1-12)"
}

cmd_install() {
  [ "$(id -u)" != "0" ] || die "run as the deploy user (atp): first the bootstrap block as root, then this as atp" 2
  cmd_bootstrap
  WARNS=0
  cmd_deploy "${1:-origin/main}"
  WARNS=0; FAILS=0
  step "first backup + restore check"
  cmd_backup --verify
  if [ "$DRY_RUN" != "1" ]; then
    OWNER_ACTION=""
    step "Phase 0 flag posture (read-only)"
    report_flags
    owner_action_exit
  fi
  printf '\ninstall: done. Updates: %s/deploy/atp.sh deploy   Health: %s/deploy/atp.sh health\n' "$ATP_DIR" "$ATP_DIR"
}

usage() { sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

main() {
  local args=() a
  for a in "$@"; do
    if [ "$a" = "--dry-run" ]; then DRY_RUN=1; else args+=("$a"); fi
  done
  set -- "${args[@]+"${args[@]}"}"
  local cmd="${1:-help}"; [ $# -gt 0 ] && shift
  env_load
  [ "$DRY_RUN" = "1" ] && printf 'dry-run: printing the steps; nothing on this host changes\n'
  case "$cmd" in
    install)        cmd_install "$@" ;;
    bootstrap)      cmd_bootstrap ;;
    deploy)         cmd_deploy "$@" ;;
    health|status)  cmd_health ;;
    rollback)       cmd_rollback "$@" ;;
    backup)         cmd_backup "$@" ;;
    restore-check)  cmd_restore_check "$@" ;;
    schedule)       cmd_schedule ;;
    releases)       cmd_releases ;;
    help|-h|--help) usage ;;
    *) usage; exit 2 ;;
  esac
}

# Everything above is functions: bash has read the whole file before main runs, so a `git checkout` that rewrites
# this file mid-deploy cannot change what is executing.
main "$@"; exit $?
