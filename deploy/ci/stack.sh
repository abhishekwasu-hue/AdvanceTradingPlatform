#!/usr/bin/env bash
# The CI staging stack (Lane D, Phase 0 exit criteria): the production compose files plus deploy/ci/compose.ci.yml,
# as its own compose project (default `atp-ci`) with a throwaway env file. Used by .github/workflows/staging-stack.yml
# and, for the 500-user load test, by the owner on the Hostinger host (deploy/ci/loadtest_500.sh) - it never touches
# the production project (`atp`), its .env, its volumes or its ports.
#
#   deploy/ci/stack.sh up                 build the release's images, start the stack, wait until healthy
#   deploy/ci/stack.sh acceptance         the Phase 0 acceptance flow (deploy/ci/acceptance.py)
#   deploy/ci/stack.sh seed N             N load-test users (deploy/ci/seed_users.py) -> $STATE/load_accounts.csv
#   deploy/ci/stack.sh load [options]     the sharded load test (backend/scripts/loadtest.py); see cmd_load
#   deploy/ci/stack.sh run ARGS...        python ARGS in the ci-runner tool container
#   deploy/ci/stack.sh api-workers N [--cpus C]  restart the API with N processes (default 2, as on the KVM 2), at most C CPUs
#   deploy/ci/stack.sh pin [SERVER CLIENT | kvm2]  the server side (API, worker, Postgres, Redis, Caddy, frontend) on
#                                         the CPUs SERVER, the load clients and the fake broker on CLIENT; `kvm2`: two CPUs
#                                         on two physical cores for the server, the rest for the clients; no arguments:
#                                         every container on every CPU again
#   deploy/ci/stack.sh logs | ps | diag   container logs / state; diag writes them under $STATE/diag
#   deploy/ci/stack.sh down               stop and delete the stack and its volumes
#
# Settings (environment):
#   ATP_CI_PROJECT   compose project, default atp-ci (refuses `atp`, the production project)
#   ATP_CI_STATE     working directory for the env file, reports and logs (default $RUNNER_TEMP or /tmp, /atp-ci-state)
#   ATP_RELEASE      image tag (default: the first 12 characters of HEAD), as deploy/atp.sh tags release images
#   ATP_CI_CLOCK     auto (default: shift into an NSE session when outside one) | real
#   ATP_CI_REBUILD   1 = rebuild images that already exist
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROJECT="${ATP_CI_PROJECT:-atp-ci}"
STATE="${ATP_CI_STATE:-${RUNNER_TEMP:-/tmp}/atp-ci-state}"
ENV_FILE="$STATE/ci.env"
ATP_RELEASE="${ATP_RELEASE:-$(git -C "$ROOT" rev-parse --short=12 HEAD)}"
ADMIN_EMAIL="ci-admin@example.com"
ATP_CI_UID="$(id -u)"
ATP_CI_GID="$(id -g)"
ATP_CI_STATE="$STATE"
export ATP_RELEASE ATP_CI_STATE ATP_CI_UID ATP_CI_GID

die() { printf 'stack.sh: %s\n' "$1" >&2; exit "${2:-1}"; }
step() { printf '\n==> %s\n' "$*"; }

[ "$PROJECT" != "atp" ] || die "refusing: 'atp' is the production compose project" 2
case "$PROJECT" in *[!a-z0-9_-]*|"") die "ATP_CI_PROJECT must be lowercase letters, digits, - or _" 2 ;; esac

dc() {
  (cd "$ROOT" && docker compose --env-file "$ENV_FILE" -f docker-compose.yml -f docker-compose.prod.yml \
    -f docker-compose.hostinger.yml -f deploy/compose.release.yml -f deploy/ci/compose.ci.yml -p "$PROJECT" "$@")
}

env_value() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1; }
rand() { python3 -c 'import secrets; print(secrets.token_urlsafe(32))'; }

