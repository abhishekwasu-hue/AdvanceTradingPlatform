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
#   /opt/atp/deploy/atp.sh backup [--verify]       database dump + files archive, encrypted, copied to R2
#   /opt/atp/deploy/atp.sh restore-check           newest backup from R2 -> scratch database -> row counts -> dropped
#   /opt/atp/deploy/atp.sh bootstrap               host checks (as root: runs deploy/hostinger/bootstrap.sh)
#   /opt/atp/deploy/atp.sh schedule | releases | help
#
#   --dry-run (anywhere, or ATP_DRY_RUN=1): prints every step it would take; changes nothing on the host.
#
# Rules it keeps (tests/test_z11_deploy_bundle.py checks them):
#   * secrets are read only from /opt/atp/.env, never created, edited or printed - only key NAMES appear in output;
#     .env must exist, be mode 600 and hold every required key; SECRETS_ENCRYPTION_KEY must be the one this host
#     first deployed with (a fingerprint is kept in .deploy/) - it is never rotated here;
#   * fails closed: any failed step stops the run with a non-zero exit before the next one changes anything;
#   * images are built from one commit and tagged with it (deploy/compose.release.yml); a rollback reuses them;
#   * migrations run through backend/scripts/migrate_guard.py (refused while NSE is open) after an encrypted
#     pre-migration backup; it never runs `alembic downgrade`;
#   * feature flags and every LIVE switch are left exactly as they are (checked before and after a deploy); profiles
#     come only from COMPOSE_PROFILES in .env;
#   * idempotent: running a command again converges on the same state.
#
# Exit codes: 0 ok | 1 failed | 2 usage | 3 migration refused (NSE open) | 10 owner action needed | 20 key changed
set -Eeuo pipefail
umask 077

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
APP_IMAGES=(atp-backend atp-worker atp-frontend atp-research-worker)

RELEASE=""          # image tag compose uses (ATP_RELEASE)
DB_AHEAD=""         # MIGRATION_ALLOW_DB_AHEAD for compose
WARNS=0
FAILS=0

# --- output -----------------------------------------------------------------------------------------------------
step() { printf '\n==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
ok()   { printf '    ok    %s\n' "$*"; }
warn() { WARNS=$((WARNS + 1)); printf '    WARN  %s\n' "$*"; }
bad()  { FAILS=$((FAILS + 1)); printf '    FAIL  %s\n' "$*"; }
die()  { local code="${2:-1}"; printf '\nFAILED: %s\n' "$1" >&2; exit "$code"; }
# shellcheck disable=SC2329  # invoked by the ERR trap below
on_err() { [ "$BASHPID" = "$$" ] || return 0; printf '\nFAILED: a command failed at line %s (exit %s) - stopped before the next step.\n' "$1" "$2" >&2; }
trap 'on_err "$LINENO" "$?"' ERR

# Mutating command: printed, then run - or only printed under --dry-run. Arguments never carry a secret.
run() {
  printf '    + %s\n' "$*"
  [ "$DRY_RUN" = "1" ] && return 0
  "$@"
}

# --- .env (read-only, never sourced: a value is data, not shell) -------------------------------------------------
env_get() {
  [ -f "$ENV_FILE" ] || return 0
  local line
  line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?$1=" "$ENV_FILE" | tail -n 1 || true)"
  line="${line#*=}"; line="${line%$'\r'}"
  case "$line" in \"*\") line="${line#\"}"; line="${line%\"}" ;; \'*\') line="${line#\'}"; line="${line%\'}" ;; esac
  printf '%s' "$line"
}
# A key counts as set when it has a value that is not a placeholder.
env_set() {
  local v; v="$(env_get "$1")"
  case "$v" in ""|CHANGE_ME|change-me|changeme|"<"*">") return 1 ;; esac
  return 0
}
is_true() { case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in 1|true|yes|on) return 0 ;; esac; return 1; }

pg_user() { local v; v="$(env_get POSTGRES_USER)"; printf '%s' "${v:-atp_user}"; }
pg_db()   { local v; v="$(env_get POSTGRES_DB)"; printf '%s' "${v:-advance_trading_platform}"; }

# --- state (.deploy/, never secret) ------------------------------------------------------------------------------
state_init() { [ "$DRY_RUN" = "1" ] || mkdir -p "$STATE_DIR/logs"; }
current_sha() { cat "$STATE_DIR/current" 2>/dev/null || true; }
key_fingerprint() {
  # printf is a builtin: the key never appears in a process list. Only the hash is kept.
  printf 'atp-z11-key-fingerprint:%s' "$(env_get SECRETS_ENCRYPTION_KEY)" | sha256sum | cut -d' ' -f1
}
db_ahead_marker() { [ -f "$STATE_DIR/db_ahead" ]; }

