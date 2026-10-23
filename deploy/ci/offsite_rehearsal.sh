#!/usr/bin/env bash
# A1 off-site restore rehearsal (readiness review R2: "a restore on a clean machine from R2 alone"), with the real
# deploy/atp.sh on a throwaway clone of this checkout - the same compose files, images and commands as the Hostinger
# host. R2 is stood in for by an S3 endpoint on the stack's own network: `rclone serve s3` in the offsite service's own
# rclone image, over a directory on the runner (the bucket).
#
#   1. atp.sh deploy N               R2 configured from the start (OFFSITE_* in .env, provider Rclone)
#   2. data through the API          a user signs up (an organisation with its own data key)
#   3. atp.sh backup --verify        encrypted, sealed dump + files archive -> the bucket -> fetched back -> restored into
#                                    a scratch database -> rows, schema, audit chain; then the bucket is checked: only
#                                    encrypted files, the weekly / monthly prefixes, no WAL archive
#   4. atp.sh health / backup-status the last good backup, local and off-site
#   5. the server is lost            `down -v` (database, backups volume), clone, files archive and .env deleted; only the
#                                    bucket and the escrowed values (runbook section 6a) are kept
#   6. a new clone + .env            built from the escrowed values only (a fresh METRICS_TOKEN: it is not escrowed)
#   7. atp.sh restore-offsite        the newest dump from the bucket, its seal checked, into the new EMPTY database
#   8. atp.sh deploy N               starts on it: deep health, the secrets key opens every organisation's data key
#                                    (health runs check_secrets_key.py), the user from step 2 logs in, audit chain intact
#
# Also set for the rehearsal only, as in rollback_rehearsal.sh: ATP_ALLOW_UNMERGED=1, ATP_ALLOW_SESSION_RESTART=1,
# ATP_ALLOW_UNTESTED=1 with a reason (A3: the commit under test has no CI result yet) and MIGRATION_FORCE=1 (CI may
# run in market hours on an empty database). Every value in .env is random and dies with $WORK.
# Needs: a non-root user in the docker group, docker compose >= 2.24, ports 80/443 and 127.0.0.1:8000/5432/6379 free.
# Writes $WORK/offsite_report.md (WORK: ATP_OFFSITE_REHEARSAL_DIR, default $RUNNER_TEMP/atp-offsite) and exits non-zero
# when a step fails. `--down` removes the stack afterwards. Nothing here reaches a real bucket or the deployed host.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$(cd "$SCRIPT_DIR/../.." && pwd)"
WORK="${ATP_OFFSITE_REHEARSAL_DIR:-${RUNNER_TEMP:-/tmp}/atp-offsite}"
CLONE="$WORK/atp"
BUCKET="$WORK/bucket"
REPORT="$WORK/offsite_report.md"
LOGS="$WORK/logs"
STUB="atp-s3stub"
DOWN=0
[ "${1:-}" = "--down" ] && DOWN=1
[ "$(id -u)" != "0" ] || { echo "run as a non-root user in the docker group (deploy/atp.sh refuses root)" >&2; exit 2; }

export ATP_DIR="$CLONE" ATP_ALLOW_UNMERGED=1 ATP_ALLOW_SESSION_RESTART=1 ATP_ALLOW_UNTESTED=1 ATP_UNTESTED_REASON="CI rehearsal of this commit" ATP_HEALTH_TIMEOUT="${ATP_HEALTH_TIMEOUT:-360}"
export ATP_FILES_BACKUP_DIR="$WORK/files"
API="http://127.0.0.1:8000"
DB="advance_trading_platform"
RESULTS=()
FAILED=0
# The values a real owner keeps in his password manager and one offline copy (runbook section 6a), nothing else.
ESCROW=(BACKUP_ENCRYPTION_PASSPHRASE SECRETS_ENCRYPTION_KEY JWT_SECRET_KEY POSTGRES_PASSWORD
        OFFSITE_REMOTE OFFSITE_S3_PROVIDER OFFSITE_S3_ENDPOINT OFFSITE_S3_ACCESS_KEY OFFSITE_S3_SECRET_KEY)