# The stack's own settings: random secrets, the clock - written once per state directory, mode 600, never printed.
ensure_env() {
  mkdir -p "$STATE"
  chmod 700 "$STATE"
  [ -s "$ENV_FILE" ] && return 0
  local clock
  if [ "${ATP_CI_CLOCK:-auto}" = "real" ]; then clock="$(python3 "$SCRIPT_DIR/ci_clock.py" --mode real)"
  else clock="$(python3 "$SCRIPT_DIR/ci_clock.py")"; fi
  umask 077
  {
    echo "# Throwaway settings of the CI staging stack ($PROJECT), written by deploy/ci/stack.sh. Not a server .env."
    echo "DOMAIN=atp.staging.internal"
    echo "ACME_EMAIL=ci@example.com"
    echo "CADDYFILE=Caddyfile.ip"
    echo "ENVIRONMENT=production"
    echo "POSTGRES_PASSWORD=$(rand)"
    echo "JWT_SECRET_KEY=$(rand)"
    echo "SECRETS_ENCRYPTION_KEY=$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
    echo "METRICS_TOKEN=$(rand)"
    echo "BACKUP_ENCRYPTION_PASSPHRASE=$(rand)"
    echo "SUPER_ADMIN_EMAILS=$ADMIN_EMAIL"
    echo "ATP_CI_ADMIN_EMAIL=$ADMIN_EMAIL"
    echo "EMAIL_VERIFICATION_REQUIRED=false"
    echo "WORKER_CYCLE_SECONDS=${WORKER_CYCLE_SECONDS:-10}"
    # A fresh, empty database inside a (shifted) NSE session: scripts/migrate_guard.py would refuse to create the schema.
    echo "MIGRATION_FORCE=1"
    printf '%s\n' "$clock"
  } > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
}

image_exists() { docker image inspect "$1" >/dev/null 2>&1; }

cmd_build() {
  ensure_env
  step "images for release $ATP_RELEASE (built from this checkout; reused when present)"
  local rebuild="${ATP_CI_REBUILD:-0}"
  if [ "$rebuild" = "1" ] || ! image_exists "atp-backend:$ATP_RELEASE"; then dc build fake-broker; else echo "    atp-backend:$ATP_RELEASE present"; fi
  if [ "$rebuild" = "1" ] || ! image_exists "atp-frontend:$ATP_RELEASE"; then dc build frontend; else echo "    atp-frontend:$ATP_RELEASE present"; fi
  if [ "$rebuild" = "1" ] || ! image_exists "atp-ci-app:$ATP_RELEASE"; then dc build backend; else echo "    atp-ci-app:$ATP_RELEASE present"; fi
}

cmd_up() {
  ensure_env
  step "clock: $(env_value ATP_CI_CLOCK_NOTE)"
  cmd_build
  step "start $PROJECT (postgres, redis, fake broker, API, worker, frontend, caddy) and wait until healthy"
  dc up -d --wait --wait-timeout "${ATP_CI_WAIT_SECONDS:-420}" || { cmd_diag; die "the stack did not become healthy - logs in $STATE/diag"; }
  step "Caddy's internal root certificate (the clients verify the edge with it)"
  local i
  for i in $(seq 1 30); do
    if dc exec -T caddy cat /data/caddy/pki/authorities/local/root.crt > "$STATE/caddy-root.crt.tmp" 2>/dev/null && [ -s "$STATE/caddy-root.crt.tmp" ]; then
      mv "$STATE/caddy-root.crt.tmp" "$STATE/caddy-root.crt"; break
    fi
    # the internal CA is created on the first TLS handshake: knock once
    curl -sk -o /dev/null -m 5 "https://127.0.0.1:${ATP_CI_HTTPS_PORT:-28443}/" -H "Host: $(env_value DOMAIN)" || true
    sleep 2
  done
  [ -s "$STATE/caddy-root.crt" ] || die "Caddy's root certificate did not appear"
  chmod 644 "$STATE/caddy-root.crt"
  step "API through the edge"
  cmd_run -c 'import os, httpx; r = httpx.get(os.environ["ATP_CI_BASE_URL"] + "/api/system/health/deep", verify=os.environ["ATP_CI_CADDY_CA"], timeout=20); print(r.status_code, r.text[:300]); assert r.status_code == 200'
  dc ps
}

cmd_run() { ensure_env; dc --profile tools run --rm -T ci-runner "$@"; }

cmd_acceptance() {
  ensure_env
  step "Phase 0 acceptance flow ($(env_value ATP_CI_CLOCK_NOTE))"
  cmd_run /ci/acceptance.py --report /state/acceptance.json "$@"
}

cmd_seed() {
  ensure_env
  local n="${1:-500}"
  step "seed $n load-test users (own organisation each, a fake Dhan session each)"
  cmd_run /ci/seed_users.py --count "$n" --out /state/load_accounts.csv
}