# releases.log: <utc time> <kind deploy|rollback> <sha> <previous sha|-> <pre-migration backup|-> <rev before|-> <rev after|->
release_line_for() { awk -v s="$1" '$2 == "deploy" && $3 == s' "$STATE_DIR/releases.log" 2>/dev/null | tail -n 1; }

# --- docker compose ----------------------------------------------------------------------------------------------
dc() {
  local ahead="$DB_AHEAD"
  db_ahead_marker && ahead=1
  (cd "$ATP_DIR" && ATP_RELEASE="${RELEASE:-latest}" MIGRATION_ALLOW_DB_AHEAD="$ahead" \
    docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.hostinger.yml \
    -f deploy/compose.release.yml -p "$PROJECT" "$@")
}
rdc() { printf '    + docker compose %s\n' "$*"; [ "$DRY_RUN" = "1" ] && return 0; dc "$@"; }

psql_q() {
  dc exec -T postgres psql -X -A -t -q -v ON_ERROR_STOP=1 -U "$(pg_user)" -d "${2:-$(pg_db)}" -c "$1" 2>/dev/null
}
db_revision() { local r; r="$(psql_q 'select version_num from alembic_version order by 1' | paste -sd, -)" || r=""; printf '%s' "${r:--}"; }
code_head() { { dc run --rm --no-deps -T backend python -m alembic heads 2>/dev/null || true; } | awk '/\(head\)/ {print $1}' | sort | paste -sd, -; }
flags_fingerprint() {
  local h
  h="$(psql_q "select md5(value_json) from platform_controls where key = 'feature_flags'")" || { printf 'no-table'; return 0; }
  printf '%s' "${h:-none}"
}
images_exist() {
  local img
  for img in atp-backend atp-worker atp-frontend; do
    docker image inspect "$img:$1" >/dev/null 2>&1 || return 1
  done
}
release_of() { printf '%s' "${1:0:12}"; }