note() { printf '\n### %s\n' "$*"; }
pass() { RESULTS+=("| $1 | PASS | $2 |"); printf '    PASS  %s: %s\n' "$1" "$2"; }
fail() { RESULTS+=("| $1 | **FAIL** | $2 |"); printf '    FAIL  %s: %s\n' "$1" "$2"; FAILED=1; }

dcp() {
  (cd "$CLONE" && docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.hostinger.yml \
    -f deploy/compose.release.yml -p atp "$@")
}
login() {
  curl -s -o /dev/null -w '%{http_code}' -m 30 -H 'Content-Type: application/json' \
    -d "{\"email\":\"$1\",\"password\":\"Rehearsal-$PASS_SUFFIX!\"}" "$API/api/auth/$2"
}
rnd() { python3 -c 'import secrets; print(secrets.token_urlsafe(32))'; }

# atp.sh, logged (the same allowance as rollback_rehearsal.sh: a FAIL only about the runner's own listening ports).
ALLOWED_FAIL='public TCP ports besides 22/80/443'
atp() {
  local name="$1"; shift
  local log="$LOGS/$name.log" rc=0
  printf '    + deploy/atp.sh %s\n' "$*"
  "$CLONE/deploy/atp.sh" "$@" > "$log" 2>&1 || rc=$?
  local fails; fails="$(grep -E '^    FAIL  ' "$log" | grep -v "$ALLOWED_FAIL" || true)"
  if [ "$rc" = "1" ] && [ -z "$fails" ] && grep -q "$ALLOWED_FAIL" "$log" && ! grep -q '^FAILED' "$log"; then
    echo "      (exit 1 from the runner's own listening ports only)"
    rc=0
  fi
  echo "$rc" > "$LOGS/$name.rc"
  tail -n 4 "$log" | sed 's/^/      | /'
  return "$rc"
}

# The S3 stand-in: the offsite service's rclone image serving $BUCKET on the stack's network (compose creates it).
stub_up() {
  docker rm -f "$STUB" >/dev/null 2>&1 || true
  dcp run -d --no-deps --name "$STUB" -v "$BUCKET:/bucket" --entrypoint rclone offsite \
    serve s3 /bucket --addr :9000 --auth-key "$STUB_KEY,$STUB_SECRET" >/dev/null
  local _
  for _ in $(seq 1 30); do
    if dcp run --rm --no-deps -T --entrypoint rclone offsite lsd offsite: >/dev/null 2>&1; then return 0; fi
    sleep 2
  done
  echo "the S3 stand-in did not answer" >&2
  return 1
}

write_env() {   # write_env <file> <escrow file or ""> : the rehearsal .env, the secret values from escrow when given
  umask 077
  {
    echo "# rehearsal only - random values, deleted with $WORK"
    echo "ENVIRONMENT=production"
    if [ -n "$2" ]; then cat "$2"
    else
      for k in POSTGRES_PASSWORD JWT_SECRET_KEY BACKUP_ENCRYPTION_PASSPHRASE; do echo "$k=$(rnd)"; done
      echo "SECRETS_ENCRYPTION_KEY=$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
      echo "OFFSITE_REMOTE=offsite:atp-backups"; echo "OFFSITE_S3_PROVIDER=Rclone"; echo "OFFSITE_S3_ENDPOINT=http://$STUB:9000"
      echo "OFFSITE_S3_ACCESS_KEY=$STUB_KEY"; echo "OFFSITE_S3_SECRET_KEY=$STUB_SECRET"
    fi
    echo "METRICS_TOKEN=$(rnd)"
    echo "DOMAIN=localhost"; echo "ACME_EMAIL=ops@example.com"; echo "CADDYFILE=Caddyfile.ip"
    echo "OFFSITE_LOCAL_DIR=$WORK/offsite-local"; echo "OFFSITE_RESTORE_DIR=$WORK/restore"
    echo "MIGRATION_FORCE=1"; echo "WORKER_CYCLE_SECONDS=15"; echo "GLOBAL_CUES_ENABLED=false"
  } > "$1"
  chmod 600 "$1"
  umask 022
}

