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
#   /opt/atp/deploy/atp.sh backup [--verify]       database dump + files archive, encrypted + sealed, copied to R2, old
#                                                  R2 copies pruned (7 daily / 4 weekly / 6 monthly, never inside the lock)
#   /opt/atp/deploy/atp.sh restore-check           newest backup from R2 -> scratch database -> checks -> dropped
#   /opt/atp/deploy/atp.sh backup-status           one JSON line: last good backup, local and off-site, and its age
#                                                  (exit 1 past BACKUP_MAX_AGE_HOURS - the hook an alert reads)
#   /opt/atp/deploy/atp.sh ops-check               A2: the server checks (backups, disk, certificate + the app's own:
#                                                  errors, worker, data feed, broker logins, DB size) -> the operator's
#                                                  Telegram / e-mail on a change (scheduled every 5 minutes)
#   /opt/atp/deploy/atp.sh restore-offsite         a NEW server: the newest backup from R2 into its EMPTY database
#                                                  (runbook section 6b: restore on a clean machine), then `deploy`
#   /opt/atp/deploy/atp.sh wal-cleanup             empties the WAL archive left from before archiving was turned off
#   /opt/atp/deploy/atp.sh base-backup             point-in-time recovery only (PG_ARCHIVE_MODE=on): encrypted base
#                                                  backup, prunes the WAL archive (scheduled weekly when on)
#   /opt/atp/deploy/atp.sh bootstrap               host checks (as root: runs deploy/hostinger/bootstrap.sh)
#   /opt/atp/deploy/atp.sh loadtest [--force-offhours] [--users N] [--ramp S] [--seconds S] [--think S] [--check]
#                                                  500 synthetic PAPER sessions against this host in NSE market hours, in
#                                                  a throwaway load-test organisation removed afterwards; memory sampled;
#                                                  report in /opt/atp/reports/ (docs/deploy/LOADTEST_REAL_HOST.md)
#   /opt/atp/deploy/atp.sh loadtest-cleanup [STAMP | --all]
#                                                  removes a load-test run's organisation / containers by hand
#   /opt/atp/deploy/atp.sh reset-password EMAIL    sets a user's new password (asked twice, never shown or logged) and
#                                                  signs out their sessions - until e-mail (SMTP) is configured
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
# Exit codes: 0 ok | 1 failed | 2 usage | 3 refused: market session (migration / restart) - for `loadtest`: outside
#             NSE market hours | 4 loadtest: cleanup not verified (outranks every other outcome) | 5 loadtest: stopped
#             by its watchdog, production degraded | 6 loadtest: refused, not enough free memory | 10 owner action
#             needed (`loadtest`: not PAPER-only Phase 0) | 20 key changed | 130 loadtest: Ctrl-C, cleanup verified
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
LT_STAMP=""         # the load test in progress (`loadtest`): on any exit its containers go and its organisation is cleaned up
LT_DIR=""           # that run's state directory (.deploy/loadtest/<stamp>)
LT_PIDS=()          # its background samplers
LT_REPORTED=0       # 1 once its report is written
LT_HUP=0            # 1 when the terminal went away (output then goes to the run's log only)
DC_WRAP=()          # a command dc runs docker compose under (setsid during the load test's cleanup: a second Ctrl-C cannot stop it)
OWNER_ACTION=""     # what health found that only the owner can do (exit 10)
BACKUP_AGE_STRICT=0 # 1 under `health`: a backup older than BACKUP_MAX_AGE_HOURS FAILs (inside a deploy it WARNs)
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
  local lt_ran=0
  if [ -n "$LT_STAMP" ]; then lt_ran=1; loadtest_finish "$rc" || true; fi
  if [ "$VERIFY_ROLE_LOGIN" = "1" ]; then verify_role_login "" || true; fi
  if [ -n "$SCRATCH_DB" ]; then
    dc exec -T postgres psql -X -q -v ON_ERROR_STOP=1 -U "$(pg_user)" -d postgres -c "DROP DATABASE IF EXISTS \"$SCRATCH_DB\"" >/dev/null 2>&1 || true
  fi
  if [ "$rc" != "0" ] && [ -n "$RESTORE_CHECKOUT" ] && [ "$DRY_RUN" != "1" ]; then
    if git -C "$ATP_DIR" checkout -q --detach "$RESTORE_CHECKOUT" 2>/dev/null; then
      printf 'checkout put back to %s (the release that still runs)\n' "${RESTORE_CHECKOUT:0:12}" >&2
    fi
  fi
  # M2 of the PR #284 review: an unverified load-test cleanup outranks every other outcome (Ctrl-C 130, a failure 1).
  if [ "$lt_ran" = "1" ] && [ "$LT_CLEAN" != "1" ]; then exit 4; fi
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
# $1 = the .env key (default SECRETS_ENCRYPTION_KEY; A1 also keeps one of BACKUP_ENCRYPTION_PASSPHRASE).
key_fingerprint() {
  local salt; salt="$(cat "$STATE_DIR/key_salt" 2>/dev/null || true)"
  [ -n "$salt" ] || { printf 'no-salt'; return 0; }
  env_get "${1:-SECRETS_ENCRYPTION_KEY}" | python3 -c 'import hashlib, sys
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
    ${DC_WRAP[@]+"${DC_WRAP[@]}"} docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.hostinger.yml \
    -f deploy/compose.release.yml -p "$PROJECT" "$@")
}
rdc() { printf '    + docker compose %s\n' "$*"; [ "$DRY_RUN" = "1" ] && return 0; dc "$@"; }
# Caddy reads deploy/$CADDYFILE through a single-file bind mount: a checkout replaces the file (a new inode) and the
# running container keeps the old one, and `up -d` does not recreate a service whose compose config is unchanged. So
# the file Caddy actually runs is compared with the checkout's - not two releases' files, because the release that
# brings a change is deployed by the previous release's atp.sh, and a run can stop between `up -d` and this step.
# Certificates stay in the caddy_data volume; the edge is down for about a second.
caddy_config_current() {
  local want; want="$ATP_DIR/deploy/$(env_get CADDYFILE | grep . || echo Caddyfile)"
  [ -f "$want" ] || return 0                       # nothing to compare with (compose itself would refuse to start)
  dc exec -T caddy cat /etc/caddy/Caddyfile 2>/dev/null | cmp -s - "$want"
}
refresh_edge() {
  [ -n "$(dc ps -q caddy 2>/dev/null || true)" ] || return 0             # not running: `up -d` creates it from the file
  if [ "$DRY_RUN" = "1" ]; then info "would compare caddy's running Caddyfile with deploy/$(env_get CADDYFILE | grep . || echo Caddyfile) and recreate caddy if they differ"; return 0; fi
  if caddy_config_current; then return 0; fi
  info "caddy runs an older Caddyfile than deploy/$(env_get CADDYFILE | grep . || echo Caddyfile) - recreating it so it reads this one"
  rdc up -d --no-deps --no-build --force-recreate caddy
}
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
# The same with the name given in full (a base backup's seal covers "base/<stamp>/<file>", scripts/backup/common.sh).
mac_stream_named() {
  ATP_MAC_PASS="$(env_get BACKUP_ENCRYPTION_PASSPHRASE)" python3 -c 'import hashlib, hmac, os, sys
key = hashlib.pbkdf2_hmac("sha256", os.environ["ATP_MAC_PASS"].encode(), b"atpmacv1", 200000, 32)
mac = hmac.new(key, sys.argv[1].encode() + b"\n", digestmod="sha256")
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

# --- A1: backup settings, ages and the escrow check (numbers live in .env, not here) ---------------------------
env_int() { local v; v="$(env_get "$1")"; case "$v" in ''|*[!0-9]*) printf '%s' "$2" ;; *) printf '%s' "$v" ;; esac; }
backup_max_age_h() { env_int BACKUP_MAX_AGE_HOURS 26; }
# PG_ARCHIVE_MODE=on in .env: continuous WAL archiving (point-in-time recovery) and its weekly encrypted base backup.
pitr_on() { case "$(env_get PG_ARCHIVE_MODE | tr '[:upper:]' '[:lower:]')" in on|always) return 0 ;; esac; return 1; }
age_hours() {   # whole hours since an ISO time; nothing when it cannot be read
  local t; [ -n "$1" ] || return 0
  t="$(date -u -d "$1" +%s 2>/dev/null)" || return 0
  printf '%s' $(( ($(date -u +%s) - t) / 3600 ))
}
# The last good backup: local = the dump's LAST_BACKUP_OK marker (scripts/backup/backup.sh); off-site = the last
# `atp.sh backup` whose R2 copy succeeded (.deploy/last_offsite_ok: "<utc time> <newest dump>"; kept in a file, not
# read from the offsite container's log, which a restart empties).
BK_LOCAL_LAST="" BK_LOCAL_AGE="" BK_OFF_LAST="" BK_OFF_NAME="" BK_OFF_AGE=""
backup_ages() {
  BK_LOCAL_LAST="$(dc exec -T backup cat /backups/LAST_BACKUP_OK 2>/dev/null | tr -d '\r' | tail -n 1 || true)"
  BK_LOCAL_AGE="$(age_hours "$BK_LOCAL_LAST")"
  BK_OFF_LAST="" BK_OFF_NAME="" BK_OFF_AGE=""
  if [ -s "$STATE_DIR/last_offsite_ok" ]; then
    read -r BK_OFF_LAST BK_OFF_NAME < "$STATE_DIR/last_offsite_ok" || true
    BK_OFF_AGE="$(age_hours "$BK_OFF_LAST")"
  fi
}
# A backup past its age limit: FAIL under `health` (what an alert reads), WARN inside a deploy or rollback.
backup_late() { if [ "$BACKUP_AGE_STRICT" = "1" ]; then bad "$*"; else warn "$*"; fi; }

# Review R2 (key escrow): the backup passphrase's salted fingerprint, kept like the secrets key's. Recorded on the
# first deploy / backup; when the value changes, the change is said once and recorded (older backups still need the
# OLD value, so it stays in escrow until they have left R2). Never prints a value.
backup_key_record() {
  [ "$DRY_RUN" = "1" ] && return 0
  [ -s "$STATE_DIR/key_salt" ] || return 0            # the first deploy makes the salt
  local f="$STATE_DIR/backup_key.fingerprint" fp
  fp="$(key_fingerprint BACKUP_ENCRYPTION_PASSPHRASE)"
  if [ -s "$f" ] && [ "$(cat "$f")" = "$fp" ]; then return 0; fi
  if [ -s "$f" ]; then
    warn "BACKUP_ENCRYPTION_PASSPHRASE is not the value recorded on $(date -r "$f" '+%Y-%m-%d') - backups taken before today need the OLD value: keep it in escrow until they have left R2 (runbook section 6a). The new value is recorded now."
    printf '%s passphrase changed\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$STATE_DIR/backup_key.changes"
  fi
  printf '%s\n' "$fp" > "$f"; chmod 600 "$f"
}
escrow_report() {
  local f="$STATE_DIR/secrets_key.fingerprint" b="$STATE_DIR/backup_key.fingerprint"
  if [ -s "$f" ]; then info "escrow: SECRETS_ENCRYPTION_KEY unchanged since $(date -r "$f" '+%Y-%m-%d') - your two escrow copies must hold this value (runbook section 6a)"; fi
  if [ -s "$b" ]; then
    if [ "$(cat "$b")" = "$(key_fingerprint BACKUP_ENCRYPTION_PASSPHRASE)" ]; then
      info "escrow: BACKUP_ENCRYPTION_PASSPHRASE unchanged since $(date -r "$b" '+%Y-%m-%d') - every backup since then opens with it"
    else warn "BACKUP_ENCRYPTION_PASSPHRASE changed since $(date -r "$b" '+%Y-%m-%d') - older backups need the old value: keep it in escrow (runbook section 6a); the next backup records the new one"; fi
  fi
}