# The load test, sharded: each shard is its own ci-runner container (its own IP on the stack network) running
# backend/scripts/loadtest.py for its slice of users and accounts, through Caddy; the raw samples are merged after.
#   --users 500 --seconds 300 --ramp 150 --think 30 [--shards N] [--name DIR] [--label TEXT] [--report FILE] [--in-step]
#   [--sign-in S] [--login-probe-every P]
# Three phases by default (loadtest.py's module docstring, "Sign-in first"): every user logs in during a sign-in phase
# of S seconds (default: the ramp's length; reported, every login must succeed), opens the app on that session over the
# ramp, and in the steady window one password login every P seconds (default 2, all shards together: 1.2 a minute per
# client IP) is the judged `login` - at 500 sessions, not inside the ramp's bcrypt wave. --sign-in 0: the one-phase run
# (the logins inside the ramp, judged), as --in-step (the login-burst profile) always is.
cmd_load() {
  ensure_env
  local users=500 seconds=450 ramp=150 think=10 shards=0 label="" name="load" report="" in_step=0 sign_in="" probe_every=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --users) users="$2"; shift 2 ;; --seconds) seconds="$2"; shift 2 ;; --ramp) ramp="$2"; shift 2 ;;
      --think) think="$2"; shift 2 ;; --shards) shards="$2"; shift 2 ;; --label) label="$2"; shift 2 ;;
      --report) report="$2"; shift 2 ;; --name) name="$2"; shift 2 ;; --in-step) in_step=1; shift ;;
      --sign-in) sign_in="$2"; shift 2 ;; --login-probe-every) probe_every="$2"; shift 2 ;;
      *) die "unknown load option $1" 2 ;;
    esac
  done
  # --in-step passes every shard as shard 0 of 1, so a probe would be in step too (one login per IP every P s, over the
  # per-IP limit): the burst profile stays one-phase.
  [ -n "$sign_in" ] || sign_in=$(( in_step ? 0 : ramp ))
  case "$sign_in" in ""|*[!0-9]*) die "--sign-in takes whole seconds" 2 ;; esac
  [ "$in_step" = "0" ] || [ "$sign_in" = "0" ] || die "--in-step runs one-phase: no --sign-in with it" 2
  [ -n "$probe_every" ] || probe_every=$(( sign_in > 0 ? 2 : 0 ))
  case "$probe_every" in ""|*[!0-9.]*) die "--login-probe-every takes seconds" 2 ;; esac
  # The login limit is 10 a minute per client IP. Spread the logins so each shard (IP) makes at most 8 a minute over the
  # window that carries them (the sign-in phase, or the ramp in a one-phase run).
  local logins_over=$(( sign_in > 0 ? sign_in : ramp ))
  if [ "$shards" = "0" ]; then shards=$(( (users * 60 + logins_over * 8 - 1) / (logins_over * 8) )); [ "$shards" -ge 1 ] || shards=1; fi
  [ -s "$STATE/load_accounts.csv" ] || cmd_seed "$users"
  local have; have="$(grep -vc '^#' "$STATE/load_accounts.csv" || true)"
  [ "$have" -ge "$users" ] || die "$STATE/load_accounts.csv has $have accounts, $users needed (deploy/ci/stack.sh seed $users)"
  case "$name" in ""|*/*|.*) die "--name must be a plain directory name" 2 ;; esac
  rm -rf "${STATE:?}/$name"; mkdir -p "$STATE/$name"
  local per=$(( (users + shards - 1) / shards )) i offset n pids=()
  local client_cpus; client_cpus="$(env_value ATP_CI_CLIENT_CPUS)"     # `stack.sh pin`: the clients off the server's CPUs
  step "load: $users users in $shards shards of up to $per (one client IP each), $([ "$sign_in" -gt 0 ] && echo "sign-in ${sign_in}s, ")ramp ${ramp}s, then ${seconds}s, think ${think}s$([ "$in_step" = 1 ] && echo ', shards in step (login bursts)')$([ "$probe_every" != 0 ] && echo ", a probe login every ${probe_every}s after the ramp")"
  # The runner's CPU and one bcrypt-12 check in the API's container with its own bcrypt, uncontended (before the load) -
  # the login p95 follows the core's bcrypt speed. Reported; the merge also derives the login budget from it
  # (loadtest.login_budget: scaled up on a slower core than the reference, never down). "unknown" when not measured.
  local cpu_model bcrypt_ms
  cpu_model="$(awk -F': *' '/^model name/ {print $2; exit}' /proc/cpuinfo 2>/dev/null || true)"
  bcrypt_ms="$(dc exec -T backend python - < "$ROOT/backend/scripts/bcrypt_baseline.py" 2>/dev/null | tail -n 1 || true)"
  case "$bcrypt_ms" in ""|*[!0-9]*) bcrypt_ms="unknown" ;; esac
  echo "    host: ${cpu_model:-unknown}, $(nproc) vCPU; bcrypt-12 check: $bcrypt_ms ms (uncontended, before the load)"
  scrape_metrics > "$STATE/$name/metrics-before.prom" || true
  pg_statements_reset || true
  sample_stats "$STATE/$name/docker-stats.csv" & local sampler=$!
  sample_db "$STATE/$name/db-connections.csv" & local db_sampler=$!
  # LD-7: one schedule for all shards - every ramp starts at the same instant (time for the containers to come up first)
  # and shard i takes the i/shards-th part of each start slot, so the logins arrive evenly over the ramp instead of one
  # per shard at the same moment every ramp/per seconds. --in-step keeps the shards in step on purpose (the burst
  # profile: one login per shard at the same instant, every ramp/per seconds - a market-open rush).
  local start_at=$(( $(date +%s) + 20 + shards ))
  for i in $(seq 0 $((shards - 1))); do
    offset=$((i * per)); n=$(( users - offset < per ? users - offset : per ))
    [ "$n" -gt 0 ] || break
    dc --profile tools run --rm -T ci-runner /ci-scripts/loadtest.py --base "https://$(env_value DOMAIN)" --cacert /state/caddy-root.crt \
      --accounts /state/load_accounts.csv --account-offset "$offset" --users "$n" --seconds "$seconds" --ramp "$ramp" \
      --think "$think" --seed "$((54 + i))" --shard "$((in_step ? 0 : i))" --shards "$((in_step ? 1 : shards))" --start-at "$start_at" \
      --sign-in "$sign_in" --login-probe-every "$probe_every" \
      ${client_cpus:+--pin-cpus "$client_cpus"} --raw-out "/state/$name/shard-$i.json" > "$STATE/$name/shard-$i.log" 2>&1 &
    pids+=("$!")
  done
  local failed=0 pid
  for pid in "${pids[@]}"; do wait "$pid" || failed=$((failed + 1)); done
  kill "$sampler" "$db_sampler" 2>/dev/null || true
  echo "    shards done ($failed reported an SLO miss or failed; the merged verdict below is the one that counts)"
  grep -h "after --start-at" "$STATE/$name"/shard-*.log 2>/dev/null | sed 's/^/    /' || true
  # LD-7: the database connections by state (pg_stat_activity, every 5 s) - peak and mean while the load ran
  awk -F, 'NR > 1 { if (!($1 in seen)) { seen[$1] = 1; samples++ } sum[$2] += $3; if ($3 > peak[$2]) peak[$2] = $3 }
           END { for (s in sum) printf "    db connections %-28s peak %3d  mean %5.1f\n", s, peak[s], sum[s] / samples }' \
    "$STATE/$name/db-connections.csv" 2>/dev/null | sort || true
  # LD-7: what the API measured itself (time per route, pool and bcrypt waits) and the statements by total time
  scrape_metrics > "$STATE/$name/metrics-after.prom" || true
  python3 "$SCRIPT_DIR/metrics_summary.py" "$STATE/$name/metrics-before.prom" "$STATE/$name/metrics-after.prom" \
    > "$STATE/$name/server-side.md" 2>&1 || true
  pg_statements > "$STATE/$name/pg-statements.md" 2>&1 || true
  sed 's/^/    /' "$STATE/$name/server-side.md" "$STATE/$name/pg-statements.md" 2>/dev/null || true
  local files=() f
  for f in "$STATE/$name"/shard-*.json; do [ -e "$f" ] && files+=("/state/$name/$(basename "$f")"); done
  [ "${#files[@]}" -gt 0 ] || die "no shard wrote its samples - see $STATE/$name/shard-*.log"
  local rc=0
  cmd_run /ci-scripts/loadtest.py --merge "${files[@]}" --json-out "/state/$name/result.json" --markdown "/state/$name/result.md" \
    --label "${label:-$PROJECT on $(hostname)}" --stats "/state/$name/docker-stats.csv" \
    --cpu-model "${cpu_model:-unknown}" --vcpus "$(nproc)" --bcrypt-ms "$bcrypt_ms" || rc=$?
  if [ -f "$STATE/$name/result.md" ]; then
    echo; cat "$STATE/$name/result.md"
    if [ -n "$report" ]; then cp "$STATE/$name/result.md" "$report"; fi
  fi
  return "$rc"
}

# LD-7 diagnostics. The API's /metrics (every process merged), read from inside its container with its own token.
scrape_metrics() {
  dc exec -T backend python -c 'import os, urllib.request
req = urllib.request.Request("http://127.0.0.1:8000/metrics", headers={"Authorization": "Bearer " + os.environ.get("METRICS_TOKEN", "")})
print(urllib.request.urlopen(req, timeout=30).read().decode())'
}

# pg_stat_statements (the CI overlay preloads it): emptied before a load run, the top statements by total time after.
pg_sql() {
  # shellcheck disable=SC2016  # expanded by the shell inside the postgres container, on purpose
  dc exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -q "$@"' psql "$@"
}
pg_statements_reset() {
  pg_sql -c "create extension if not exists pg_stat_statements" -c "select pg_stat_statements_reset()" > /dev/null
}
pg_statements() {
  echo "Statements by total time (pg_stat_statements, this run):"
  echo
  pg_sql -A -F ' | ' -c "select calls, round(total_exec_time::numeric / 1000, 2) as total_s,
      round(mean_exec_time::numeric, 2) as mean_ms, round(max_exec_time::numeric, 1) as max_ms, rows,
      round((100 * total_exec_time / nullif(sum(total_exec_time) over (), 0))::numeric, 1) as pct,
      left(regexp_replace(query, '\s+', ' ', 'g'), 160) as query
    from pg_stat_statements where dbid = (select oid from pg_database where datname = current_database())
    order by total_exec_time desc limit 15" -P footer=off
}

# CPU / memory of each container every 10 s while the load runs (what saturated, if anything).
sample_stats() {
  local out="$1"
  echo "time,container,cpu_pct,mem" > "$out"
  while :; do
    docker stats --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}}' 2>/dev/null \
      | grep "^${PROJECT}-" | sed "s/^/$(date -u +%H:%M:%S),/" >> "$out" || true
    sleep 10
  done
}

# The stack's database connections by state every 5 s (LD-7: is anything waiting on the pool, or holding a connection
# idle in a transaction?).
sample_db() {
  local out="$1"
  echo "time,state,count" > "$out"
  while :; do
    # shellcheck disable=SC2016  # expanded by the shell inside the postgres container, on purpose
    dc exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -AtF, -c "select coalesce(state, '"'"'-'"'"'), count(*) from pg_stat_activity where datname = current_database() and pid <> pg_backend_pid() group by 1"' 2>/dev/null \
      | sed "s/^/$(date -u +%H:%M:%S),/" >> "$out" || true
    sleep 5
  done
}

# Restart the API with N processes (API_WORKERS, backend/scripts/serve_api.sh; CI and Hostinger default 2), optionally
# capped at C CPUs (`--cpus C`: the Hostinger-shaped run gives the API the KVM 2's two vCPUs).
cmd_api_workers() {
  ensure_env
  local n="${1:?api-workers N [--cpus C]}" cpus=""
  [ "${2:-}" = "--cpus" ] && cpus="${3:?--cpus C}"
  case "$n" in *[!0-9]*|"") die "api-workers takes a number" 2 ;; esac
  case "$cpus" in *[!0-9.]*) die "--cpus takes a number" 2 ;; esac
  sed -i '/^API_WORKERS=/d' "$ENV_FILE"
  echo "API_WORKERS=$n" >> "$ENV_FILE"
  step "API with $n process(es)${cpus:+, at most $cpus CPUs}"
  dc up -d --wait --wait-timeout 300 backend
  if [ -n "$cpus" ]; then
    # Capped, then restarted: the processes size their bcrypt threads from the CPUs they may use at start-up
    # (app/auth/security.py reads the cgroup quota), as on a host with that many CPUs.
    docker update --cpus "$cpus" "$(dc ps -q backend)" > /dev/null
    docker restart "$(dc ps -q backend)" > /dev/null
    dc up -d --wait --wait-timeout 300 backend
  fi
}

# LD-7: the KVM 2 shape by CPU placement. `docker update --cpus` (a CFS quota) lets the API's threads spread over all the
# runner's CPUs and then stalls them for the rest of each 100 ms period once the quota is spent - a stall a VM with 2
# vCPUs does not have - while Postgres, the worker and Caddy ran on the other CPUs for free. Pinned, everything the
# owner's box runs shares SERVER's CPUs, and the load clients and the fake broker (elsewhere in production) use CLIENT.
SERVER_SERVICES="backend worker postgres redis caddy frontend"
# Two CPUs on two different physical cores for the server side, every other CPU for the clients: a VPS's 2 vCPUs are
# two host threads, while on an SMT runner (4 vCPUs = 2 cores x 2 threads) CPUs 0 and 1 can be one core's two threads.
kvm2_cpus() {
  local c key seen=" " server="" client="" n=0
  for c in $(seq 0 $(( $(nproc) - 1 ))); do
    key="$(cat "/sys/devices/system/cpu/cpu$c/topology/physical_package_id" 2>/dev/null || echo 0):$(cat "/sys/devices/system/cpu/cpu$c/topology/core_id" 2>/dev/null || echo "$c")"
    if [ "$n" -lt 2 ] && [ "${seen#* "$key" }" = "$seen" ]; then server="${server:+$server,}$c"; seen="$seen$key "; n=$((n + 1))
    else client="${client:+$client,}$c"; fi
  done
  echo "$server ${client:-$server}"
}

cmd_pin() {
  ensure_env
  local all server client svc id c
  all="0-$(( $(nproc) - 1 ))"
  if [ "${1:-}" = "kvm2" ]; then
    read -r server client < <(kvm2_cpus)
    echo "    CPU topology: $(lscpu 2>/dev/null | sed -n 's/^\(Model name\|Thread(s) per core\|Core(s) per socket\):[[:space:]]*/\1 /p' | paste -sd';' -)"
    for c in $(seq 0 $(( $(nproc) - 1 ))); do
      printf '    cpu%s: core %s, threads %s\n' "$c" "$(cat "/sys/devices/system/cpu/cpu$c/topology/core_id" 2>/dev/null)" \
        "$(cat "/sys/devices/system/cpu/cpu$c/topology/thread_siblings_list" 2>/dev/null)"
    done
  else
    server="${1:-$all}"; client="${2:-$all}"
  fi
  case "$server$client" in *[!0-9,-]*) die "pin takes CPU lists such as 0,1 and 2,3" 2 ;; esac
  step "server side (API, worker, Postgres, Redis, Caddy, frontend) on CPUs $server; load clients and fake broker on CPUs $client"
  for svc in $SERVER_SERVICES; do
    id="$(dc ps -q "$svc")"
    if [ -n "$id" ]; then docker update --cpuset-cpus "$server" "$id" > /dev/null; fi
  done
  id="$(dc ps -q fake-broker)"
  if [ -n "$id" ]; then docker update --cpuset-cpus "$client" "$id" > /dev/null; fi
  sed -i '/^ATP_CI_CLIENT_CPUS=/d' "$ENV_FILE"
  if [ "$client" != "$all" ]; then echo "ATP_CI_CLIENT_CPUS=$client" >> "$ENV_FILE"; fi
  # the API sizes its bcrypt threads from the CPUs it may run on at start-up (app/auth/security.py): restart it
  docker restart "$(dc ps -q backend)" > /dev/null
  dc up -d --wait --wait-timeout 300 backend
}