# Containers write some files as root into directories the runner owns: remove with sudo when the runner has it.
wipe() { rm -rf "$@" 2>/dev/null || sudo -n rm -rf "$@"; }

fresh_clone() {
  wipe "$CLONE"
  git clone -q --no-hardlinks "$SRC" "$CLONE"
  git -C "$CLONE" checkout -q --detach "$N"
}

main() {
  wipe "$WORK"; mkdir -p "$WORK" "$LOGS" "$WORK/files" "$WORK/offsite-local" "$WORK/restore" "$BUCKET/atp-backups"
  PASS_SUFFIX="$(python3 -c 'import secrets; print(secrets.token_hex(6))')"
  STUB_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(10))')"
  STUB_SECRET="$(rnd)"
  STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  N="$(git -C "$SRC" rev-parse HEAD)"

  note "throwaway clone of ${N:0:12}; the bucket is $BUCKET (served over S3 by $STUB)"
  fresh_clone
  write_env "$CLONE/.env" ""
  if stub_up; then pass "S3 stand-in" "rclone serve s3 on the stack's network, bucket atp-backups"; else fail "S3 stand-in" "did not start"; return; fi

  note "1. deploy N (R2 configured)"
  if atp deploy-1 deploy "$N"; then pass "deploy N" "${N:0:12} running"; else fail "deploy N" "exit $(cat "$LOGS/deploy-1.rc") - logs/deploy-1.log"; return; fi
  if [ "$(login before-loss@example.com register)" = "201" ]; then pass "data before the loss" "user before-loss@example.com signed up through the API"
  else fail "data before the loss" "sign-up failed"; fi

  note "3. backup --verify (to the bucket and back)"
  if atp backup backup --verify && grep -q 'R2 backup .* restored' "$LOGS/backup.log" && grep -q 'audit hash chain intact' "$LOGS/backup.log"; then
    pass "backup --verify" "$(grep -E 'R2 backup .* restored' "$LOGS/backup.log" | sed 's/^ *ok *//' | head -n 1)"
  else fail "backup --verify" "exit $(cat "$LOGS/backup.rc") - logs/backup.log"; fi
  local b="$BUCKET/atp-backups" plain wal
  plain="$(grep -rlE '^PGDMP' "$b" 2>/dev/null || true)"; wal="$(find "$b" -path '*wal_archive*' 2>/dev/null | head -n 1)"
  if ls "$b"/backups/atp_*.dump.enc.hmac >/dev/null 2>&1 && ls "$b"/files/atp_files_*.tar.gz.enc.hmac >/dev/null 2>&1 \
     && ls "$b"/backups-weekly/atp_*.dump.enc >/dev/null 2>&1 && ls "$b"/backups-monthly/atp_*.dump.enc >/dev/null 2>&1 \
     && [ -z "$plain" ] && [ -z "$wal" ] && [ -z "$(find "$b" -name '*.dump' -o -name 'LAST_*' | head -n 1)" ]; then
    pass "what reached the bucket" "$(find "$b" -type f | wc -l) files: sealed dumps (daily, weekly, monthly), the files archive; nothing in the clear, no WAL"
  else fail "what reached the bucket" "plain: ${plain:-none}, wal: ${wal:-none}; $(find "$b" -type f | sed "s#^$b/##" | tr '\n' ' ' | cut -c1-400)"; fi

  note "4. health and backup-status"
  if atp health-1 health && grep -q 'last off-site copy (R2)' "$LOGS/health-1.log"; then pass "health" "$(grep -E 'last off-site copy' "$LOGS/health-1.log" | sed 's/^ *ok *//')"
  else fail "health" "$(grep -E '^health:|FAIL' "$LOGS/health-1.log" | head -n 3 | tr '\n' ' ')"; fi
  if "$CLONE/deploy/atp.sh" backup-status > "$LOGS/backup-status.json" 2>"$LOGS/backup-status.err" \
     && python3 -c 'import json, sys; d = json.load(open(sys.argv[1])); assert d["status"] == "ok" and d["offsite"]["age_hours"] == 0' "$LOGS/backup-status.json"; then
    pass "backup-status" "$(cat "$LOGS/backup-status.json")"
  else fail "backup-status" "$(cat "$LOGS/backup-status.json" "$LOGS/backup-status.err" | head -c 400)"; fi

  note "5. the server is lost: only the bucket and the escrowed values are kept"
  local escrow="$WORK/escrow.env" k
  umask 077; : > "$escrow"
  for k in "${ESCROW[@]}"; do grep -E "^$k=" "$CLONE/.env" | tail -n 1 >> "$escrow"; done
  umask 022
  dcp down -v --remove-orphans > "$LOGS/down.log" 2>&1 || true
  docker rm -f "$STUB" >/dev/null 2>&1 || true
  wipe "$CLONE" "$WORK/files" "$WORK/restore"; mkdir -p "$WORK/files" "$WORK/restore"
  if [ -z "$(docker volume ls -q --filter name=atp_postgres_data)" ] && [ -z "$(docker volume ls -q --filter name=atp_backups)" ]; then
    pass "server lost" "database and backups volumes, clone, .env and files archive deleted; kept: the bucket + ${#ESCROW[@]} escrowed values"
  else fail "server lost" "a volume survived: $(docker volume ls -q --filter name=atp_ | tr '\n' ' ')"; fi

  note "6-7. a new clone, .env from escrow, restore-offsite"
  fresh_clone
  write_env "$CLONE/.env" "$escrow"
  rm -f "$escrow"
  if ! stub_up; then fail "S3 stand-in (again)" "did not start"; return; fi
  if ATP_CONFIRM_DB="$DB" atp restore restore-offsite && grep -q 'restore-offsite: done' "$LOGS/restore.log"; then
    pass "restore-offsite" "$(grep -E 'restored atp_' "$LOGS/restore.log" | sed 's/^ *ok *//' | head -n 1)"
  else fail "restore-offsite" "exit $(cat "$LOGS/restore.rc") - logs/restore.log"; return; fi

  note "8. deploy N on the restored database"
  if atp deploy-2 deploy "$N" && grep -q 'organisation data keys open' "$LOGS/deploy-2.log"; then
    pass "deploy after the restore" "deep health ok; $(grep -oE 'secrets key: [^|]*' "$LOGS/deploy-2.log" | tail -n 1)"
  else fail "deploy after the restore" "exit $(cat "$LOGS/deploy-2.rc") - logs/deploy-2.log"; fi
  if [ "$(login before-loss@example.com login)" = "200" ]; then pass "the user from before the loss" "logs in on the new server"
  else fail "the user from before the loss" "login failed"; fi
  if dcp exec -T backend python -m app.audit.verify_chain > "$LOGS/audit.log" 2>&1; then pass "audit chain on the new server" "intact"
  else fail "audit chain on the new server" "see logs/audit.log"; fi
}

write_report() {
  {
    echo "Off-site restore rehearsal with deploy/atp.sh - $STARTED to $(date -u +%Y-%m-%dT%H:%M:%SZ) on $(hostname)."
    echo "Release ${N:0:12}; the bucket: an S3 endpoint (rclone serve s3) over a runner directory; no real bucket, no deployed host."
    echo
    echo "| Step | Result | Detail |"; echo "|---|---|---|"
    printf '%s\n' "${RESULTS[@]}"
  } > "$REPORT"
}

cleanup() {
  if [ "$DOWN" = "1" ] && [ -d "$CLONE" ]; then dcp down -v --remove-orphans >/dev/null 2>&1 || true; fi
  docker rm -f "$STUB" >/dev/null 2>&1 || true
}
trap cleanup EXIT
N="" STARTED="" STUB_KEY="" STUB_SECRET="" PASS_SUFFIX=""
main || FAILED=1
write_report
echo; cat "$REPORT"
[ "$FAILED" = "0" ] && [ "${#RESULTS[@]}" -ge 11 ]