# The WAL archive (review R1). Off (the default): nothing should be in it - what is left from before is listed with
# its cleanup command. On: the newest base backup must be at most 8 days old, or the archive is not pruned.
wal_report() {
  local mode n mb last age
  mode="$(psql_q 'show archive_mode' postgres | tr -d '\r' || true)"
  # shellcheck disable=SC2016  # expands inside the postgres container
  read -r n mb < <(dc exec -T postgres sh -c 'printf "%s %s\n" "$(find /wal_archive -type f 2>/dev/null | wc -l)" "$(du -sm /wal_archive 2>/dev/null | cut -f1)"' 2>/dev/null | tr -d '\r' || true) || true
  n="${n:-?}"; mb="${mb:-?}"
  case "$mode" in
    off)
      if pitr_on; then warn "WAL archiving: PG_ARCHIVE_MODE=on in .env, but the running database has it off - the next deploy applies it"; fi
      case "$n" in
        0) ok "WAL archiving off (recovery point: the daily encrypted dump; runbook section 6c turns point-in-time recovery on)" ;;
        '?') info "WAL archiving off; the archive could not be read" ;;
        *) warn "WAL archiving is off, but $n file(s) (${mb} MB) from before are still in the archive - remove them with: $ATP_DIR/deploy/atp.sh wal-cleanup" ;;
      esac ;;
    on|always)
      if ! pitr_on; then warn "WAL archiving is on in the running database but not in .env (PG_ARCHIVE_MODE) - the next deploy turns it off"; fi
      last="$(dc exec -T backup cat /backups/LAST_BASE_BACKUP_OK 2>/dev/null | tr -d '\r' | tail -n 1 || true)"
      age="$(age_hours "$last")"
      if [ -z "$age" ]; then warn "WAL archiving on ($n files, ${mb} MB) but no base backup yet - run: $ATP_DIR/deploy/atp.sh base-backup"
      elif [ "$age" -le 192 ]; then ok "WAL archiving on: last base backup $last (${age} h ago), archive $n files, ${mb} MB"
      else backup_late "WAL archiving on: the last base backup is ${age} h old (over 8 days) - the archive is not pruned ($n files, ${mb} MB)"; fi ;;
    *) info "WAL archiving: state unknown (database not reachable)" ;;
  esac
}

# The cron block `deploy` and `schedule` install (A1): every night an encrypted backup, its R2 copy and a restore of it
# into a scratch database (--verify); a second restore check every Sunday; the base backup only with PITR on.
schedule_block() {
  local self="$ATP_DIR/deploy/atp.sh" logs="$STATE_DIR/logs"
  printf '%s\n' "# BEGIN atp.sh managed block - replaced by 'deploy/atp.sh schedule'; edit outside it" \
    "PATH=/usr/local/bin:/usr/bin:/bin" \
    "17 2 * * * $self backup --scheduled --verify >> $logs/backup.log 2>&1" \
    "43 3 * * 0 $self restore-check --scheduled >> $logs/restore-check.log 2>&1" \
    "2-59/5 * * * * $self ops-check >> $logs/ops-check.log 2>&1"
  if pitr_on; then printf '%s\n' "29 4 * * 6 $self base-backup --scheduled >> $logs/base-backup.log 2>&1"; fi
  printf '%s\n' "# END atp.sh managed block"
}
schedule_summary() {
  printf 'daily 02:17 backup + R2 copy + restore check, Sunday 03:43 restore check'
  if pitr_on; then printf ', Saturday 04:29 base backup'; fi
  printf ', server checks every 5 minutes'
}
# current | missing | outdated | no-crontab
schedule_state() {
  command -v crontab >/dev/null 2>&1 || { printf 'no-crontab'; return 0; }
  local current block
  current="$(crontab -l 2>/dev/null || true)"
  block="$(printf '%s\n' "$current" | awk '/^# BEGIN atp.sh managed block/{on=1} on{print} /^# END atp.sh managed block/{on=0}')"
  if [ -z "$block" ]; then printf 'missing'
  elif [ "$block" = "$(schedule_block)" ]; then printf 'current'
  else printf 'outdated'; fi
}
schedule_report() {
  case "$(schedule_state)" in
    current) ok "schedule installed: $(schedule_summary)" ;;
    missing) warn "no backup schedule in the crontab of $(id -un) - run: $ATP_DIR/deploy/atp.sh schedule" ;;
    outdated) warn "the backup schedule in the crontab is older than this release - run: $ATP_DIR/deploy/atp.sh schedule" ;;
    *) warn "crontab not installed - the nightly backup and the weekly restore check are not scheduled" ;;
  esac
}

# Health's backup lines (A1). The A2 alert reads the same through `atp.sh backup-status`.
backup_report() {
  local max; max="$(backup_max_age_h)"
  backup_ages
  if [ -z "$BK_LOCAL_LAST" ]; then warn "no database backup yet (LAST_BACKUP_OK missing) - run: atp.sh backup"
  elif [ -z "$BK_LOCAL_AGE" ]; then warn "last database backup: unreadable time '$BK_LOCAL_LAST'"
  elif [ "$BK_LOCAL_AGE" -le "$max" ]; then ok "last database backup $BK_LOCAL_LAST (${BK_LOCAL_AGE} h ago)"
  else backup_late "last database backup $BK_LOCAL_LAST is ${BK_LOCAL_AGE} h old (limit ${max} h, BACKUP_MAX_AGE_HOURS)"; fi
  if [ "$(offsite_state)" = "configured" ]; then
    if [ -z "$BK_OFF_LAST" ]; then warn "off-site (R2): no copy recorded yet - run: atp.sh backup --verify"
    elif [ -z "$BK_OFF_AGE" ]; then warn "off-site (R2): unreadable time '$BK_OFF_LAST' in $STATE_DIR/last_offsite_ok"
    elif [ "$BK_OFF_AGE" -le "$max" ]; then ok "last off-site copy (R2) $BK_OFF_LAST (${BK_OFF_AGE} h ago): ${BK_OFF_NAME:-?}"
    else backup_late "last off-site copy (R2) $BK_OFF_LAST is ${BK_OFF_AGE} h old (limit ${max} h, BACKUP_MAX_AGE_HOURS)"; fi
    local sync; sync="$(dc logs --no-color --tail 300 offsite 2>/dev/null | grep -E 'synced|FAILED' | tail -n 1 || true)"
    case "$sync" in *FAILED*) bad "off-site copy failing: $sync" ;; *synced*) ok "off-site: $sync" ;; *) info "off-site: the hourly copy has logged nothing since its last start" ;; esac
  else warn "off-site: $(offsite_state | cut -d: -f2-)"; fi
  if [ -f "$STATE_DIR/last_restore_check" ]; then info "last restore check: $(cat "$STATE_DIR/last_restore_check")"; fi
  wal_report
  schedule_report
  escrow_report
}

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
      backup_key_record
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
  n=0
  for k in GOOGLE_OAUTH_CLIENT_ID GOOGLE_OAUTH_CLIENT_SECRET; do if env_set "$k"; then n=$((n + 1)); fi; done
  case "$n" in 0) info "Google sign-in (GS-1): not set (optional; no Google button)";; 2) ok "Google sign-in (GS-1): both keys set (the button shows)";;
    *) warn "Google sign-in (GS-1): only one of GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET set - the button stays hidden";; esac
  # B8: browser notifications need a stable VAPID key; without one the app refuses new browser subscriptions (runbook section 4).
  if env_set VAPID_PRIVATE_KEY; then
    ok "browser notifications (Web Push): VAPID_PRIVATE_KEY set - subscriptions survive restarts"
    case "$(env_get VAPID_SUBJECT)" in ""|*@example.com) warn "browser notifications: VAPID_SUBJECT is not your own contact (mailto:you@yourdomain)";; esac
  else
    warn "browser notifications (Web Push): VAPID_PRIVATE_KEY is not set - the app refuses new browser subscriptions until it is (runbook section 4, 'Browser and phone notifications')"
  fi
  if env_set FCM_SERVICE_ACCOUNT_JSON_BASE64 || env_set FCM_SERVICE_ACCOUNT_FILE; then ok "phone notifications (Android app, FCM): key set - the mobile_push flag decides who gets them"
  else info "phone notifications (Android app, FCM): not set (optional; FCM_SERVICE_ACCOUNT_JSON_BASE64) - nothing is sent to phones"; fi
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
# One parse of /health/deep: status database migrations worker redis seconds_since_heartbeat cycle_seconds
deep_fields() {
  python3 -c 'import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    d = {}
c = d.get("checks") or {}
w = c.get("worker") or {}
print(d.get("status") or "unparseable", *[(c.get(k) or {}).get("status") or "-" for k in ("database", "migrations", "worker", "redis")],
      w.get("seconds_since_heartbeat", -1), w.get("cycle_seconds", -1))'
}

# Deep health acceptable? ok, or degraded only because the database is ahead after a checked code-only rollback.
deep_ok() {
  local body st db_s mig wk rd hb cyc
  body="$(curl -fsS -m 10 "$API/api/system/health/deep" 2>/dev/null)" || return 1
  read -r st db_s mig wk rd hb cyc < <(printf '%s' "$body" | deep_fields)
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
  local body st db_s mig wk rd hb cyc
  if body="$(curl -fsS -m 5 "$API/api/system/health" 2>/dev/null)" && [ "$(printf '%s' "$body" | json_get status)" = "ok" ]; then ok "/api/system/health ok"
  else bad "/api/system/health does not answer ok on $API"; fi
  if body="$(curl -sS -m 10 "$API/api/system/health/deep" 2>/dev/null)"; then
    read -r st db_s mig wk rd hb cyc < <(printf '%s' "$body" | deep_fields)
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
  if [ -n "$(dc ps -q caddy 2>/dev/null || true)" ] && ! caddy_config_current; then
    bad "caddy runs an older Caddyfile than deploy/$(env_get CADDYFILE | grep . || echo Caddyfile) - run: $ATP_DIR/deploy/atp.sh deploy"
  fi
  if [ "$(env_get CADDYFILE)" != "Caddyfile.ip" ]; then public_domain_check "$domain"; fi

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
  backup_report

  step "host"
  local ports
  ports="$(ss -tlnH 2>/dev/null | awk '{print $4}' | grep -vE '^(127\.|\[::1\]|\[::ffff:127\.)' | sed -E 's/.*:([0-9]+)$/\1/' | sort -un | tr '\n' ' ' || true)"
  local p extra=""
  for p in $ports; do case "$p" in 22|80|443) ;; *) extra="$extra $p" ;; esac; done
  if [ -n "$extra" ]; then bad "public TCP ports besides 22/80/443:$extra (Docker publishes past ufw)"; else ok "public TCP ports: ${ports:-none seen}"; fi
  local used; used="$(df -P "$ATP_DIR" | awk 'NR==2 {gsub("%","",$5); print $5}')"
  if [ "${used:-0}" -ge 95 ]; then bad "disk ${used}% used"; elif [ "${used:-0}" -ge 85 ]; then warn "disk ${used}% used"; else ok "disk ${used}% used"; fi
}