cmd_diag() {
  mkdir -p "$STATE/diag"
  dc ps -a > "$STATE/diag/ps.txt" 2>&1 || true
  local svc
  for svc in postgres redis fake-broker backend worker frontend caddy; do
    dc logs --no-color --tail 400 "$svc" > "$STATE/diag/$svc.log" 2>&1 || true
  done
  echo "diagnostics in $STATE/diag"
}

cmd_down() {
  [ -s "$ENV_FILE" ] || { echo "no stack state in $STATE"; return 0; }
  dc --profile tools --profile ci-disabled down -v --remove-orphans
}

main() {
  local cmd="${1:-help}"; [ $# -gt 0 ] && shift
  case "$cmd" in
    up) cmd_up ;;
    build) cmd_build ;;
    acceptance) cmd_acceptance "$@" ;;
    seed) cmd_seed "$@" ;;
    load) cmd_load "$@" ;;
    run) cmd_run "$@" ;;
    logs) ensure_env; dc logs --no-color --tail "${1:-200}" ;;
    ps) ensure_env; dc ps -a ;;
    diag) ensure_env; cmd_diag ;;
    api-workers) cmd_api_workers "$@" ;;
    pin) cmd_pin "$@" ;;
    down) cmd_down ;;
    env) ensure_env; grep -E '^(DOMAIN|ATP_CI_)' "$ENV_FILE" ;;
    help|-h|--help) sed -n '2,25p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' ;;
    *) die "unknown command $cmd (try: help)" 2 ;;
  esac
}

main "$@"