# After RELEASE is set: is the database ahead of that release's code? Then its API may start only with
# MIGRATION_ALLOW_DB_AHEAD=1 (migrate_guard); the marker keeps later compose runs consistent until a deploy catches up.
sync_db_ahead() {
  [ "$DRY_RUN" = "1" ] && return 0
  local now_rev rel_head
  now_rev="$(db_revision)"; rel_head="$(code_head)"
  if [ -n "$rel_head" ] && [ "$now_rev" != "-" ] && [ "$now_rev" != "$rel_head" ]; then
    DB_AHEAD=1; state_init; printf '%s\n' "$now_rev" > "$STATE_DIR/db_ahead"
    info "database at $now_rev, release $RELEASE knows up to $rel_head: it starts with MIGRATION_ALLOW_DB_AHEAD=1"
  else
    DB_AHEAD=""; rm -f "$STATE_DIR/db_ahead"
  fi
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
      env_set OFFSITE_B2_KEY_ID && env_set OFFSITE_B2_APPLICATION_KEY && { printf 'configured'; return 0; }
      printf 'not_configured:OFFSITE_B2_KEY_ID / OFFSITE_B2_APPLICATION_KEY are empty' ;;
    /*) printf 'not_configured:OFFSITE_REMOTE is a local directory (not off-site)' ;;
    "") printf 'not_configured:OFFSITE_REMOTE is empty (Hostinger: offsite:atp-backups)' ;;
    *) printf 'not_configured:OFFSITE_REMOTE=%s has no remote on this host (Hostinger: offsite:<bucket> for R2)' "${remote%%:*}:" ;;
  esac
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
check_env() {
  local record="${1:-}"
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

  [ "$record" = "report" ] && return 0      # health reports the key check itself (check_key_live), it does not stop
  local fp; fp="$(key_fingerprint)"
  if [ -f "$STATE_DIR/secrets_key.sha256" ]; then
    [ "$(cat "$STATE_DIR/secrets_key.sha256")" = "$fp" ] || die "SECRETS_ENCRYPTION_KEY in .env is NOT the key this server deployed with.
Every stored broker / AI credential depends on it. Put the previous value back in .env - never rotate it from a
deploy (a rotation is a separate procedure: scripts/reencrypt_secrets.py, docs/OPERATIONS.md)." 20
    ok "SECRETS_ENCRYPTION_KEY unchanged since the first deploy"
  elif [ "$record" = "record" ]; then
    if [ "$DRY_RUN" = "1" ]; then info "would record the SECRETS_ENCRYPTION_KEY fingerprint (first deploy)"
    else state_init; printf '%s\n' "$fp" > "$STATE_DIR/secrets_key.sha256"; chmod 600 "$STATE_DIR/secrets_key.sha256"
         ok "SECRETS_ENCRYPTION_KEY fingerprint recorded (first deploy) - later runs refuse a different key"; fi
  else
    info "SECRETS_ENCRYPTION_KEY fingerprint not recorded yet (first deploy records it)"
  fi
}

report_settings() {
  local profiles; profiles="$(env_get COMPOSE_PROFILES)"
  info "compose profiles: ${profiles:-none} (from .env COMPOSE_PROFILES - never added here)"
  local k on=""
  for k in "${LIVE_SWITCHES[@]}"; do is_true "$(env_get "$k")" && on="$on $k"; done
  if [ -n "$on" ]; then warn "LIVE switches set in .env:$on - left as they are (Phase 0 is PAPER only)"; else ok "no LIVE switch set in .env"; fi
  local r2; r2="$(offsite_state)"
  if [ "$r2" = "configured" ]; then ok "off-site backups: R2 configured ($(env_get OFFSITE_REMOTE))"
  else warn "off-site backups: ${r2#not_configured:} - owner blocker: R2 bucket/token in .env (runbook section 6); backups stay on this server until then"; fi
  if env_set HISTORY_OWNER_TENANT_ID && ! env_set HISTORY_OWNER_TENANT_NAME; then
    warn "HISTORY_OWNER_TENANT_ID is set without HISTORY_OWNER_TENANT_NAME - the 1-minute history import refuses until both match"
  fi
  local n=0
  for k in R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY R2_BUCKET R2_ENDPOINT; do env_set "$k" && n=$((n + 1)); done
  case "$n" in 0) info "history import (HB0) R2 token: not set (import stays off)";; 4) ok "history import (HB0) R2 token: set (read-only, used only by the importer)";;
    *) warn "history import (HB0): only $n of R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / R2_BUCKET / R2_ENDPOINT set";; esac
}

lock() {
  state_init
  [ "$DRY_RUN" = "1" ] && return 0
  exec 9>"$STATE_DIR/lock"
  flock -w "${1:-0}" 9 || die "another atp.sh run holds $STATE_DIR/lock - wait for it to finish" 1
}

# --- health ------------------------------------------------------------------------------------------------------
# Deep health acceptable? ok, or degraded only because the database is ahead after a code-only rollback.
deep_ok() {
  local body status mig
  body="$(curl -fsS -m 10 "$API/api/system/health/deep" 2>/dev/null)" || return 1
  status="$(printf '%s' "$body" | json_get status)"
  [ "$status" = "ok" ] && return 0
  if [ "$status" = "degraded" ] && { [ "$DB_AHEAD" = "1" ] || db_ahead_marker; }; then
    mig="$(printf '%s' "$body" | json_get checks.migrations.status)"
    [ "$mig" = "behind" ] && [ "$(printf '%s' "$body" | json_get checks.redis.status)" = "ok" ] \
      && [ "$(printf '%s' "$body" | json_get checks.worker.status)" != "stale_market_open" ] && return 0
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

expected_services() {
  printf '%s\n' postgres redis backend worker frontend caddy backup offsite
  case ",$(env_get COMPOSE_PROFILES)," in *,research,*) printf '%s\n' research-worker ;; esac
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
  local body st
  if body="$(curl -fsS -m 5 "$API/api/system/health" 2>/dev/null)" && [ "$(printf '%s' "$body" | json_get status)" = "ok" ]; then ok "/api/system/health ok"
  else bad "/api/system/health does not answer ok on $API"; fi
  if body="$(curl -sS -m 10 "$API/api/system/health/deep" 2>/dev/null)"; then
    local db_s mig wk rd
    read -r st db_s mig wk rd < <(printf '%s' "$body" | python3 -c 'import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    d = {}
c = d.get("checks") or {}
print(d.get("status") or "unparseable", *[(c.get(k) or {}).get("status") or "-" for k in ("database", "migrations", "worker", "redis")])')
    info "deep: $st (database $db_s, migrations $mig, worker $wk, redis $rd)"
    if [ "$st" = "ok" ]; then ok "deep health ok"
    elif deep_ok; then warn "deep health degraded only because the database is ahead of this code (after a code-only rollback) - deploy the newer release when fixed"
    else bad "deep health $st"; fi
  else bad "/api/system/health/deep unreachable"; fi

  step "public HTTPS through Caddy"
  local domain code; domain="$(env_get DOMAIN)"
  code="$(curl -sk -o /dev/null -m 10 -w '%{http_code}' --resolve "$domain:443:127.0.0.1" "https://$domain/api/system/health" 2>/dev/null || true)"
  if [ "$code" = "200" ]; then ok "https://$domain/api/system/health -> 200"; else bad "https://$domain/api/system/health -> ${code:-no answer} (caddy logs: docker compose ... logs caddy)"; fi

  step "release"
  local cur; cur="$(current_sha)"
  info "running: ${cur:0:12}${cur:+ }$(git -C "$ATP_DIR" log -1 --format='%cd %s' --date=format:'%Y-%m-%d %H:%M' "${cur:-HEAD}" 2>/dev/null | cut -c1-80)"
  [ -n "$cur" ] && [ "$(git -C "$ATP_DIR" rev-parse HEAD)" != "$cur" ] && warn "checkout $(git -C "$ATP_DIR" rev-parse --short=12 HEAD) differs from the running release ${cur:0:12}"
  db_ahead_marker && warn "database is ahead of the running code since a rollback (revision $(cat "$STATE_DIR/db_ahead"))"

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
  [ -f "$STATE_DIR/last_restore_check" ] && info "last restore check: $(cat "$STATE_DIR/last_restore_check")"

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
  local fp; fp="$(key_fingerprint)"
  if [ -f "$STATE_DIR/secrets_key.sha256" ]; then
    if [ "$(cat "$STATE_DIR/secrets_key.sha256")" = "$fp" ]; then ok "fingerprint matches the first deploy"
    else bad "SECRETS_ENCRYPTION_KEY in .env differs from the one this host deployed with - put the previous value back"; fi
  else warn "no key fingerprint recorded yet (the first deploy records it)"; fi
  if dc exec -T backend test -f scripts/check_secrets_key.py >/dev/null 2>&1; then
    local out
    if out="$(dc exec -T backend python scripts/check_secrets_key.py 2>&1)"; then ok "$out"; else bad "$out"; fi
  else info "key check script not in this image (older release) - skipped"; fi
}

report_flags() {
  local json out
  json="$(psql_q "select value_json from platform_controls where key = 'feature_flags'" || true)"
  out="$(printf '%s' "$json" | python3 -c 'import json, sys
try:
    flags = (json.loads(sys.stdin.read() or "{}") or {}).get("flags") or {}
except Exception:
    flags = {}
on = sorted(n for n, v in flags.items() if isinstance(v, dict) and v.get("on"))
print("stored ON: " + (", ".join(on) if on else "none"))
live = flags.get("live_trading")
print("live:" + ("default" if live is None else ("on" if live.get("on") else "off")))')"
  info "flags $(printf '%s\n' "$out" | head -n 1)"
  case "$(printf '%s\n' "$out" | tail -n 1)" in
    live:off) ok "flag live_trading is off" ;;
    live:on) warn "flag live_trading is ON: Phase 0 has no LIVE - turn it off in Admin > Controls (this script never changes flags)" ;;
    *) warn "flag live_trading is ON by default (kill-flag semantics): Phase 0 has no LIVE - turn it off in Admin > Controls (this script never changes flags)" ;;
  esac
  report_settings
}

health_summary() {
  printf '\n'
  if [ "$FAILS" -gt 0 ]; then printf 'health: FAILED - %s failure(s), %s warning(s)\n' "$FAILS" "$WARNS"; return 1; fi
  printf 'health: OK - %s warning(s)\n' "$WARNS"
}

# --- commands ----------------------------------------------------------------------------------------------------
cmd_health() {
  [ "$(id -u)" != "0" ] || die "run as the deploy user (atp), not root" 2
  check_tools
  check_env report
  RELEASE="$(release_of "$(current_sha)")"
  health_checks
  health_summary
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
  local d restore_dir local_dir
  restore_dir="$(env_get OFFSITE_RESTORE_DIR)"; local_dir="$(env_get OFFSITE_LOCAL_DIR)"
  for d in "${restore_dir:-/var/backups/atp-restore-test}" "${local_dir:-/var/backups/atp-offsite}"; do
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
  git -C "$ATP_DIR" fetch -q --tags origin      # also under --dry-run: it only updates origin/*, nothing that runs
  local sha prev
  sha="$(git -C "$ATP_DIR" rev-parse --verify -q "$ref^{commit}")" || die "unknown ref '$ref'" 2
  if ! git -C "$ATP_DIR" merge-base --is-ancestor "$sha" origin/main && [ "${ATP_ALLOW_UNMERGED:-0}" != "1" ]; then
    die "$ref ($sha) is not on origin/main - deploy merged code only (ATP_ALLOW_UNMERGED=1 to override knowingly)" 2
  fi
  git -C "$ATP_DIR" cat-file -e "$sha:deploy/atp.sh" 2>/dev/null || die "$ref predates deploy/atp.sh - cannot deploy it with this bundle" 2
  prev="$(current_sha)"
  RELEASE="$(release_of "$sha")"
  info "deploying ${sha:0:12} $(git -C "$ATP_DIR" log -1 --format='%s' "$sha" | cut -c1-80)"
  info "running now: ${prev:0:12}${prev:+ }${prev:-nothing recorded (first deploy)}"
  [ "$prev" = "$sha" ] && info "same release as running - converging (idempotent re-run)"
  run git -C "$ATP_DIR" checkout -q --detach "$sha"

  step "database and cache"
  rdc up -d --wait postgres redis

  step "images atp-*:$RELEASE (built from the commit, reused by a rollback)"
  if [ "$DRY_RUN" != "1" ] && images_exist "$RELEASE"; then ok "images for $RELEASE already built"
  else rdc build --pull; fi

  step "migrations"
  local before="" after="" head="" flags_before backup_file="-"
  if [ "$DRY_RUN" = "1" ]; then
    info "would compare the database revision with the image's alembic head; if behind: encrypted pre-migration backup, then scripts/migrate_guard.py (refused while NSE is open)"
    flags_before="dry-run"
  else
    before="$(db_revision)"; head="$(code_head)"
    [ -n "$head" ] || die "could not read the alembic head from atp-backend:$RELEASE" 1
    flags_before="$(flags_fingerprint)"
    info "database at ${before}, code head ${head}"
    if [ "$before" = "$head" ]; then
      ok "schema at head - no migration, no pre-migration backup needed"
    else
      step "pre-migration backup (encrypted, before anything changes)"
      rdc run --rm --no-deps -T --entrypoint sh backup /scripts/backup.sh || die "pre-migration backup failed - nothing was migrated or restarted" 1
      backup_file="$(dc run --rm --no-deps -T --entrypoint sh backup -c 'readlink /backups/latest' | tr -d '\r' | tail -n 1)"
      [ -n "$backup_file" ] || die "pre-migration backup not found after backup.sh" 1
      ok "pre-migration backup: $backup_file"
      if [ "$(offsite_state)" = "configured" ]; then
        if rdc run --rm --no-deps -T offsite --once; then ok "copied to R2"; else warn "R2 copy failed - the local backup stands; the hourly copy retries"; fi
      else warn "R2 not configured - the pre-migration backup is on this server only"; fi
      step "alembic upgrade head (scripts/migrate_guard.py)"
      local rc=0
      rdc run --rm --no-deps -T backend python scripts/migrate_guard.py || rc=$?
      [ "$rc" = "3" ] && die "migration refused: NSE session open. Nothing was restarted; the old release still runs. Retry after 15:30 IST." 3
      [ "$rc" = "0" ] || die "migration failed (exit $rc). The old release still runs; the database is as the backup $backup_file left it, or partly migrated - see runbook 'Troubleshooting'." 1
      after="$(db_revision)"
      [ "$after" = "$head" ] || die "after migrating the database is at $after, expected $head" 1
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
    if [ -n "$prev" ] && images_exist "$(release_of "$prev")"; then
      step "putting the previous API back (${prev:0:12})"
      RELEASE="$(release_of "$prev")"
      sync_db_ahead
      rdc up -d --no-deps --no-build backend || true
      if wait_api; then info "previous API healthy again - worker and frontend were not touched"; else info "previous API not healthy either - see runbook 'Troubleshooting'"; fi
      run git -C "$ATP_DIR" checkout -q --detach "$prev"
    fi
    die "deploy of ${sha:0:12} failed at the API health check" 1
  fi

  step "worker, frontend and the rest (exactly one worker)"
  rdc up -d --no-build
  if [ "$DRY_RUN" != "1" ]; then
    state_init
    printf '%s deploy %s %s %s %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$sha" "${prev:--}" "$backup_file" "${before:--}" "${after:--}" >> "$STATE_DIR/releases.log"
    printf '%s\n' "$sha" > "$STATE_DIR/current"
    rm -f "$STATE_DIR/db_ahead"
    record_infra_digests "$sha"
  fi

  step "health"
  if [ "$DRY_RUN" != "1" ]; then
    local waited=0
    while :; do
      FAILS=0; WARNS=0
      health_checks > "$STATE_DIR/logs/health-last.txt" 2>&1 || true
      [ "$FAILS" = "0" ] && break
      [ "$waited" -ge "$HEALTH_TIMEOUT" ] && break
      sleep "$POLL"; waited=$((waited + POLL))
    done
    cat "$STATE_DIR/logs/health-last.txt"
    local flags_after; flags_after="$(flags_fingerprint)"
    if [ "$flags_before" = "$flags_after" ] || [ "$flags_before" = "no-table" ]; then ok "feature flags unchanged by the deploy"
    else bad "feature flags CHANGED during the deploy ($flags_before -> $flags_after) - check Admin > Controls and the audit log"; fi
  else
    info "would run every health check and compare the feature flags before/after"
  fi

  cmd_schedule quiet
  prune_images
  if ! health_summary; then
    printf '\nThe release runs but is not healthy. Roll back with:\n    %s/deploy/atp.sh rollback\n' "$ATP_DIR"
    exit 1
  fi
  printf 'deployed %s\n' "${sha:0:12}"
}

record_infra_digests() {
  local img out=""
  for img in postgres:17-alpine redis:7-alpine caddy:2-alpine rclone/rclone:1; do
    out="$out $img=$(docker image inspect --format '{{index .RepoDigests 0}}' "$img" 2>/dev/null | sed 's/.*@//' | cut -c1-19)"
  done
  printf '%s %s infra%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$out" >> "$STATE_DIR/images.log"
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
}

cmd_rollback() {
  local to="" restore_db=0
  while [ $# -gt 0 ]; do
    case "$1" in --to) to="${2:-}"; [ -n "$to" ] || die "--to needs a commit" 2; shift 2 ;; --restore-db) restore_db=1; shift ;; *) die "unknown rollback option $1" 2 ;; esac
  done
  [ "$(id -u)" != "0" ] || die "run as the deploy user (atp), not root" 2
  [ -n "$to" ] && [ "$restore_db" = "1" ] && die "--restore-db undoes exactly one release: roll back one release at a time (no --to)" 2
  lock 0
  step "preflight"
  check_tools
  check_repo
  check_env
  local cur line target backup rev_before rev_after
  cur="$(current_sha)"
  [ -n "$cur" ] || die "no release recorded in $STATE_DIR/current - nothing to roll back" 2
  line="$(release_line_for "$cur")"
  [ -n "$line" ] || die "no deploy of ${cur:0:12} in $STATE_DIR/releases.log - name the target: rollback --to <sha>" 2
  read -r _ _ _ target backup rev_before rev_after <<<"$line"
  if [ -n "$to" ]; then target="$(git -C "$ATP_DIR" rev-parse --verify -q "$to^{commit}")" || die "unknown commit $to" 2; fi
  [ -n "$target" ] && [ "$target" != "-" ] || die "${cur:0:12} was the first release deployed here - there is nothing before it" 2
  [ "$target" != "$cur" ] || die "${cur:0:12} is already running" 2
  git -C "$ATP_DIR" cat-file -e "$target:deploy/atp.sh" 2>/dev/null || die "${target:0:12} predates deploy/atp.sh - roll back to a release deployed with this bundle" 2

  step "plan"
  info "running ${cur:0:12} -> roll back to ${target:0:12}"
  if [ "$rev_before" != "$rev_after" ]; then
    info "${cur:0:12} migrated the schema $rev_before -> $rev_after (pre-migration backup: $backup)"
    if [ "$restore_db" = "1" ]; then
      info "--restore-db: the database goes back to $backup; rows written since then stay only in the kept copy"
      info "(the current database is KEPT under a new name - nothing is destroyed)"
    else
      info "code-only: the older images run on the newer schema (migrations are additive; the API starts with"
      info "MIGRATION_ALLOW_DB_AHEAD=1). If that migration dropped or renamed anything, use --restore-db instead."
    fi
  else
    info "${cur:0:12} did not change the schema: code-only rollback"
    [ "$restore_db" = "1" ] && die "--restore-db is not needed: ${cur:0:12} did not migrate (no pre-migration backup was taken)" 2
  fi
  if [ "$restore_db" = "1" ] && [ "$backup" = "-" ]; then die "no pre-migration backup recorded for ${cur:0:12}" 2; fi
  if [ "$(date +%u)" -le 5 ] && [ "$(date +%H%M)" -ge 0900 ] && [ "$(date +%H%M)" -le 1530 ]; then
    warn "NSE hours: the worker restarts during the session (protective exits resume on its next cycle)"
  fi

  local db; db="$(pg_db)"
  if [ "$restore_db" = "1" ] && [ "$DRY_RUN" != "1" ]; then
    local answer="${ATP_CONFIRM_DB:-}"
    if [ -z "$answer" ]; then
      printf '\nType the database name (%s) to replace its contents with %s: ' "$db" "$backup"
      read -r answer </dev/tty || answer=""
    fi
    [ "$answer" = "$db" ] || die "not confirmed - nothing changed" 1
  fi

  RELEASE="$(release_of "$target")"
  step "code ${target:0:12}"
  run git -C "$ATP_DIR" checkout -q --detach "$target"
  if [ "$DRY_RUN" != "1" ] && images_exist "$RELEASE"; then ok "images atp-*:$RELEASE present (no rebuild)"
  else rdc build; fi

  local app_services=(backend worker)
  case ",$(env_get COMPOSE_PROFILES)," in *,research,*) app_services+=(research-worker) ;; esac
  if [ "$restore_db" = "1" ]; then
    local stamp scratch kept
    stamp="$(date -u +%Y%m%d%H%M%S)"; scratch="${db}_rb_${stamp}"; kept="${db}_pre_rollback_${stamp}"
    step "database back to $backup (restored beside the live one first; the live one keeps serving meanwhile)"
    rdc run --rm --no-deps -T --entrypoint sh backup /scripts/backup.sh || die "safety backup of the current database failed - nothing changed" 1
    rdc exec -T postgres psql -X -q -v ON_ERROR_STOP=1 -U "$(pg_user)" -d postgres -c "CREATE DATABASE \"$scratch\""
    # shellcheck disable=SC2016  # $0 / $ATP_TARGET_DB expand inside the backup container
    rdc run --rm --no-deps -T -e RESTORE_CONFIRM=yes -e ATP_TARGET_DB="$scratch" --entrypoint sh backup -c \
      '. /scripts/common.sh && sh /scripts/restore.sh "/backups/$0" "$(with_db "$(pg_url)" "$ATP_TARGET_DB")"' "$backup" \
      || die "restore of $backup failed - the live database is untouched (scratch $scratch left for inspection)" 1
    if [ "$DRY_RUN" != "1" ]; then
      local got; got="$(psql_q 'select version_num from alembic_version order by 1' "$scratch" | paste -sd, -)" || got=""
      [ "$got" = "$rev_before" ] || die "the restored copy is at revision ${got:-none}, expected $rev_before - live database untouched (scratch $scratch kept)" 1
      ok "restored copy at revision $got"
    fi
    step "swap: stop the app, keep the live database as $kept, put the restored copy in its place"
    rdc stop "${app_services[@]}"
    rdc exec -T postgres psql -X -q -A -t -v ON_ERROR_STOP=1 -U "$(pg_user)" -d postgres \
      -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$db' AND pid <> pg_backend_pid()" \
      -c "ALTER DATABASE \"$db\" RENAME TO \"$kept\"" -c "ALTER DATABASE \"$scratch\" RENAME TO \"$db\"" \
      || { dc up -d --no-deps --no-build "${app_services[@]}" || true; die "database swap failed - the app was restarted on the live database" 1; }
    ok "$db is the restored copy; the previous database is kept as $kept (drop it once satisfied: runbook section 8)"
  fi

  sync_db_ahead      # is the database ahead of the target code? (code-only rollback of a migrating release)

  step "rolling restart on ${target:0:12}: API first"
  rdc up -d --no-deps --no-build backend
  wait_api || die "the rolled-back API is not healthy - logs: docker compose ... logs backend; runbook 'Troubleshooting'" 1
  rdc up -d --no-build
  if [ "$DRY_RUN" = "1" ]; then info "would run every health check"; printf '\ndry-run: nothing changed\n'; return 0; fi
  printf '%s rollback %s %s %s %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$target" "$cur" "-" "$rev_after" "$(db_revision)" >> "$STATE_DIR/releases.log"
  printf '%s\n' "$target" > "$STATE_DIR/current"
  step "health"
  FAILS=0; WARNS=0
  health_checks
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
    step "database dump (pg_dump, AES-256, SHA-256 sidecar, retention ${BACKUP_RETENTION_DAYS:-14} days)"
    local fresh=0
    if [ "$scheduled" = "1" ] && [ "$DRY_RUN" != "1" ]; then
      local last; last="$(dc exec -T backup cat /backups/LAST_BACKUP_OK 2>/dev/null || true)"
      [ -n "$last" ] && [ $(( $(date -u +%s) - $(date -u -d "$last" +%s 2>/dev/null || echo 0) )) -lt 72000 ] && fresh=1
    fi
    if [ "$fresh" = "1" ]; then ok "the backup service dumped within 20 h - not dumping twice"
    else rdc run --rm --no-deps -T --entrypoint sh backup /scripts/backup.sh || die "database backup failed" 1; fi
  fi

  step "files archive (deploy state + BACKUP_EXTRA_DIRS), encrypted"
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
  tar --exclude='.deploy/lock' --exclude='.deploy/logs' -czf - "${members[@]}" \
    | ATP_FILES_PASS="$pass" openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:ATP_FILES_PASS -out "$out.part"
  mv "$out.part" "$out"
  sha256sum "$out" | cut -d' ' -f1 > "$out.sha256"
  ok "$(basename "$out") ($(wc -c < "$out" | tr -d ' ') bytes)"
  local keep="${BACKUP_FILES_KEEP:-14}" old
  while read -r old; do
    [ -n "$old" ] || continue
    rm -f "$old" "$old.sha256"; info "retention: removed $(basename "$old")"
  done < <(find "$dir" -maxdepth 1 -name 'atp_files_*.tar.gz.enc' | sort -r | tail -n +"$((keep + 1))")   # UTC stamp in the name: newest first
}

# Decrypts a files archive and lists it (proves the passphrase and the archive). $1 = local path.
verify_files_archive() {
  local f="$1" pass
  [ -f "$f" ] || { bad "files archive $f missing"; return 0; }
  if [ -f "$f.sha256" ] && [ "$(sha256sum "$f" | cut -d' ' -f1)" != "$(cat "$f.sha256")" ]; then bad "$(basename "$f"): sha256 mismatch"; return 0; fi
  pass="$(env_get BACKUP_ENCRYPTION_PASSPHRASE)"
  local n
  if n="$(ATP_FILES_PASS="$pass" openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:ATP_FILES_PASS -in "$f" | tar -tzf - | wc -l)"; then
    ok "$(basename "$f"): decrypts, $n entries"
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
  local local_only="${1:-0}" require="${2:-0}" r2 src file report status
  r2="$(offsite_state)"
  if [ "$local_only" = "0" ] && [ "$r2" = "configured" ]; then
    src="R2"
    step "newest database backup from R2 -> /var/backups/atp-restore-test"
    if [ "$DRY_RUN" = "1" ]; then rdc run --rm --no-deps -T --entrypoint sh offsite /scripts/offsite_fetch.sh /restore; file="/restore/<newest>"
    else
      file="$(dc run --rm --no-deps -T --entrypoint sh offsite /scripts/offsite_fetch.sh /restore | tr -d '\r' | tail -n 1)" || { bad "could not fetch a backup from R2"; return 0; }
      ok "fetched $(basename "$file")"
    fi
  else
    src="local"
    if [ "$r2" != "configured" ]; then
      if [ "$require" = "1" ]; then bad "R2 not configured (${r2#not_configured:}) - --require-offsite"; return 0; fi
      warn "R2 not configured yet (${r2#not_configured:}) - verifying the LOCAL copy only (owner blocker: R2 in .env)"
    fi
    file="latest"
  fi

  step "restore into a scratch database, compare row counts, drop it (scripts/backup/verify_backup.sh)"
  if [ "$DRY_RUN" = "1" ]; then rdc run --rm --no-deps -T --entrypoint sh backup /scripts/verify_backup.sh "$file"; return 0; fi
  report="$(dc run --rm --no-deps -T --entrypoint sh backup /scripts/verify_backup.sh "$file" 2>"$STATE_DIR/logs/verify-stderr.txt" | tr -d '\r' | tail -n 1)" || true
  status="$(printf '%s' "$report" | json_get status)"
  if [ "$status" = "ok" ]; then
    ok "$src backup $(printf '%s' "$report" | json_get file) restored: alembic $(printf '%s' "$report" | json_get alembic.copy)"
    printf '%s' "$report" | python3 -c 'import json, sys
c = json.load(sys.stdin).get("counts") or {}
print("    rows (copy / live): " + ", ".join("%s %s/%s" % (t, v.get("copy"), v.get("source")) for t, v in c.items()))'
  else
    bad "$src backup did not verify: ${status:-no report} $(printf '%s' "$report" | json_get notes) (stderr: $STATE_DIR/logs/verify-stderr.txt)"
  fi
  printf '%s source=%s status=%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$src" "${status:-none}" "$report" >> "$STATE_DIR/restore-checks.log"
  printf '%s %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$src" "${status:-none}" > "$STATE_DIR/last_restore_check"

  step "files archive"
  local newest
  if [ "$src" = "R2" ]; then
    # shellcheck disable=SC2016  # $OFFSITE_REMOTE expands inside the rclone container
    newest="$(dc run --rm --no-deps -T --entrypoint sh offsite -c \
      'f="$(rclone lsf "$OFFSITE_REMOTE/files" --files-only --include "atp_files_*.enc" | sort | tail -n 1)"; [ -n "$f" ] && rclone copyto "$OFFSITE_REMOTE/files/$f" "/restore/$f" && rclone copyto "$OFFSITE_REMOTE/files/$f.sha256" "/restore/$f.sha256"; echo "$f"' \
      | tr -d '\r' | tail -n 1 || true)"
    local restore_dir; restore_dir="$(env_get OFFSITE_RESTORE_DIR)"
    if [ -n "$newest" ]; then verify_files_archive "${restore_dir:-/var/backups/atp-restore-test}/$newest"; else warn "no files archive in R2 yet (atp.sh backup puts one there)"; fi
  else
    newest="$(find "$FILES_BACKUP_DIR" -maxdepth 1 -name 'atp_files_*.tar.gz.enc' 2>/dev/null | sort | tail -n 1 || true)"
    if [ -n "$newest" ]; then verify_files_archive "$newest"; else warn "no files archive yet (atp.sh backup makes one)"; fi
  fi
}

cmd_schedule() {
  local quiet="${1:-}"
  [ "$quiet" = "quiet" ] || { lock 0; check_tools; }
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
  printf '%-20s %-8s %-12s %-12s %-40s %s\n' "time (UTC)" kind release previous "pre-migration backup" "schema"
  awk '{printf "%-20s %-8s %-12s %-12s %-40s %s -> %s\n", $1, $2, substr($3,1,12), substr($4,1,12), $5, $6, $7}' "$STATE_DIR/releases.log"
  printf 'running: %s\n' "$(current_sha | cut -c1-12)"
}

cmd_install() {
  [ "$(id -u)" != "0" ] || die "run as the deploy user (atp): first the bootstrap block as root, then this as atp" 2
  cmd_bootstrap
  WARNS=0
  cmd_deploy "${1:-origin/main}"
  WARNS=0
  step "first backup + restore check"
  cmd_backup --verify
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