# With a real domain (CADDYFILE=Caddyfile). Through Caddy on this host (no DNS): the apex's security headers and the
# www site's redirect - both come from this release's Caddyfile, so they FAIL. Through public DNS with the certificate
# verified: the apex and www as the world reaches them - DNS, Let's Encrypt and the route back to this host's own
# address are outside a release, so they only WARN (a deploy or rollback is never failed by them). GET requests only.
public_domain_check() {
  local domain="$1" code hdrs h missing="" want got
  local here=(--resolve "$domain:443:127.0.0.1" --resolve "www.$domain:443:127.0.0.1")
  hdrs="$(curl -sk -m 10 -D - -o /dev/null "${here[@]}" "https://$domain/" 2>/dev/null | tr -d '\r' | tr '[:upper:]' '[:lower:]' || true)"
  if ! printf '%s\n' "$hdrs" | grep -q '^http/'; then
    bad "https://$domain/ through Caddy here gave no answer, so its security headers could not be checked"
  else
    for h in strict-transport-security x-content-type-options x-frame-options content-security-policy referrer-policy; do
      printf '%s\n' "$hdrs" | grep -q "^$h:" || missing="$missing $h"
    done
    if [ -z "$missing" ]; then ok "https://$domain/ sends HSTS, CSP, X-Frame-Options, X-Content-Type-Options and Referrer-Policy"
    else bad "https://$domain/ is missing:$missing (deploy/Caddyfile)"; fi
  fi
  want="301 https://$domain/atp-health-probe?x=1"
  got="$(curl -sk -o /dev/null -m 10 -w '%{http_code} %{redirect_url}' "${here[@]}" "https://www.$domain/atp-health-probe?x=1" 2>/dev/null || true)"
  case "$got" in
    "$want") ok "www.$domain -> permanent redirect to https://$domain (path and query kept)" ;;
    ""|000*) warn "www.$domain gave no answer through Caddy here - no certificate for it yet (its DNS record, or Let's Encrypt still issuing)" ;;
    *) bad "www.$domain/atp-health-probe?x=1 -> '$got', expected '$want' (deploy/Caddyfile)" ;;
  esac
  code="$(curl -sS -o /dev/null -m 10 -w '%{http_code}' "https://$domain/api/system/health" 2>/dev/null || true)"
  if [ "$code" = "200" ]; then ok "https://$domain/api/system/health -> 200 over public DNS, certificate trusted"
  else warn "https://$domain/api/system/health over public DNS -> ${code:-no answer} (DNS, the certificate, or no route back to this host's own address)"; fi
  got="$(curl -sS -o /dev/null -m 10 -w '%{http_code} %{redirect_url}' "https://www.$domain/atp-health-probe?x=1" 2>/dev/null || true)"
  if [ "$got" = "$want" ]; then ok "https://www.$domain over public DNS -> 301 to https://$domain"
  else warn "https://www.$domain over public DNS -> '${got:-no answer}', expected '$want' (www DNS record or its certificate)"; fi
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
  BACKUP_AGE_STRICT=1
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
  refresh_edge
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
  refresh_edge
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
  backup_key_record
  RELEASE="$(release_of "$(current_sha)")"
  local r2; r2="$(offsite_state)"

  if [ "$files_only" = "0" ]; then
    step "database dump (pg_dump, AES-256, SHA-256 + HMAC seal; keeps $(env_int BACKUP_KEEP_MIN 7) daily, $(env_int BACKUP_KEEP_WEEKLY 4) weekly, $(env_int BACKUP_KEEP_MONTHLY 6) monthly)"
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
      'rclone copy /files "$OFFSITE_REMOTE/files" --include "/atp_files_*.tar.gz.enc{,.sha256,.hmac}" -q' || die "copy of the files archive to R2 failed" 1
    ok "encrypted, sealed database dumps (daily, weekly, monthly) and the files archive copied to $(env_get OFFSITE_REMOTE)"
    if [ "$DRY_RUN" != "1" ]; then
      local newest; newest="$(dc run --rm --no-deps -T --entrypoint sh backup -c 'readlink /backups/latest' 2>/dev/null | tr -d '\r' | tail -n 1 || true)"
      printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${newest:--}" > "$STATE_DIR/last_offsite_ok"
    fi
    step "off-site retention (scripts/backup/offsite_prune.sh: the same counts, never inside the $(env_int OFFSITE_LOCK_DAYS 14)-day lock)"
    if rdc run --rm --no-deps -T --entrypoint sh offsite /scripts/offsite_prune.sh; then ok "old R2 copies pruned"
    else warn "pruning old R2 copies failed (see above) - nothing was lost; is the bucket lock longer than OFFSITE_LOCK_DAYS?"; fi
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
  local logs="$STATE_DIR/logs" block current
  block="$(schedule_block)"
  current="$(crontab -l 2>/dev/null || true)"
  local others; others="$(printf '%s\n' "$current" | awk '/^# BEGIN atp.sh managed block/{skip=1} !skip{print} /^# END atp.sh managed block/{skip=0}' | sed '/^$/d')"
  local wanted; wanted="$(printf '%s\n%s\n' "$others" "$block" | sed '/^$/d')"
  if [ "$(printf '%s\n' "$current" | sed '/^$/d')" = "$wanted" ]; then ok "crontab already current ($(schedule_summary))"; return 0; fi
  if [ "$DRY_RUN" = "1" ]; then info "would install the crontab block ($(schedule_summary))"; return 0; fi
  state_init; mkdir -p "$logs"
  printf '%s\n' "$wanted" | crontab -
  ok "crontab installed: $(schedule_summary) (logs in $logs)"
}

# A1 / A2 hook: the last good backup as one JSON line on stdout (nothing else there), read-only. status: ok | warn (no
# backup yet, or R2 not configured - an owner blocker, not an outage) | fail (older than BACKUP_MAX_AGE_HOURS, the
# marker unreadable after backups have run, or the newest restore check failed). Exit 1 on fail, so a monitor can
# alert on the exit code alone.
cmd_backup_status() {
  RELEASE="$(release_of "$(current_sha)")"
  local max status="ok" problems="" r2 configured=false rc_line="" rc_time="" rc_status=""
  max="$(backup_max_age_h)"
  backup_ages
  bs_note() { problems="${problems:+$problems; }$2"; case "$1:$status" in fail:*) status="fail" ;; warn:ok) status="warn" ;; esac; }
  # A host that has run `atp.sh backup` (install does) and now shows no marker: the backups volume or its container is
  # gone - that is an outage, not a fresh install.
  if [ -z "$BK_LOCAL_AGE" ] && [ -s "$STATE_DIR/last_backup" ]; then bs_note fail "local backup marker unreadable (backup container or volume down?)"
  elif [ -z "$BK_LOCAL_AGE" ]; then bs_note warn "no local backup recorded"
  elif [ "$BK_LOCAL_AGE" -gt "$max" ]; then bs_note fail "local backup ${BK_LOCAL_AGE} h old"; fi
  r2="$(offsite_state)"
  if [ "$r2" = "configured" ]; then
    configured=true
    if [ -z "$BK_OFF_AGE" ]; then bs_note warn "no off-site copy recorded"
    elif [ "$BK_OFF_AGE" -gt "$max" ]; then bs_note fail "off-site copy ${BK_OFF_AGE} h old"; fi
  else bs_note warn "off-site not configured"; fi
  if [ -s "$STATE_DIR/last_restore_check" ]; then
    rc_line="$(cat "$STATE_DIR/last_restore_check")"
    read -r rc_time _ rc_status _ <<<"$rc_line" || true
    case "$rc_status" in ok|ok_older_schema) ;; *) bs_note fail "last restore check: ${rc_status:-unknown}" ;; esac
  fi
  python3 -c 'import json, sys
a = sys.argv[1:]
def num(v):
    return int(v) if v.isdigit() else None
print(json.dumps({"status": a[0], "problems": a[1], "limit_hours": int(a[2]),
                  "local": {"last": a[3] or None, "age_hours": num(a[4])},
                  "offsite": {"configured": a[5] == "true", "last": a[6] or None, "age_hours": num(a[7]), "newest": a[8] or None},
                  "restore_check": {"last": a[9] or None, "status": a[10] or None}}, sort_keys=True))' \
    "$status" "$problems" "$max" "$BK_LOCAL_LAST" "$BK_LOCAL_AGE" "$configured" "$BK_OFF_LAST" "$BK_OFF_AGE" "$BK_OFF_NAME" "$rc_time" "$rc_status"
  [ "$status" != "fail" ]
}

# A2: the operator's server checks. The host facts the app cannot see - the backup status (the A1 hook above), the disk
# and the HTTPS certificate's days left - go as one JSON object on stdin to backend/scripts/ops_check.py, which adds the
# app's own checks, sends what changed to the operator's Telegram chat / e-mail (deduplicated) and prints one line.
# Read-only on the host; never holds the deploy lock (a run that finds it held - a deploy or a backup - is skipped, so a
# deploy's planned restart is not reported as an outage).
cmd_ops_check() {
  [ "$(id -u)" != "0" ] || die "run as the deploy user (atp), not root" 2
  state_init
  if [ "$DRY_RUN" = "1" ]; then info "would collect the backup status, disk and certificate and run the server checks in the backend"; return 0; fi
  if [ -e "$STATE_DIR/lock" ] && ! flock -n "$STATE_DIR/lock" true; then
    info "ops-check skipped: another atp.sh run (a deploy or a backup) is in progress"; return 0
  fi
  local bs used domain cert_end="" end_s="" days="" note=""
  bs="$( (cmd_backup_status) 2>/dev/null | tail -n 1)" || true
  used="$(df -P "$ATP_DIR" 2>/dev/null | awk 'NR==2 {gsub("%","",$5); print $5}')" || true
  domain="$(env_get DOMAIN)"
  if [ "$(env_get CADDYFILE)" = "Caddyfile.ip" ]; then note="ip_only"
  elif [ -n "$domain" ]; then
    cert_end="$(timeout 15 openssl s_client -connect 127.0.0.1:443 -servername "$domain" </dev/null 2>/dev/null \
      | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)" || true
    if [ -n "$cert_end" ] && end_s="$(date -u -d "$cert_end" +%s 2>/dev/null)"; then days=$(( (end_s - $(date -u +%s)) / 86400 )); fi
  fi
  python3 -c 'import json, sys
a = sys.argv[1:]
def num(v):
    return int(v) if v.lstrip("-").isdigit() else None
try:
    backup = json.loads(a[0]) if a[0] else None
except ValueError:
    backup = None
print(json.dumps({"backup": backup, "disk_used_percent": num(a[1]), "cert_days_left": num(a[2]), "cert_note": a[3] or None}, sort_keys=True))' \
    "$bs" "$used" "$days" "$note" | dc exec -T backend python scripts/ops_check.py --host-facts -
}

# Review R1: the WAL archive is useless once archiving is off (no base backup was ever taken on Hostinger, so it could
# not be replayed) and only fills the disk. Refuses while the running database archives.
cmd_wal_cleanup() {
  [ "$(id -u)" != "0" ] || die "run as the deploy user (atp), not root" 2
  lock 0
  check_tools
  check_env
  RELEASE="$(release_of "$(current_sha)")"
  step "WAL archive cleanup"
  local mode; mode="$(psql_q 'show archive_mode' postgres | tr -d '\r' || true)"
  [ -n "$mode" ] || die "cannot read archive_mode from the running database - is it up? (atp.sh health)" 1
  [ "$mode" = "off" ] || die "WAL archiving is '$mode' in the running database: the archive is in use (base-backup prunes it) - nothing removed" 2
  local count='find /wal_archive -mindepth 1 -maxdepth 1 -type f | wc -l'
  local before; before="$(dc run --rm --no-deps -T --entrypoint sh backup -c "$count" 2>/dev/null | tr -d '\r ' | tail -n 1 || true)"
  if [ "${before:-0}" = "0" ]; then ok "the WAL archive is already empty"; return 0; fi
  info "$before file(s) in the WAL archive, written before archiving was turned off"
  rdc run --rm --no-deps -T --entrypoint sh backup -c 'find /wal_archive -mindepth 1 -maxdepth 1 -type f -delete' || die "could not empty the WAL archive" 1
  [ "$DRY_RUN" = "1" ] && return 0
  local after; after="$(dc run --rm --no-deps -T --entrypoint sh backup -c "$count" 2>/dev/null | tr -d '\r ' | tail -n 1 || true)"
  [ "${after:-?}" = "0" ] || die "the WAL archive still holds ${after:-?} file(s)" 1
  ok "WAL archive emptied ($before file(s) removed); archiving stays off (PG_ARCHIVE_MODE)"
}

# Point-in-time recovery only (PG_ARCHIVE_MODE=on, runbook section 6c): pg_basebackup inside the postgres container
# (local socket - replication is allowed there) streamed into the backup container, which encrypts + seals it and
# prunes the WAL archive before the oldest kept base backup (scripts/backup/base_backup.sh --stdin). Copied to R2.
cmd_base_backup() {
  local scheduled=0
  while [ $# -gt 0 ]; do
    case "$1" in --scheduled) scheduled=1 ;; *) die "unknown base-backup option $1" 2 ;; esac
    shift
  done
  [ "$(id -u)" != "0" ] || die "run as the deploy user (atp), not root" 2
  lock "$([ "$scheduled" = "1" ] && echo 1800 || echo 0)"
  check_tools
  check_env
  RELEASE="$(release_of "$(current_sha)")"
  pitr_on || die "point-in-time recovery is off (PG_ARCHIVE_MODE is not on in .env) - the daily dump is the recovery point; no base backup is needed (runbook section 6c)" 2
  step "base backup (encrypted + sealed; prunes the WAL archive before the oldest of the newest $(env_int BASE_BACKUP_KEEP 4))"
  if [ "$DRY_RUN" = "1" ]; then
    info "+ docker compose exec -T postgres pg_basebackup -U $(pg_user) -D - -Ft -z -X fetch --checkpoint=fast | docker compose run --rm --no-deps -T --entrypoint sh backup /scripts/base_backup.sh --stdin"
    return 0
  fi
  dc exec -T postgres pg_basebackup -U "$(pg_user)" -D - -Ft -z -X fetch --checkpoint=fast \
    | dc run --rm --no-deps -T --entrypoint sh backup /scripts/base_backup.sh --stdin || die "base backup failed" 1
  local stamp want got
  stamp="$(dc run --rm --no-deps -T --entrypoint sh backup -c 'readlink /backups/base/latest' 2>/dev/null | tr -d '\r' | tail -n 1 || true)"
  [ -n "$stamp" ] || die "base backup not found after base_backup.sh" 1
  want="$(dc run --rm --no-deps -T --entrypoint cat backup "/backups/base/$stamp/base.tar.gz.enc.hmac" 2>/dev/null | tr -d '\r\n' || true)"
  got="$(dc run --rm --no-deps -T --entrypoint cat backup "/backups/base/$stamp/base.tar.gz.enc" | mac_stream_named "base/$stamp/base.tar.gz.enc")" || got=""
  if [ -n "$want" ] && [ "$want" = "$got" ]; then ok "base backup $stamp encrypted and sealed"; else die "base backup $stamp has no valid seal" 1; fi
  if [ "$(offsite_state)" = "configured" ]; then
    if rdc run --rm --no-deps -T offsite --once; then ok "copied to R2 (encrypted; the WAL archive itself stays on this server)"; else die "copy of the base backup to R2 failed" 1; fi
  else warn "R2 not configured - the base backup is on this server only"; fi
}

# Review R2, the clean-machine restore (runbook section 6b): a NEW server with only R2 and the escrowed .env values.
# Fills the EMPTY database with the newest encrypted, sealed dump from R2; `deploy` then starts the release on it.
# Never replaces data: it refuses a database that holds any table (on a running server use rollback --restore-db, or
# docs/OPERATIONS.md 1.2).
cmd_restore_offsite() {
  [ "$(id -u)" != "0" ] || die "run as the deploy user (atp), not root" 2
  lock 0
  check_tools
  check_repo
  check_env
  [ "$(offsite_state)" = "configured" ] || die "R2 is not configured in .env ($(offsite_state | cut -d: -f2-)) - put the OFFSITE_* values from your escrow in .env first (runbook section 6b)" 10
  local sha; sha="$(git -C "$ATP_DIR" rev-parse HEAD)"
  RELEASE="$(release_of "$(current_sha | grep . || echo "$sha")")"
  step "images atp-*:$RELEASE"
  if [ "$DRY_RUN" != "1" ] && images_exist "$RELEASE"; then ok "images for $RELEASE already built"; else rdc build --pull; fi
  step "database (must be empty)"
  rdc up -d --wait postgres
  local db; db="$(pg_db)"
  if [ "$DRY_RUN" != "1" ]; then
    local tables; tables="$(psql_q "select count(*) from pg_tables where schemaname not in ('pg_catalog', 'information_schema')" | tr -d '\r' || true)"
    [ -n "$tables" ] || die "cannot read the database $db - nothing was changed" 1
    [ "$tables" = "0" ] || die "the database $db already holds $tables table(s): restore-offsite only fills an EMPTY database on a new server. Nothing was changed. (A running server: atp.sh rollback --restore-db, or docs/OPERATIONS.md 1.2.)" 2
    ok "database $db is empty"
  fi
  step "newest database backup from R2 -> $(restore_dir)"
  local file seal
  if [ "$DRY_RUN" = "1" ]; then
    rdc run --rm --no-deps -T -e OFFSITE_REQUIRE_ENCRYPTED=1 --entrypoint sh offsite /scripts/offsite_fetch.sh /restore
    rdc run --rm --no-deps -T -e RESTORE_CONFIRM=yes --entrypoint sh backup /scripts/restore.sh "/restore/<newest>"
    return 0
  fi
  file="$(dc run --rm --no-deps -T -e OFFSITE_REQUIRE_ENCRYPTED=1 --entrypoint sh offsite /scripts/offsite_fetch.sh /restore | tr -d '\r' | tail -n 1)" || die "could not fetch a backup from R2 (OFFSITE_* values, bucket name)" 1
  case "$file" in /restore/*.dump.enc) ;; *) die "R2 handed back '$file', not an encrypted dump - refused" 1 ;; esac
  seal="$(host_mac_state "$(restore_dir)/$(basename "$file")")"
  case "$seal" in
    ok) ok "fetched $(basename "$file"), seal (HMAC) verified - BACKUP_ENCRYPTION_PASSPHRASE is the right value" ;;
    missing) die "$(basename "$file") has no seal - refused" 1 ;;
    *) die "$(basename "$file") does not match its seal: either BACKUP_ENCRYPTION_PASSPHRASE in .env is not the value the backup was made with, or the file was changed - nothing was restored" 1 ;;
  esac
  local answer="${ATP_CONFIRM_DB:-}"
  if [ -z "$answer" ]; then
    printf '\nType the database name (%s) to fill it with %s: ' "$db" "$(basename "$file")"
    read -r answer </dev/tty || answer=""
  fi
  [ "$answer" = "$db" ] || die "not confirmed - nothing changed" 1
  step "restore into $db"
  rdc run --rm --no-deps -T -e RESTORE_CONFIRM=yes --entrypoint sh backup /scripts/restore.sh "$file" || die "the restore failed - the database may be partly filled: drop it (docker compose ... down -v) and run restore-offsite again" 1
  local rev; rev="$(db_revision)"
  ok "restored $(basename "$file"): schema $rev"
  state_init
  printf '%s source=R2 status=restored file=%s schema=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(basename "$file")" "$rev" >> "$STATE_DIR/restore-checks.log"
  step "files archive (deploy state + BACKUP_EXTRA_DIRS)"
  local newest
  # shellcheck disable=SC2016  # $OFFSITE_REMOTE expands inside the rclone container
  newest="$(dc run --rm --no-deps -T --entrypoint sh offsite -c \
    'f="$(rclone lsf "$OFFSITE_REMOTE/files" --files-only --include "atp_files_*.enc" | sort | tail -n 1)"; [ -n "$f" ] && for x in "$f" "$f.sha256" "$f.hmac"; do rclone copyto "$OFFSITE_REMOTE/files/$x" "/restore/$x" 2>/dev/null; done; echo "$f"' \
    | tr -d '\r' | tail -n 1 || true)"
  if [ -n "$newest" ]; then
    verify_files_archive "$(restore_dir)/$newest"
    info "it stays in $(restore_dir) - unpack BACKUP_EXTRA_DIRS from it by hand if you used them (runbook section 6b); .deploy/ is rebuilt by the next deploy"
  else warn "no files archive in R2"; fi
  if [ "$FAILS" -gt 0 ]; then printf '\nrestore-offsite: FAILED - %s failure(s)\n' "$FAILS"; exit 1; fi
  printf '\nrestore-offsite: done. Now start the release on it: %s/deploy/atp.sh deploy && %s/deploy/atp.sh health\n' "$ATP_DIR" "$ATP_DIR"
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

# --- load test on this host (docs/deploy/LOADTEST_REAL_HOST.md) ------------------------------------------------
# N synthetic sessions (default 500) in NSE market hours against the release that runs, through Caddy as users arrive,
# in a throwaway organisation `loadtest-<stamp>` (backend/scripts/loadtest_org.py): PAPER only, no broker credential,
# no flag, secret, role or other organisation touched. The load client runs here, one small container per client IP on
# a network of its own that reaches only Caddy (the API's login limit of 10 a minute per IP holds, as for real users),
# at a low CPU weight and capped per container; its own CPU and memory are sampled and reported apart. A watchdog stops
# the run when production degrades. Every exit - the end, a failure, an abort, Ctrl-C, a closed terminal - stops the
# containers and cleans the organisation up by the ids its seed reported (loadtest_finish).
LT_LOGINS_PER_IP_MINUTE=8     # under the API's 10 a minute per client IP (as the CI's sharded runs)
LT_CLIENT_MEMORY="128m"       # per client container, no swap
LT_CLIENT_RSS_MIB=48          # what one client container uses: ~31 MiB measured for 20 sessions with one TLS context, + samples
LT_APP_WEIGHT=1024            # docker's default --cpu-shares, what each app container has; the client shards share it
LT_MAX_WINDOW=840             # ramp + duration: access tokens live 15 minutes and the client does not refresh them
LT_MIN_AVAILABLE_MIB="${ATP_LOADTEST_MIN_AVAILABLE_MIB:-400}"   # watchdog floor: MemAvailable below this stops the run
LT_MAX_SWAP_GROWTH_MIB=512    # watchdog: swap used growing by more than this since the start stops the run
LT_HEARTBEAT_FLOOR_S=180      # the heartbeat limit below never goes under this
LT_HEARTBEAT_CAP_S=900        # ... nor over this: a worker silent for 15 minutes stops the run whatever its cycle
LT_MAX_HEARTBEAT_S="$LT_HEARTBEAT_FLOOR_S"   # watchdog: the trading worker's heartbeat older than 3 cycles stops the run
                              # (SLO-2); the preflight sets it from deep health's cycle_seconds (lt_heartbeat_limit)
LT_WATCH_SECONDS="${ATP_LOADTEST_WATCH_SECONDS:-30}"
LT_RESULT=1                   # the report's exit code
LT_CLEAN=0                    # 1 once the cleanup is verified
LT_ABORT=""                   # why the watchdog stopped the run
LT_SEED_NAME="" LT_SEED_PID=""   # the seeding container while it runs (stopped, rolling its transaction back, on an exit)
LT_CADDY_ID=""                # caddy, while it is attached to the run's network
LT_MARKET="" LT_POSTURE="" LT_NET="" LT_BASE="" LT_ALLOWED="" LT_EDGE=""
LT_EDGE_ARGS=() LT_HOST_ARGS=()
LT_FLAGS_BEFORE="" LT_OTHERS_BEFORE=""    # read-only digests taken before seeding, compared at the end (lt_compare)
LT_CPU_MODEL="unknown" LT_BCRYPT_MS="unknown"   # the preflight's host baseline, for the report (informational only)

# "YYYY-MM-DD HH:MM" in IST (TZ is Asia/Kolkata above). ATP_LOADTEST_NOW stands in for the clock in the tests; the app's
# own calendar, when it answers, decides whether NSE is open.
ist_now() { if [ -n "${ATP_LOADTEST_NOW:-}" ]; then printf '%s' "$ATP_LOADTEST_NOW"; else date '+%Y-%m-%d %H:%M'; fi; }
# Minutes left in the regular NSE cash session (Monday-Friday 09:15-15:30 IST) at $1, by the clock alone; 0 = closed.
nse_clock_minutes_left() {
  local dow hm mins
  dow="$(date -d "$1" +%u 2>/dev/null)" || { printf '0'; return 0; }
  hm="$(date -d "$1" +%H%M)"
  mins=$(( 10#${hm:0:2} * 60 + 10#${hm:2:2} ))
  if [ "$dow" -le 5 ] && [ "$mins" -ge 555 ] && [ "$mins" -lt 930 ]; then printf '%s' $(( 930 - mins )); else printf '0'; fi
}

# The run's meta.json (settings and safety checks for the report): lt_meta KEY VALUE [KEY VALUE ...]. Never a secret.
lt_meta() {
  if [ -z "$LT_DIR" ] || [ ! -d "$LT_DIR" ]; then return 0; fi
  python3 - "$LT_DIR/meta.json" "$@" <<'PY' || true
import json, sys
path, kv = sys.argv[1], sys.argv[2:]
try:
    with open(path) as fh:
        meta = json.load(fh)
except (OSError, ValueError):
    meta = {}
for key, value in zip(kv[::2], kv[1::2]):
    try:
        meta[key] = json.loads(value) if value[:1] in "[{" or value in ("true", "false") or value.replace(".", "", 1).isdigit() else value
    except ValueError:
        meta[key] = value
with open(path, "w") as fh:
    json.dump(meta, fh, indent=2)
PY
}

# loadtest_org.py in the release's backend image (the host's database, as the app connects to it).
lt_org() { dc run --rm --no-deps -T backend python scripts/loadtest_org.py "$@"; }
lt_shield() { if [ "${#DC_WRAP[@]}" -gt 0 ]; then "${DC_WRAP[@]}" "$@"; else "$@"; fi; }

# Refuses (exit 3) outside NSE market hours unless --force-offhours, and when fewer minutes are left before 15:30 IST
# than the run needs ($1). Sets LT_MARKET for the report.
loadtest_market_gate() {
  local need="$1" force="$2" now left js nse="unknown"
  now="$(ist_now)"
  left="$(nse_clock_minutes_left "$now")"
  js="$(session_state "$RELEASE")"
  if [ -n "$js" ]; then
    nse="$(printf '%s' "$js" | python3 -c 'import json, sys
print("open" if "NSE" in (json.load(sys.stdin).get("open_sessions") or []) else "closed")' 2>/dev/null || echo unknown)"
  fi
  local open=0 how
  case "$nse" in
    open) open=1; how="the app's calendar" ;;
    closed) how="the app's calendar (weekend, exchange holiday or outside 09:15-15:30 IST)" ;;
    *) how="the clock only - the app's calendar did not answer, so exchange holidays are not known"
       warn "NSE session from the clock only: the app's calendar did not answer (holidays unknown)"
       if [ "$left" -gt 0 ]; then open=1; fi ;;
  esac
  if [ "$open" = "1" ]; then
    LT_MARKET="open (NSE, $how, $now IST)"
    if [ "$left" = "0" ]; then
      warn "NSE is open by the app's calendar outside 09:15-15:30 IST (a special session): the time left is not known"
    elif [ "$left" -lt "$need" ]; then
      if [ "$force" = "1" ]; then warn "only $left minute(s) left before 15:30 IST and the run needs about $need - running anyway (--force-offhours); part of it will be after the close"
      else die "refused: $left minute(s) left before the NSE close (15:30 IST) and the run needs about $need. Start it before $(date -d "15:30 today - $need minutes" +%H:%M 2>/dev/null || echo 'that') IST on a trading day, or add --force-offhours to run anyway (the report marks it)." 3; fi
    fi
    ok "NSE market hours ($how): $now IST, $left minute(s) to the close"
    return 0
  fi
  if [ "$force" = "1" ]; then
    LT_MARKET="off-hours (--force-offhours; NSE closed by $how, $now IST)"
    warn "outside NSE market hours ($now IST, by $how) - running anyway (--force-offhours). The report says so: it is not the market-hours result."
    return 0
  fi
  die "refused: outside NSE market hours - $now IST, NSE closed by $how. The load test is meant for 09:15-15:30 IST on a trading day; run it then, or add --force-offhours to run it anyway (the report marks such a run)." 3
}

# Refuses (exit 10) unless the deployment is PAPER only: phase0 in .env and, as the running release resolves them,
# deployment phase phase0, live_trading off for everyone and every organisation, phase0_paper_only on. Read-only - this
# script never changes a flag.
loadtest_posture_gate() {
  local phase js live="" live_tenants="" po_known="" po_on="" dphase=""
  phase="$(deployment_phase)"
  [ "$phase" = "phase0" ] || die "refused: ATP_DEPLOYMENT_PHASE is '$phase' in .env, not phase0 - the load test runs only on a PAPER-only Phase 0 deployment" 10
  js="$(dc exec -T backend sh -c 'test -f scripts/flag_posture.py && python scripts/flag_posture.py' 2>/dev/null | tr -d '\r' | tail -n 1 || true)"
  [ -n "$js" ] || die "refused: the running API could not report its flag posture (scripts/flag_posture.py) - PAPER only cannot be confirmed" 10
  read -r live live_tenants po_known po_on dphase < <(printf '%s' "$js" | python3 -c 'import json, sys
d = json.load(sys.stdin)
lt, po = d.get("live_trading") or {}, d.get("phase0_paper_only") or {}
print(int(bool(lt.get("on", True))), int(lt.get("tenants") or 0), int(bool(po.get("known"))), int(bool(po.get("on"))),
      "".join(ch for ch in str(d.get("deployment_phase") or "-") if ch.isalnum() or ch in "_-")[:20] or "-")' 2>/dev/null) || true
  [ "$dphase" = "phase0" ] || die "refused: the running release reports deployment phase '${dphase:-unknown}', not phase0" 10
  if [ "$live" != "0" ] || [ "$live_tenants" != "0" ]; then
    die "refused: live_trading is on (everyone: ${live:-?}, organisations: ${live_tenants:-?}) - Phase 0 is PAPER only. Turn it off in Admin > Controls first; this script never changes a flag." 10
  fi
  if [ "$po_known" != "1" ] || [ "$po_on" != "1" ]; then
    die "refused: phase0_paper_only is not on - turn it on in Admin > Controls first; this script never changes a flag." 10
  fi
  LT_POSTURE="PAPER only: phase0, live_trading off for everyone and every organisation, phase0_paper_only on"
  ok "$LT_POSTURE"
}

# Where the clients reach Caddy (L5 of the PR #284 review): a network of the run's own, `--internal` (no route out), with
# Caddy attached to it for the run and nothing else - the clients cannot reach Postgres, Redis or backend:8000, only the
# public edge, each from its own IP there. Sets LT_NET, LT_CADDY_ID, LT_BASE, LT_ALLOWED, LT_EDGE_ARGS, LT_HOST_ARGS, LT_EDGE.
loadtest_edge() {
  local stamp="$1" cid ip domain caddyfile
  cid="$(dc ps -q caddy 2>/dev/null | head -n 1 || true)"
  [ -n "$cid" ] || die "caddy is not running" 1
  LT_NET="atp-loadtest-$stamp"
  docker network create --internal --label "atp.loadtest=$stamp" "$LT_NET" >/dev/null || die "could not create the load-test network $LT_NET" 1
  docker network connect "$LT_NET" "$cid" || die "could not attach caddy to the load-test network $LT_NET" 1
  LT_CADDY_ID="$cid"
  ip="$(docker inspect -f "{{(index .NetworkSettings.Networks \"$LT_NET\").IPAddress}}" "$cid" 2>/dev/null || true)"
  [ -n "$ip" ] || die "could not find caddy's address on $LT_NET" 1
  domain="$(env_get DOMAIN)"; caddyfile="$(env_get CADDYFILE)"
  LT_EDGE_ARGS=(); LT_HOST_ARGS=()
  if [ "${caddyfile:-Caddyfile}" = "Caddyfile.ip" ]; then
    # A bare IP (no SNI): Caddy answers with its internal certificate for $DOMAIN (default_sni); the client checks it
    # against Caddy's own root and sends the site name as Host.
    dc exec -T caddy cat /data/caddy/pki/authorities/local/root.crt > "$LT_DIR/caddy-root.crt" 2>/dev/null || true
    [ -s "$LT_DIR/caddy-root.crt" ] || die "could not read Caddy's internal root certificate" 1
    chmod 644 "$LT_DIR/caddy-root.crt"
    LT_BASE="https://$ip"; LT_ALLOWED="$ip"
    LT_EDGE_ARGS=(--cacert /lt/caddy-root.crt --no-verify-hostname --host-header "$domain")
    LT_EDGE="Caddy over HTTPS on the run's own internal network ($ip, site $domain, Caddy's internal certificate); Postgres, Redis and the API not reachable from it"
  else
    LT_BASE="https://$domain"; LT_ALLOWED="$domain"
    LT_HOST_ARGS=(--add-host "$domain:$ip")
    LT_EDGE="Caddy over HTTPS on the run's own internal network ($domain -> $ip, its public certificate verified); Postgres, Redis and the API not reachable from it"
  fi
}

# Detaches Caddy from the run's network and removes it (and every load-test network when $1 is empty).
lt_remove_networks() {
  local label="atp.loadtest${1:+=$1}" net caddy
  caddy="${LT_CADDY_ID:-}"
  if [ -z "$caddy" ]; then caddy="$(dc ps -q caddy 2>/dev/null | head -n 1 || true)"; fi
  while IFS= read -r net; do
    [ -n "$net" ] || continue
    if [ -n "$caddy" ]; then lt_shield docker network disconnect -f "$net" "$caddy" >/dev/null 2>&1 || true; fi
    lt_shield docker network rm "$net" >/dev/null 2>&1 || true
  done < <(docker network ls -q --filter "label=$label" 2>/dev/null || true)
  LT_CADDY_ID=""
}
# loadtest-cleanup: no load-test network (of STAMP, or any) left after lt_remove_networks? One left (Caddy still
# attached) is a cleanup not verified: says so and returns 1 (the caller exits 4).
lt_networks_gone() {
  local n
  n="$(docker network ls -q --filter "label=atp.loadtest${1:+=$1}" 2>/dev/null | wc -l | tr -d ' ' || true)"
  [ "${n:-0}" != "0" ] || return 0
  bad "the load-test network is still there: atp.sh loadtest-cleanup --all"
  printf '\nloadtest-cleanup: NOT verified - %s load-test network(s) still there (Caddy may be attached)\n' "$n"
  return 1
}

# Background samplers (LAB-6): every container's CPU and memory (docker stats), and the host's memory and swap.
lt_sample_stats() {
  local out="$1" every="$2" now
  while :; do
    now="$(date +%s)"
    docker stats --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}},{{.MemPerc}}' 2>/dev/null | sed "s/^/$now,/" >> "$out" || true
    sleep "$every"
  done
}
lt_sample_host() {
  local out="$1" every="$2"
  while :; do
    { awk -v t="$(date +%s)" '/^MemTotal:/ {a = $2} /^MemFree:/ {b = $2} /^MemAvailable:/ {c = $2} /^SwapTotal:/ {d = $2} /^SwapFree:/ {e = $2}
           END {printf "%s,%s,%s,%s,%s,%s", t, a, b, c, d, e}' /proc/meminfo
      awk '/^pswpin / {i = $2} /^pswpout / {o = $2} END {printf ",%d,%d\n", i, o}' /proc/vmstat; } >> "$out" 2>/dev/null || true
    sleep "$every"
  done
}

# The app containers' restart counts and OOM flags ("name restarts oom" per line).
lt_restarts() {
  local ids
  ids="$(dc ps -q 2>/dev/null | tr '\n' ' ' || true)"
  [ -n "${ids// /}" ] || return 0
  # shellcheck disable=SC2086  # one id per word
  docker inspect -f '{{.Name}} {{.RestartCount}} {{.State.OOMKilled}}' $ids 2>/dev/null || true
}

# The trading worker, from one /health/deep body: why it stops a load test (the watchdog) or keeps one from starting
# (the preflight); nothing = fine. The heartbeat limit is 3 worker cycles - 3 x the cycle_seconds deep health reports,
# as it counts a worker stale - never below LT_HEARTBEAT_FLOOR_S (absent or not a whole number: the floor) and never
# above LT_HEARTBEAT_CAP_S.
lt_heartbeat_limit() {
  local cyc="${1:-}"
  case "$cyc" in ""|*[!0-9]*|???????*) printf '%s' "$LT_HEARTBEAT_FLOOR_S"; return 0 ;; esac
  cyc=$(( 10#$cyc * 3 ))
  if [ "$cyc" -lt "$LT_HEARTBEAT_FLOOR_S" ]; then cyc="$LT_HEARTBEAT_FLOOR_S"; fi
  if [ "$cyc" -gt "$LT_HEARTBEAT_CAP_S" ]; then cyc="$LT_HEARTBEAT_CAP_S"; fi
  printf '%s' "$cyc"
}
lt_worker_problem() {
  local st db_s mig wk rd hb cyc
  read -r st db_s mig wk rd hb cyc < <(printf '%s' "$1" | deep_fields) || true
  if [ "${hb:--1}" -gt "$LT_MAX_HEARTBEAT_S" ] 2>/dev/null; then printf "the trading worker's heartbeat is %ss old (limit %ss)" "$hb" "$LT_MAX_HEARTBEAT_S"
  elif [ "${wk:-ok}" != "ok" ] && [ "${wk:-ok}" != "-" ]; then printf 'the trading worker reports %s' "$wk"; fi
  return 0
}

# The host baseline the report shows beside the login verdict (informational only - never a verdict, never an abort):
# the CPU model, and one bcrypt cost-12 check measured uncontended in the running API's container with its own bcrypt
# (backend/scripts/bcrypt_baseline.py: a random throwaway password, nothing stored, only the time printed), within 30 s.
# "unknown" when either cannot be read.
lt_cpu_model() {
  local m
  m="$(awk -F': *' '/^model name/ {print $2; exit}' /proc/cpuinfo 2>/dev/null | tr -cd '[:print:]' || true)"
  printf '%s' "${m:-unknown}"
}
lt_bcrypt_ms() {
  local -a DC_WRAP=(timeout 30)
  local ms="" script="$SCRIPT_DIR/../backend/scripts/bcrypt_baseline.py"
  if [ -f "$script" ]; then ms="$(dc exec -T backend python - < "$script" 2>/dev/null | tr -d '\r' | tail -n 1 || true)"; fi
  case "$ms" in ""|*[!0-9]*) ms="unknown" ;; esac
  printf '%s' "$ms"
}

# M1 of the PR #284 review: every LT_WATCH_SECONDS, is production still healthy? Deep health (2 misses in a row), the
# trading worker's heartbeat, MemAvailable, swap growth, app container restarts or OOM kills. The first that fails is
# written to $LT_DIR/abort.txt; the wait loop then stops the run (and the cleanup runs). Runs in the background.
lt_watchdog() {
  local misses=0 reason body avail swap0 swap
  { echo "- 0 false"; lt_restarts; } > "$LT_DIR/restarts-before.txt"
  swap0="$(awk '/^SwapTotal:/ {t = $2} /^SwapFree:/ {f = $2} END {print int((t - f) / 1024)}' /proc/meminfo 2>/dev/null || echo 0)"
  while :; do
    sleep "$LT_WATCH_SECONDS"
    reason=""
    if deep_ok; then misses=0; else misses=$((misses + 1)); fi
    if [ "$misses" -ge 2 ]; then reason="the API's deep health failed twice in a row ($API/api/system/health/deep)"; fi
    if [ -z "$reason" ] && body="$(curl -fsS -m 10 "$API/api/system/health/deep" 2>/dev/null)"; then
      reason="$(lt_worker_problem "$body")"
    fi
    if [ -z "$reason" ]; then
      avail="$(awk '/^MemAvailable:/ {print int($2 / 1024)}' /proc/meminfo 2>/dev/null || echo 999999)"
      swap="$(awk '/^SwapTotal:/ {t = $2} /^SwapFree:/ {f = $2} END {print int((t - f) / 1024)}' /proc/meminfo 2>/dev/null || echo 0)"
      if [ "$avail" -lt "$LT_MIN_AVAILABLE_MIB" ]; then reason="MemAvailable ${avail} MiB is under the floor of ${LT_MIN_AVAILABLE_MIB} MiB"
      elif [ $((swap - swap0)) -gt "$LT_MAX_SWAP_GROWTH_MIB" ]; then reason="swap use grew by $((swap - swap0)) MiB during the run (limit ${LT_MAX_SWAP_GROWTH_MIB} MiB)"; fi
    fi
    if [ -z "$reason" ]; then
      lt_restarts > "$LT_DIR/restarts-now.txt"
      reason="$(awk 'NR == FNR {n[$1] = $2; o[$1] = $3; next}
                     ($1 in n) && $2 + 0 > n[$1] + 0 {print substr($1, 2) " restarted (restart count " $2 ")"; exit}
                     $3 == "true" && o[$1] != "true" {print substr($1, 2) " was OOM-killed"; exit}' \
                "$LT_DIR/restarts-before.txt" "$LT_DIR/restarts-now.txt" 2>/dev/null || true)"
    fi
    if [ -n "$reason" ]; then printf '%s\n' "$reason" > "$LT_DIR/abort.txt"; return 0; fi
  done
}

# The API's /metrics (every process), read inside its container with its own token - never printed.
lt_scrape() {
  dc exec -T backend python -c 'import os, urllib.request
req = urllib.request.Request("http://127.0.0.1:8000/metrics", headers={"Authorization": "Bearer " + os.environ.get("METRICS_TOKEN", "")})
print(urllib.request.urlopen(req, timeout=30).read().decode())' 2>/dev/null
}
# Read-only digests that show the run changed nothing outside its organisation: the feature-flag row; every other
# organisation and its users (ids, names, status, plan, emails, roles, active).
lt_others_digest() {
  psql_q "select (select md5(coalesce(string_agg(concat_ws(':', id, name, status, plan), ',' order by id), '')) from tenants where name not like 'loadtest-%') || ':' || (select md5(coalesce(string_agg(concat_ws(':', u.id, u.tenant_id, u.email, u.role, u.is_active), ',' order by u.id), '')) from users u join tenants t on t.id = u.tenant_id where t.name not like 'loadtest-%')" || printf 'unknown'
}

# One progress line from the latest samples (printed by awk directly: see the wait loop for why there is no $(...)).
lt_progress() {
  printf '    %(%H:%M:%S)T  t%s  clients running %s/%s  ' -1 "$1" "$2" "$3"
  awk -F, 'END {if (NR > 1) printf "host available %d MiB, swap used %d MiB  ", $4 / 1024, ($5 - $6) / 1024}' "$LT_DIR/host-memory.csv" 2>/dev/null || true
  awk -F, -v p="$4" 'NR > 1 && index($2, p) == 1 {s[$1] += $3 + 0; last = $1} END {if (last) printf "client CPU %.0f%% of one vCPU", s[last]; print ""}' \
    "$LT_DIR/docker-stats.csv" 2>/dev/null || printf '\n'
}

# The run changed nothing outside its organisation: the flag row and the other organisations, before and after.
lt_compare() {
  [ -n "$LT_FLAGS_BEFORE" ] || return 0
  local flags others
  flags="$(flags_snapshot)"; others="$(lt_others_digest)"
  if [ "$LT_FLAGS_BEFORE" = "$flags" ] && [ "$flags" != "unknown" ]; then lt_meta flags_unchanged yes; else lt_meta flags_unchanged "NO - check Admin > Controls"; fi
  if [ "$LT_OTHERS_BEFORE" = "$others" ] && [ "$others" != "unknown" ]; then lt_meta orgs_unchanged yes
  else lt_meta orgs_unchanged "NOT CONFIRMED - the digest of the other organisations changed during the run (a real sign-up or login meanwhile changes it too)"; fi
  LT_FLAGS_BEFORE=""
}

# The samplers and the watchdog stop when the load does, so the cleanup's own containers are not in the memory figures.
lt_stop_samplers() {
  local pid
  for pid in "${LT_PIDS[@]+"${LT_PIDS[@]}"}"; do kill "$pid" 2>/dev/null || true; done
  for pid in "${LT_PIDS[@]+"${LT_PIDS[@]}"}"; do wait "$pid" 2>/dev/null || true; done
  LT_PIDS=()
}

lt_print_cleanup() {
  python3 -c 'import json, sys
raw = sys.stdin.read().strip()
try:
    d = json.loads(raw.splitlines()[-1]) if raw else {}
except ValueError:
    d = {}
if not d:
    print("    FAIL  no cleanup report"); sys.exit(0)
if d.get("refused"):
    print("    FAIL  refused: " + d["refused"]); sys.exit(0)
if d.get("note"):
    print("    " + d["note"])
t = d.get("tombstone") or {}
print("    deleted:   " + (", ".join("%s %s" % kv for kv in sorted((d.get("deleted") or {}).items())) or "nothing"))
print("    kept:      " + (", ".join("%s %s" % kv for kv in sorted((d.get("ledger_rows_kept") or {}).items())) or "none")
      + " (append-only ledgers: the audit chain is never edited)")
if t:
    print("    tombstone: %s #%s, status %s, %s users (%s active, %s with a usable password)" % (d.get("tenant"), t.get("tenant_id"),
          t.get("status"), t.get("users_kept"), t.get("active_users"), t.get("usable_passwords")))
print("    left over: " + (", ".join("%s %s" % kv for kv in sorted((d.get("remaining") or {}).items())) or "none"))
if d.get("failed"):
    print("    failed:    " + "; ".join("%s: %s" % kv for kv in d["failed"].items()))
if d.get("audit_chain"):
    print("    audit chain (from its latest anchor): " + d["audit_chain"])
print("    ok    cleanup verified" if d.get("clean") else "    FAIL  cleanup NOT verified - docs/deploy/LOADTEST_REAL_HOST.md, manual cleanup")'
}

# One organisation's cleanup through loadtest_org.py: by the ids in its org file when there is one, else by its seed
# record (--absent-ok: no record = the seed never committed). Prints the JSON line.
lt_clean_org() {
  local stamp="$1" dir="$STATE_DIR/loadtest/$1" tid="${2:-}" absent="${3:-}"
  if [ -s "$dir/org.json" ]; then
    lt_org cleanup --stamp "$stamp" --org-file - --verify-audit < "$dir/org.json" 2>>"$dir/cleanup-stderr.txt" | tr -d '\r' | tail -n 1
  elif [ -n "$tid" ]; then
    lt_org cleanup --stamp "$stamp" --tenant-id "$tid" --verify-audit < /dev/null 2>>"$dir/cleanup-stderr.txt" | tr -d '\r' | tail -n 1
  else
    lt_org cleanup --stamp "$stamp" ${absent:+--absent-ok} --verify-audit < /dev/null 2>>"$dir/cleanup-stderr.txt" | tr -d '\r' | tail -n 1
  fi
}

# Stops the run's containers, watchdog and samplers, detaches its network, cleans its organisation up, removes the
# accounts file, writes and prints the report. Runs once, on the normal path and from the EXIT trap alike; Ctrl-C is
# ignored meanwhile and docker runs in its own session, so a second Ctrl-C cannot cut the cleanup short.
loadtest_finish() {
  local rc="${1:-0}"
  [ -n "$LT_STAMP" ] || return 0
  trap '' INT TERM HUP
  if [ "$LT_HUP" = "1" ] && [ -n "$LT_DIR" ]; then exec >>"$LT_DIR/run.log" 2>&1; fi
  local stamp="$LT_STAMP" ids left out="" tries nets
  LT_STAMP=""
  if command -v setsid >/dev/null 2>&1; then DC_WRAP=(setsid -w); fi
  if [ "$rc" = "0" ]; then step "load test $stamp: cleanup"
  else step "load test $stamp: cleanup after an interrupted, aborted or failed run (exit $rc) - Ctrl-C is ignored until it is done"; fi
  lt_stop_samplers
  if [ -n "$LT_SEED_NAME" ]; then
    # L1: a seed still running is stopped - its one transaction rolls back - before anything is cleaned.
    lt_shield docker rm -f "$LT_SEED_NAME" >/dev/null 2>&1 || true
    if [ -n "$LT_SEED_PID" ]; then kill "$LT_SEED_PID" 2>/dev/null || true; wait "$LT_SEED_PID" 2>/dev/null || true; fi
    LT_SEED_NAME=""; LT_SEED_PID=""
    info "the seeding was stopped"
  fi
  ids="$(docker ps -aq --filter "label=atp.loadtest=$stamp" 2>/dev/null || true)"
  if [ -n "$ids" ]; then
    # shellcheck disable=SC2086  # one id per word
    lt_shield docker rm -f $ids >/dev/null 2>&1 || true
  fi
  left="$(docker ps -aq --filter "label=atp.loadtest=$stamp" 2>/dev/null | wc -l | tr -d ' ' || true)"
  if [ "${left:-0}" = "0" ]; then ok "load-client containers removed"; else bad "$left load-client container(s) left: docker rm -f \$(docker ps -aq --filter label=atp.loadtest=$stamp)"; fi
  lt_meta load_containers_left "${left:-unknown}"
  lt_remove_networks "$stamp"
  nets="$(docker network ls -q --filter "label=atp.loadtest=$stamp" 2>/dev/null | wc -l | tr -d ' ' || true)"
  if [ "${nets:-0}" = "0" ]; then ok "load-test network removed, Caddy detached from it"; else bad "the load-test network is still there: atp.sh loadtest-cleanup --all"; fi
  lt_meta network_left "${nets:-unknown}"
  lt_compare || true
  for tries in 1 2; do
    out="$(lt_clean_org "$stamp" "" absent)" || true
    case "$out" in *'"clean": true'*) LT_CLEAN=1; break ;; esac
    case "$out" in *'"refused"'*) break ;; esac           # a refusal does not change on a retry
    [ "$tries" = "2" ] || { info "cleanup not verified yet - trying once more"; sleep 3; }
  done
  # A network left over (Caddy still attached to it) is a cleanup not verified too, whatever the organisation's state.
  if [ "${nets:-0}" != "0" ] && [ "$LT_CLEAN" = "1" ]; then LT_CLEAN=0; bad "cleanup not verified: the load-test network is still there"; fi
  printf '%s\n' "${out:-}" > "$LT_DIR/cleanup.json"
  printf '%s\n' "${out:-}" | lt_print_cleanup
  rm -f "$LT_DIR/accounts.csv" "$LT_DIR/seed.out"
  if [ -e "$LT_DIR/accounts.csv" ]; then lt_meta accounts_file_removed "NO - remove $LT_DIR/accounts.csv"; else lt_meta accounts_file_removed yes; fi
  DC_WRAP=()
  if [ "$LT_REPORTED" != "1" ]; then
    LT_REPORTED=1
    local report_dir="${ATP_REPORTS_DIR:-$ATP_DIR/reports}"
    mkdir -p "$report_dir" 2>/dev/null || report_dir="$LT_DIR"
    step "summary"
    LT_RESULT=0
    python3 "$SCRIPT_DIR/hostinger/loadtest_summary.py" --dir "$LT_DIR" --out "$report_dir/loadtest-$stamp.md" || LT_RESULT=$?
    [ "$LT_CLEAN" = "1" ] || LT_RESULT=4              # whatever the summary read or said: an unverified cleanup is 4
    chmod 644 "$report_dir/loadtest-$stamp.md" 2>/dev/null || true
    ln -sfn "loadtest-$stamp.md" "$report_dir/loadtest-latest.md" 2>/dev/null || true
    printf '\nRaw samples and logs: %s\n' "$LT_DIR"
    if [ -s "$report_dir/loadtest-$stamp.md" ]; then
      printf '\n===== report %s (paste this back) =====\n\n' "$report_dir/loadtest-$stamp.md"
      cat "$report_dir/loadtest-$stamp.md"
      printf '===== end of report =====\n'
    fi
  fi
  if [ "$LT_CLEAN" = "1" ]; then printf '\ncleanup: verified\n'; else printf '\ncleanup: NOT VERIFIED (exit 4) - docs/deploy/LOADTEST_REAL_HOST.md, manual cleanup\n'; fi
}

cmd_loadtest() {
  local users=500 ramp=150 seconds=300 think=30 cpus=0.25 every=5 force=0 check=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --users) users="${2:-}"; shift 2 ;; --ramp) ramp="${2:-}"; shift 2 ;; --seconds) seconds="${2:-}"; shift 2 ;;
      --think) think="${2:-}"; shift 2 ;; --client-cpus) cpus="${2:-}"; shift 2 ;; --sample-seconds) every="${2:-}"; shift 2 ;;
      --force-offhours) force=1; shift ;; --check) check=1; shift ;;
      *) die "unknown loadtest option $1 (atp.sh help)" 2 ;;
    esac
  done
  local v
  for v in "$users" "$ramp" "$seconds" "$think" "$every" "$LT_MIN_AVAILABLE_MIB" "$LT_WATCH_SECONDS"; do
    case "$v" in ""|*[!0-9]*) die "--users, --ramp, --seconds, --think and --sample-seconds take whole numbers" 2 ;; esac
  done
  if [ "$users" -lt 1 ] || [ "$users" -gt 1000 ]; then die "--users must be 1..1000" 2; fi
  if [ "$ramp" -lt 10 ] || [ "$seconds" -lt 30 ]; then die "--ramp must be at least 10 s and --seconds at least 30 s" 2; fi
  [ $((ramp + seconds)) -le "$LT_MAX_WINDOW" ] || die "--ramp + --seconds must stay within $LT_MAX_WINDOW s: access tokens live 15 minutes and the client does not refresh them" 2
  [ "$every" -ge 2 ] || die "--sample-seconds must be at least 2" 2
  case "$cpus" in ""|*[!0-9.]*|*.*.*) die "--client-cpus takes a number such as 0.25" 2 ;; esac
  [ "$(id -u)" != "0" ] || die "run as the deploy user (atp), not root" 2
  [ "$DRY_RUN" = "1" ] && check=1

  lock 0
  step "preflight"
  check_tools
  check_env report
  local cur; cur="$(current_sha)"
  [ -n "$cur" ] || die "no release recorded - deploy first: $ATP_DIR/deploy/atp.sh deploy (outside market hours)" 10
  RELEASE="$(release_of "$cur")"
  info "release running: $RELEASE"
  if [ "$(git -C "$ATP_DIR" rev-parse HEAD 2>/dev/null)" != "$cur" ]; then warn "the checkout differs from the running release - the load test measures the running release ($RELEASE)"; fi
  docker image inspect "atp-backend:$RELEASE" >/dev/null 2>&1 || die "image atp-backend:$RELEASE not found - deploy first: $ATP_DIR/deploy/atp.sh deploy" 10
  local shards per window need weight
  shards=$(( (users * 60 + ramp * LT_LOGINS_PER_IP_MINUTE - 1) / (ramp * LT_LOGINS_PER_IP_MINUTE) ))
  [ "$shards" -ge 1 ] || shards=1
  per=$(( (users + shards - 1) / shards ))
  weight=$(( LT_APP_WEIGHT / shards )); [ "$weight" -ge 2 ] || weight=2
  window=$(( 20 + shards + ramp + seconds ))
  need=$(( (window + 240 + 59) / 60 ))              # + seeding, the client's start and the cleanup
  info "plan: $users users in $shards client containers (one client IP each, at most $per users and $LT_LOGINS_PER_IP_MINUTE logins a minute per IP), login ramp ${ramp}s, then ${seconds}s, think ${think}s - about $need minutes"

  step "NSE market hours"
  loadtest_market_gate "$need" "$force"
  step "PAPER-only Phase 0"
  loadtest_posture_gate
  step "the running stack"
  local svc running
  running="$(dc ps --status running --services 2>/dev/null || true)"
  for svc in postgres redis backend worker caddy; do
    printf '%s\n' "$running" | grep -qx "$svc" || die "$svc is not running - check with: $ATP_DIR/deploy/atp.sh health" 1
  done
  deep_ok || die "the API's deep health is not ok - check with: $ATP_DIR/deploy/atp.sh health" 1
  # The trading worker as the watchdog judges it: a state it stops on (never_seen, stale, ...) is refused here, before
  # anything is created, rather than aborting the run one watchdog round after seeding.
  local deep wk hb cyc problem
  deep="$(curl -fsS -m 10 "$API/api/system/health/deep" 2>/dev/null)" || die "the API's deep health is not ok - check with: $ATP_DIR/deploy/atp.sh health" 1
  read -r _ _ _ wk _ hb cyc < <(printf '%s' "$deep" | deep_fields) || true
  LT_MAX_HEARTBEAT_S="$(lt_heartbeat_limit "${cyc:-}")"
  problem="$(lt_worker_problem "$deep")"
  [ -n "$problem" ] || [ "${wk:-}" = "ok" ] || problem="the trading worker reports ${wk:-nothing}"
  [ -z "$problem" ] || die "refused: $problem - the watchdog would stop the run. Nothing was created. Check with: $ATP_DIR/deploy/atp.sh health" 1
  ok "postgres, redis, backend, worker, caddy running; deep health ok; trading worker ok (heartbeat ${hb:-?}s ago, limit ${LT_MAX_HEARTBEAT_S}s)"
  local avail_mib need_mib
  avail_mib="$(awk '/^MemAvailable:/ {print int($2 / 1024)}' /proc/meminfo 2>/dev/null || echo 0)"
  # L4: what the clients use (about LT_CLIENT_RSS_MIB each), above the watchdog's floor, with 256 MiB to spare.
  need_mib=$(( shards * LT_CLIENT_RSS_MIB + LT_MIN_AVAILABLE_MIB + 256 ))
  if [ "${avail_mib:-0}" -lt "$need_mib" ]; then
    die "refused: only ${avail_mib:-0} MiB available; the load client needs about $(( shards * LT_CLIENT_RSS_MIB )) MiB and the run stops below ${LT_MIN_AVAILABLE_MIB} MiB - $need_mib MiB needed. Nothing was created." 6
  fi
  ok "memory available: ${avail_mib} MiB (the client needs about $(( shards * LT_CLIENT_RSS_MIB )) MiB; the run stops below ${LT_MIN_AVAILABLE_MIB} MiB)"
  local leftover
  leftover="$(lt_org list 2>/dev/null | tr -d '\r' | tail -n 1 | python3 -c 'import json, sys
try:
    rows = json.load(sys.stdin)
except ValueError:
    rows = []
print(" ".join(r["stamp"] for r in rows if r.get("active_users")))' 2>/dev/null || true)"
  if [ -n "$leftover" ]; then warn "an earlier run's load-test organisation still has active users: $leftover - remove it with: $ATP_DIR/deploy/atp.sh loadtest-cleanup <stamp>"; fi
  # Informational only, never a verdict or an abort: the login p95 follows the core's bcrypt speed.
  LT_CPU_MODEL="$(lt_cpu_model)"; LT_BCRYPT_MS="$(lt_bcrypt_ms)"
  info "host: $LT_CPU_MODEL, $(nproc) vCPU; bcrypt-12 check: $LT_BCRYPT_MS ms (CI reference: ~220 ms passes LD-9 at 500 logins over a 150 s ramp, ~500 ms fails)"

  if [ "$check" = "1" ]; then
    printf '\nloadtest --check: ready. Nothing was created or changed. Run without --check to start (about %s minutes).\n' "$need"
    return 0
  fi

  local stamp; stamp="$(date +%Y%m%d-%H%M%S)"
  LT_DIR="$STATE_DIR/loadtest/$stamp"
  mkdir -p "$LT_DIR"; chmod 700 "$LT_DIR"
  trap 'exit 130' INT
  trap 'exit 143' TERM
  trap 'LT_HUP=1; exit 129' HUP
  LT_STAMP="$stamp"                                 # from here on, every exit cleans up
  lt_meta stamp "$stamp" started_ist "$(date '+%Y-%m-%d %H:%M:%S')" release "$RELEASE" users "$users" ramp "$ramp" seconds "$seconds" \
    think "$think" shards "$shards" client_cpus_per_shard "$cpus" client_cpu_shares_per_shard "$weight" vcpus "$(nproc)" sample_seconds "$every" market "$LT_MARKET" \
    posture "$LT_POSTURE" client_prefix "atp-loadtest-$stamp-" cpu_model "$LT_CPU_MODEL" bcrypt12_ms "$LT_BCRYPT_MS" \
    host "$(hostname) ($(nproc) vCPU, $(awk '/^MemTotal:/ {printf "%.1f GB", $2 / 1048576}' /proc/meminfo))" \
    chart_data "chart: the users' own broker session (none: 409); chart_lake: the shared market lake's daily bars (database only)" \
    watchdog "every ${LT_WATCH_SECONDS}s: deep health (2 misses), worker heartbeat <= ${LT_MAX_HEARTBEAT_S}s, MemAvailable >= ${LT_MIN_AVAILABLE_MIB} MiB, swap growth <= ${LT_MAX_SWAP_GROWTH_MIB} MiB, no app container restart or OOM kill"
  LT_FLAGS_BEFORE="$(flags_snapshot)"; LT_OTHERS_BEFORE="$(lt_others_digest)"

  step "load-test organisation loadtest-$stamp: $users synthetic PAPER users, no broker credentials"
  # L1: in the background and waited for, so Ctrl-C returns at once; the exit stops the named container (its one
  # transaction rolls back) before the cleanup.
  LT_SEED_NAME="atp-loadtest-$stamp-seed"
  dc run --rm --no-deps -T --name "$LT_SEED_NAME" backend python scripts/loadtest_org.py seed --stamp "$stamp" --count "$users" \
    < /dev/null > "$LT_DIR/seed.out" 2> "$LT_DIR/seed-stderr.txt" &
  LT_SEED_PID="$!"
  if ! wait "$LT_SEED_PID"; then
    LT_SEED_PID=""
    die "seeding failed: $(tail -n 2 "$LT_DIR/seed-stderr.txt" | tr '\n' ' ')" 1
  fi
  LT_SEED_NAME=""; LT_SEED_PID=""
  { printf '# email,password - synthetic load-test users (PAPER, no broker); removed at cleanup\n'
    tr -d '\r' < "$LT_DIR/seed.out" | sed -n 's/^account,//p'; } > "$LT_DIR/accounts.csv"
  tr -d '\r' < "$LT_DIR/seed.out" | sed -n 's/^org,//p' | tail -n 1 > "$LT_DIR/org.json"
  rm -f "$LT_DIR/seed.out"
  local seeded creds tid
  seeded="$(grep -vc '^#' "$LT_DIR/accounts.csv" || true)"
  [ "$seeded" = "$users" ] || die "seeded $seeded users, expected $users" 1
  tid="$(python3 -c 'import json, sys; d = json.load(open(sys.argv[1])); assert d["stamp"] == sys.argv[2]; print(int(d["tenant_id"]))' "$LT_DIR/org.json" "$stamp" 2>/dev/null || true)"
  case "$tid" in ""|*[!0-9]*) die "seed did not report the organisation's id" 1 ;; esac
  creds="$(psql_q "select count(*) from broker_credentials where tenant_id = $tid" || echo unknown)"
  [ "$creds" = "0" ] || die "the load-test organisation has broker credentials ($creds) - stopping" 1
  lt_meta broker_credentials "none (0 rows)" tenant_id "$tid"
  ok "$seeded users seeded in organisation #$tid (accounts in $LT_DIR/accounts.csv, mode 600, removed at cleanup; ids in $LT_DIR/org.json)"

  step "samplers: docker stats and /proc/meminfo every ${every}s; watchdog every ${LT_WATCH_SECONDS}s"
  echo "epoch,container,cpu,mem_usage,mem_pct" > "$LT_DIR/docker-stats.csv"
  echo "epoch,mem_total_kb,mem_free_kb,mem_available_kb,swap_total_kb,swap_free_kb,pswpin,pswpout" > "$LT_DIR/host-memory.csv"
  lt_sample_stats "$LT_DIR/docker-stats.csv" "$every" & LT_PIDS+=("$!")
  lt_sample_host "$LT_DIR/host-memory.csv" "$every" & LT_PIDS+=("$!")
  lt_watchdog & LT_PIDS+=("$!")
  lt_scrape > "$LT_DIR/metrics-before.prom" || true

  step "load client: $shards containers on the run's own network"
  loadtest_edge "$stamp"
  lt_meta edge "$LT_EDGE"
  info "$LT_EDGE"
  local start_at i offset n name uid gid
  uid="$(id -u)"; gid="$(id -g)"
  start_at=$(( $(date +%s) + 20 + shards ))
  for i in $(seq 0 $((shards - 1))); do
    offset=$((i * per)); n=$(( users - offset < per ? users - offset : per ))
    [ "$n" -gt 0 ] || break
    name="atp-loadtest-$stamp-$i"
    docker run -d --name "$name" --label "atp.loadtest=$stamp" --network "$LT_NET" ${LT_HOST_ARGS[@]+"${LT_HOST_ARGS[@]}"} \
      --cpus "$cpus" --cpu-shares "$weight" --memory "$LT_CLIENT_MEMORY" --memory-swap "$LT_CLIENT_MEMORY" --pids-limit 64 \
      --read-only --tmpfs /tmp --no-healthcheck --cap-drop ALL --security-opt no-new-privileges --user "$uid:$gid" \
      -e LOADTEST_ALLOWED_HOSTS="$LT_ALLOWED" -v "$LT_DIR:/lt" --entrypoint python "atp-backend:$RELEASE" \
      scripts/loadtest.py --base "$LT_BASE" ${LT_EDGE_ARGS[@]+"${LT_EDGE_ARGS[@]}"} --chart-lake --accounts /lt/accounts.csv \
      --account-offset "$offset" --users "$n" --seconds "$seconds" --ramp "$ramp" --think "$think" --seed "$((54 + i))" \
      --shard "$i" --shards "$shards" --start-at "$start_at" --timeout 30 --raw-out "/lt/shard-$i.json" >/dev/null \
      || die "could not start load-client container $name" 1
  done
  ok "$shards client containers started; the ramp starts at $(date -d "@$start_at" +%H:%M:%S) IST"

  step "running: ramp ${ramp}s + ${seconds}s (progress every 30 s; Ctrl-C stops and cleans up)"
  # No command substitution in this loop: bash re-parses one when it runs it, and a Ctrl-C landing mid-parse makes the
  # trap fail to parse (the run still cleans up, but exits 2). The signal lands in `sleep` or a docker call instead.
  local deadline last=0 t up rel _id
  deadline=$(( start_at + ramp + seconds + 180 ))
  while :; do
    if [ -s "$LT_DIR/abort.txt" ]; then
      IFS= read -r LT_ABORT < "$LT_DIR/abort.txt" || true
      warn "ABORTED: production degraded - $LT_ABORT. Stopping the load now."
      break
    fi
    docker ps -q --filter "label=atp.loadtest=$stamp" > "$LT_DIR/clients-up.txt" 2>/dev/null || true
    up=0
    while IFS= read -r _id; do up=$((up + 1)); done < "$LT_DIR/clients-up.txt"
    [ "$up" -gt 0 ] || break
    printf -v t '%(%s)T' -1
    if [ "$t" -ge "$deadline" ]; then warn "the client is still running ${seconds}s + 3 minutes after the ramp - stopping it"; break; fi
    if [ $((t - last)) -ge 30 ]; then
      last="$t"
      rel="+$((t - start_at))s"
      if [ "$t" -lt "$start_at" ]; then rel="-$((start_at - t))s (before the ramp)"; fi
      lt_progress "$rel" "$up" "$shards" "atp-loadtest-$stamp-"
    fi
    sleep 5
  done
  lt_stop_samplers
  if [ -n "$LT_ABORT" ]; then
    lt_meta aborted "$LT_ABORT"
    # shellcheck disable=SC2046  # one id per word
    docker rm -f $(docker ps -aq --filter "label=atp.loadtest=$stamp" 2>/dev/null || true) >/dev/null 2>&1 || true
  fi
  local failed=0
  for i in $(seq 0 $((shards - 1))); do
    docker logs "atp-loadtest-$stamp-$i" > "$LT_DIR/shard-$i.log" 2>&1 || true
    [ -s "$LT_DIR/shard-$i.json" ] || failed=$((failed + 1))           # its own exit code is its own SLO verdict; the merge decides
  done
  [ "$failed" = "0" ] || [ -n "$LT_ABORT" ] || warn "$failed client container(s) failed - see $LT_DIR/shard-*.log"
  lt_scrape > "$LT_DIR/metrics-after.prom" || true
  local files=() f
  for f in "$LT_DIR"/shard-*.json; do [ -e "$f" ] && files+=("/lt/$(basename "$f")"); done
  if [ "${#files[@]}" -gt 0 ]; then
    docker run --rm --network none --read-only --no-healthcheck --cap-drop ALL --user "$uid:$gid" -v "$LT_DIR:/lt" --entrypoint python \
      "atp-backend:$RELEASE" scripts/loadtest.py --merge "${files[@]}" --json-out /lt/result.json > "$LT_DIR/merge.log" 2>&1 || true
  elif [ -z "$LT_ABORT" ]; then
    warn "no client wrote its samples - see $LT_DIR/shard-*.log"
  fi

  loadtest_finish 0
  exit "$LT_RESULT"
}

cmd_loadtest_cleanup() {
  local target="${1:-}"
  [ "$(id -u)" != "0" ] || die "run as the deploy user (atp), not root" 2
  [ -n "$target" ] || die "usage: atp.sh loadtest-cleanup STAMP | --all | --list" 2
  case "$target" in --all|--list) ;; *) [[ "$target" =~ ^[0-9]{8}-[0-9]{6}$ ]] || die "STAMP is YYYYMMDD-HHMMSS (atp.sh loadtest-cleanup --list shows them)" 2 ;; esac
  lock 0
  check_tools
  check_env report
  RELEASE="$(release_of "$(current_sha)")"
  [ -n "$RELEASE" ] || die "no release recorded - nothing deployed here" 10
  step "load-test organisations on this host (found by their seed audit record, never by name)"
  local js rows
  js="$(lt_org list | tr -d '\r' | tail -n 1)" || die "could not list them (the backend image of $RELEASE)" 1
  printf '%s' "$js" | python3 -c 'import json, sys
rows = json.load(sys.stdin)
for r in rows:
    print("    %s  organisation #%s %r  %s  users %s (%s active)%s" % (r["stamp"], r["tenant_id"], r.get("name"), r["status"], r["users"],
          r["active_users"], "" if r["ours"] else "  - NOT provably a load-test organisation, never touched: " + r.get("reason", "")))
if not rows:
    print("    none")' || die "could not read the list" 1
  # "stamp tenant_id" for every organisation still provably a load-test one
  rows="$(printf '%s' "$js" | python3 -c 'import json, sys
print("\n".join("%s %d" % (r["stamp"], r["tenant_id"]) for r in json.load(sys.stdin) if r["ours"]))')"
  local containers; containers="$(docker ps -aq --filter label=atp.loadtest 2>/dev/null | wc -l | tr -d ' ' || true)"
  info "load-client containers: ${containers:-0}"
  [ "$target" != "--list" ] || return 0
  if [ "$target" != "--all" ]; then
    rows="$(printf '%s\n' "$rows" | awk -v s="$target" '$1 == s')"
    [ -n "$rows" ] || die "no provable load-test organisation for $target (see the list above) - nothing changed" 1
  else
    if [ "${containers:-0}" != "0" ]; then
      # shellcheck disable=SC2046  # one id per word
      docker rm -f $(docker ps -aq --filter label=atp.loadtest) >/dev/null 2>&1 || true
    fi
    lt_remove_networks ""
  fi
  local scope="" nets_ok=1
  [ "$target" = "--all" ] || scope="$target"
  if [ -z "$rows" ]; then
    lt_networks_gone "$scope" || exit 4
    printf '\nnothing to clean up\n'; return 0
  fi
  local stamp tid out ids failed=0
  while read -r stamp tid; do
    [ -n "$stamp" ] || continue
    step "cleanup loadtest-$stamp (organisation #$tid)"
    mkdir -p "$STATE_DIR/loadtest/$stamp"
    ids="$(docker ps -aq --filter "label=atp.loadtest=$stamp" 2>/dev/null || true)"
    if [ -n "$ids" ]; then
      # shellcheck disable=SC2086  # one id per word
      docker rm -f $ids >/dev/null 2>&1 || true
    fi
    lt_remove_networks "$stamp"
    out="$(lt_clean_org "$stamp" "$tid")" || true
    printf '%s\n' "$out" | lt_print_cleanup
    case "$out" in *'"clean": true'*) ;; *) failed=$((failed + 1)) ;; esac
    rm -f "$STATE_DIR/loadtest/$stamp/accounts.csv"
  done <<< "$rows"
  lt_networks_gone "$scope" || nets_ok=0
  [ "$failed" = "0" ] || printf '\nloadtest-cleanup: %s organisation(s) NOT verified clean\n' "$failed"
  [ "$failed" = "0" ] && [ "$nets_ok" = "1" ] || exit 4
  printf '\nloadtest-cleanup: done\n'
}

usage() { sed -n '2,/--dry-run (anywhere/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

# Handover 8d: the owner sets a user's password on the server (backend/scripts/reset_password.py). The password is
# read from the terminal without echo, twice, and reaches the script on stdin only: never an argument (the process
# list), never printed, never in a log. `set -x` is never on in this script.
cmd_reset_password() {
  [ "$(id -u)" != "0" ] || die "run as the deploy user (atp), not root" 2
  local email="${1:-}" pw pw2 rc=0
  if [ -z "$email" ] || [ $# -ne 1 ]; then die "usage: atp.sh reset-password EMAIL" 2; fi
  case "$email" in *@*) ;; *) die "not an e-mail address: $email" 2 ;; esac
  [ -t 0 ] || die "run this in a terminal: the password is typed, never piped or passed" 2
  if [ "$DRY_RUN" = "1" ]; then printf 'dry-run: would ask for the new password twice and set it for %s\n' "$email"; return 0; fi
  check_tools
  IFS= read -r -s -p "New password for $email: " pw; printf '\n'
  IFS= read -r -s -p "Type it again: " pw2; printf '\n'
  [ -n "$pw" ] || die "no password typed - nothing changed" 2
  [ "$pw" = "$pw2" ] || { pw=""; pw2=""; die "the two passwords differ - nothing changed" 2; }
  pw2=""
  printf '%s\n' "$pw" | dc exec -T backend python scripts/reset_password.py "$email" || rc=$?
  pw=""
  case "$rc" in
    0) ok "done: $email can sign in with the new password" ;;
    3) die "no active user with that e-mail - nothing changed" 3 ;;
    4) die "the password breaks a rule (shown above) - nothing changed" 4 ;;
    *) die "the password could not be set (is the backend running? atp.sh health)" "$rc" ;;
  esac
}

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
    backup-status)  cmd_backup_status ;;
    ops-check)      cmd_ops_check ;;
    wal-cleanup)    cmd_wal_cleanup ;;
    base-backup)    cmd_base_backup "$@" ;;
    restore-offsite) cmd_restore_offsite ;;
    releases)       cmd_releases ;;
    loadtest)       cmd_loadtest "$@" ;;
    loadtest-cleanup) cmd_loadtest_cleanup "$@" ;;
    reset-password) cmd_reset_password "$@" ;;
    help|-h|--help) usage ;;
    *) usage; exit 2 ;;
  esac
}

# Everything above is functions: bash has read the whole file before main runs, so a `git checkout` that rewrites
# this file mid-deploy cannot change what is executing.
main "$@"; exit $?
