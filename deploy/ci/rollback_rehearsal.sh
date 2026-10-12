#!/usr/bin/env bash
# Rollback rehearsal (Phase 0 exit criterion "rollback rehearsed"), with the real deploy/atp.sh on a throwaway clone
# of this checkout - the same compose files, images and commands the owner runs on the Hostinger host:
#
#   1. atp.sh deploy N            release N (this commit) on an empty database; data written through the API
#   2. atp.sh deploy N+1          N plus a ROLLBACK_SAFE migration (a new table): pre-migration backup, migrate, data
#   3. atp.sh rollback            code-only: N runs again on the database N+1 left (allowed: every migration in between
#                                 is ROLLBACK_SAFE) - health, logins of users made before and during N+1, audit chain
#   4. atp.sh deploy N+2          N+1 plus a migration declared NOT rollback-safe
#   5. atp.sh rollback            must be refused (exit 1): N cannot run on that schema; N+2 keeps running
#   6. atp.sh rollback --restore-db
#                                 the database goes back to the backup taken just before N+2 migrated; N runs on it;
#                                 the rows written under N+2 are only in the kept copy (<db>_pre_rollback_*)
#
# N+1 and N+2 are local commits in the clone (never pushed), so the run sets ATP_ALLOW_UNMERGED=1. Also set for the
# rehearsal only: ATP_ALLOW_SESSION_RESTART=1 and MIGRATION_FORCE=1 (CI may run during market hours on an empty
# database), ATP_CONFIRM_DB (the confirmation --restore-db asks for). The clone's .env is generated with random values.
#
# Needs: a non-root user in the docker group (atp.sh refuses root), docker compose >= 2.24, ports 80/443 and
# 127.0.0.1:8000/5432/6379 free. Writes the report to $WORK/rollback_report.md (WORK: ATP_REHEARSAL_DIR, default
# $RUNNER_TEMP/atp-rehearsal) and exits non-zero when a step fails. `--down` removes the stack afterwards.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$(cd "$SCRIPT_DIR/../.." && pwd)"
WORK="${ATP_REHEARSAL_DIR:-${RUNNER_TEMP:-/tmp}/atp-rehearsal}"
CLONE="$WORK/atp"
REPORT="$WORK/rollback_report.md"
LOGS="$WORK/logs"
DOWN=0
[ "${1:-}" = "--down" ] && DOWN=1
[ "$(id -u)" != "0" ] || { echo "run as a non-root user in the docker group (deploy/atp.sh refuses root)" >&2; exit 2; }

export ATP_DIR="$CLONE" ATP_ALLOW_UNMERGED=1 ATP_ALLOW_SESSION_RESTART=1 ATP_HEALTH_TIMEOUT="${ATP_HEALTH_TIMEOUT:-360}"
export ATP_FILES_BACKUP_DIR="$WORK/files"
API="http://127.0.0.1:8000"
RESULTS=()
FAILED=0

note() { printf '\n### %s\n' "$*"; }
pass() { RESULTS+=("| $1 | PASS | $2 |"); printf '    PASS  %s: %s\n' "$1" "$2"; }
fail() { RESULTS+=("| $1 | **FAIL** | $2 |"); printf '    FAIL  %s: %s\n' "$1" "$2"; FAILED=1; }

dcp() {
  (cd "$CLONE" && docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.hostinger.yml \
    -f deploy/compose.release.yml -p atp "$@")
}
sql() { dcp exec -T postgres psql -X -A -t -q -v ON_ERROR_STOP=1 -U atp_user -d advance_trading_platform -c "$1"; }
current() { cat "$CLONE/.deploy/current" 2>/dev/null | cut -c1-12; }
revision() { sql "select string_agg(version_num, ',') from alembic_version"; }
register() {   # email -> HTTP status of a self sign-up through the API
  curl -s -o /dev/null -w '%{http_code}' -m 30 -H 'Content-Type: application/json' \
    -d "{\"email\":\"$1\",\"password\":\"Rehearsal-$PASS_SUFFIX!\"}" "$API/api/auth/register"
}
login() {
  curl -s -o /dev/null -w '%{http_code}' -m 30 -H 'Content-Type: application/json' \
    -d "{\"email\":\"$1\",\"password\":\"Rehearsal-$PASS_SUFFIX!\"}" "$API/api/auth/login"
}
deep() { curl -s -m 10 "$API/api/system/health/deep" | python3 -c 'import json, sys; d = json.load(sys.stdin); print(d.get("status"), (d.get("checks") or {}).get("migrations", {}).get("status"))' 2>/dev/null || echo "unreachable"; }

# atp.sh, logged. The rehearsal host is a CI runner: a FAIL that is only about the host (public ports other than
# 22/80/443 that the runner image itself listens on) is not about the release, so it is reported and allowed.
ALLOWED_FAIL='public TCP ports besides 22/80/443'
atp() {
  local name="$1"; shift
  local log="$LOGS/$name.log" rc=0
  printf '    + deploy/atp.sh %s\n' "$*"
  "$CLONE/deploy/atp.sh" "$@" > "$log" 2>&1 || rc=$?
  local fails; fails="$(grep -E '^    FAIL  ' "$log" | grep -v "$ALLOWED_FAIL" || true)"
  if [ "$rc" = "1" ] && [ -z "$fails" ] && grep -q "$ALLOWED_FAIL" "$log" && ! grep -q '^FAILED' "$log"; then
    echo "      (exit 1 from the runner's own listening ports only: $(grep "$ALLOWED_FAIL" "$log" | head -n 1 | sed 's/^ *//'))"
    rc=0
  fi
  echo "$rc" > "$LOGS/$name.rc"
  tail -n 4 "$log" | sed 's/^/      | /'
  return "$rc"
}

head_of() {   # the alembic head of a checkout's migrations (the revision nothing revises)
  python3 - "$1/backend/alembic/versions" <<'EOF'
import re, sys
from pathlib import Path
revs, downs = set(), set()
for p in Path(sys.argv[1]).glob("*.py"):
    t = p.read_text()
    m = re.search(r"^revision(?:: str)?\s*=\s*['\"]([^'\"]+)['\"]", t, re.M)
    if m:
        revs.add(m.group(1))
    d = re.search(r"^down_revision[^=]*=\s*(.+)$", t, re.M)
    if d:
        downs.update(re.findall(r"['\"]([0-9a-zA-Z_]+)['\"]", d.group(1)))
heads = sorted(revs - downs)
assert len(heads) == 1, heads
print(heads[0])
EOF
}

add_migration() {   # file revision down_revision safe(True|False) upgrade-body
  cat > "$CLONE/backend/alembic/versions/$1" <<EOF
"""CI rollback rehearsal only (deploy/ci/rollback_rehearsal.sh): a local commit in a throwaway clone, never pushed.

Revision ID: $2
Revises: $3
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '$2'
down_revision: Union[str, Sequence[str], None] = '$3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = $4     # rehearsal: $( [ "$4" = True ] && echo "additive - a new table the previous release never reads" || echo "declared unsafe on purpose: a code-only rollback past it must be refused" )


def upgrade() -> None:
$5


def downgrade() -> None:
    raise NotImplementedError("deploy/atp.sh never downgrades")
EOF
}

main() {
  rm -rf "$WORK"; mkdir -p "$WORK" "$LOGS" "$WORK/files" "$WORK/offsite" "$WORK/restore"
  PASS_SUFFIX="$(python3 -c 'import secrets; print(secrets.token_hex(6))')"
  STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  note "throwaway clone of $(git -C "$SRC" rev-parse --short=12 HEAD), releases N, N+1, N+2"
  git clone -q --no-hardlinks "$SRC" "$CLONE"
  git -C "$CLONE" checkout -q --detach "$(git -C "$SRC" rev-parse HEAD)"
  local head0
  N="$(git -C "$CLONE" rev-parse HEAD)"
  head0="$(head_of "$CLONE")"
  git -C "$CLONE" config user.email "ci-rehearsal@example.com"; git -C "$CLONE" config user.name "CI rehearsal"
  add_migration "ci0rehearsal1_marker_table.py" ci0rehearsal1 "$head0" True \
"    op.create_table('ci_rehearsal_marker', sa.Column('id', sa.Integer(), primary_key=True),
                    sa.Column('note', sa.String(100), nullable=False))"
  git -C "$CLONE" add -A && git -C "$CLONE" commit -q -m "rehearsal N+1: a ROLLBACK_SAFE migration"
  N1="$(git -C "$CLONE" rev-parse HEAD)"
  add_migration "ci0rehearsal2_not_safe.py" ci0rehearsal2 ci0rehearsal1 False \
"    op.add_column('ci_rehearsal_marker', sa.Column('flag', sa.Integer(), nullable=False, server_default='0'))"
  git -C "$CLONE" add -A && git -C "$CLONE" commit -q -m "rehearsal N+2: a migration declared not rollback-safe"
  N2="$(git -C "$CLONE" rev-parse HEAD)"
  git -C "$CLONE" checkout -q --detach "$N"
  echo "    N   ${N:0:12} (head $head0)"; echo "    N+1 ${N1:0:12} (ci0rehearsal1, ROLLBACK_SAFE = True)"; echo "    N+2 ${N2:0:12} (ci0rehearsal2, ROLLBACK_SAFE = False)"

  umask 077
  {
    echo "# rehearsal only - random values, deleted with $WORK"
    echo "ENVIRONMENT=production"
    for k in POSTGRES_PASSWORD JWT_SECRET_KEY METRICS_TOKEN BACKUP_ENCRYPTION_PASSPHRASE; do echo "$k=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"; done
    echo "SECRETS_ENCRYPTION_KEY=$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
    echo "DOMAIN=localhost"; echo "ACME_EMAIL=ops@example.com"; echo "CADDYFILE=Caddyfile.ip"
    echo "OFFSITE_REMOTE=/offsite_local"; echo "OFFSITE_LOCAL_DIR=$WORK/offsite"; echo "OFFSITE_RESTORE_DIR=$WORK/restore"
    echo "MIGRATION_FORCE=1"; echo "WORKER_CYCLE_SECONDS=15"; echo "GLOBAL_CUES_ENABLED=false"
  } > "$CLONE/.env"
  chmod 600 "$CLONE/.env"
  umask 022

  note "1. deploy N"
  local t0; t0=$(date +%s)
  if atp deploy-n deploy "$N"; then pass "deploy N" "${N:0:12} running, schema $(revision), deep health $(deep) ($(( $(date +%s) - t0 )) s)"
  else fail "deploy N" "exit $(cat "$LOGS/deploy-n.rc") - logs/deploy-n.log"; return; fi
  if [ "$(register before@example.com)" = "201" ]; then pass "data under N" "user before@example.com signed up through the API"; else fail "data under N" "sign-up failed"; fi

  note "2. deploy N+1 (ROLLBACK_SAFE migration)"
  t0=$(date +%s)
  if atp deploy-n1 deploy "$N1"; then pass "deploy N+1" "${N1:0:12} running, schema $(revision), pre-migration backup $(awk '$3 ~ /^'"$N1"'/ {print $5}' "$CLONE/.deploy/releases.log" | tail -n 1) ($(( $(date +%s) - t0 )) s)"
  else fail "deploy N+1" "exit $(cat "$LOGS/deploy-n1.rc")"; return; fi
  sql "insert into ci_rehearsal_marker (note) values ('written under N+1')" >/dev/null
  if [ "$(register during-n1@example.com)" = "201" ]; then pass "data under N+1" "a ci_rehearsal_marker row + user during-n1@example.com"; else fail "data under N+1" "sign-up failed"; fi

  note "3. rollback (code-only; every migration in between is ROLLBACK_SAFE)"
  t0=$(date +%s)
  if atp rollback-1 rollback && [ "$(current)" = "${N:0:12}" ]; then
    local rows; rows="$(sql "select count(*) from ci_rehearsal_marker")"
    if [ "$(login before@example.com)" = "200" ] && [ "$(login during-n1@example.com)" = "200" ] && [ "$rows" = "1" ]; then
      pass "rollback, code-only" "N running on schema $(revision) (ahead, allowed), deep health $(deep); both users log in; marker rows $rows ($(( $(date +%s) - t0 )) s)"
    else fail "rollback, code-only" "data check: logins $(login before@example.com)/$(login during-n1@example.com), marker rows $rows"; fi
  else fail "rollback, code-only" "exit $(cat "$LOGS/rollback-1.rc"), running $(current)"; return; fi
  if dcp exec -T backend python -m app.audit.verify_chain > "$LOGS/audit-1.log" 2>&1; then pass "audit chain after rollback" "intact"
  else fail "audit chain after rollback" "see logs/audit-1.log"; fi

  note "4. deploy N+2 (migration NOT rollback-safe)"
  t0=$(date +%s)
  if atp deploy-n2 deploy "$N2"; then pass "deploy N+2" "${N2:0:12} running, schema $(revision) ($(( $(date +%s) - t0 )) s)"
  else fail "deploy N+2" "exit $(cat "$LOGS/deploy-n2.rc")"; return; fi
  sql "insert into ci_rehearsal_marker (note, flag) values ('written under N+2', 1)" >/dev/null
  if [ "$(register during-n2@example.com)" = "201" ]; then pass "data under N+2" "a marker row + user during-n2@example.com"; else fail "data under N+2" "sign-up failed"; fi

  note "5. rollback (code-only) must be refused"
  local rc=0
  atp rollback-refused rollback || rc=$?
  if [ "$rc" = "1" ] && grep -q "code-only rollback refused" "$LOGS/rollback-refused.log" && [ "$(current)" = "${N2:0:12}" ] && [ "$(deep)" = "ok ok" ]; then
    pass "code-only rollback past an unsafe migration" "refused (exit 1); N+2 still running, deep health ok"
  else fail "code-only rollback past an unsafe migration" "exit $rc, running $(current), deep $(deep)"; fi

  note "6. rollback --restore-db"
  t0=$(date +%s)
  if ATP_CONFIRM_DB=advance_trading_platform ATP_CONFIRM_ORPHANS=ORPHAN atp rollback-restore rollback --restore-db && [ "$(current)" = "${N:0:12}" ]; then
    local live kept kept_n2
    live="$(sql "select string_agg(note, '|' order by id) from ci_rehearsal_marker")"
    kept="$(sql "select datname from pg_database where datname like 'advance_trading_platform_pre_rollback_%' order by datname desc limit 1")"
    kept_n2="$(dcp exec -T postgres psql -X -A -t -q -U atp_user -d "$kept" -c "select count(*) from users where email = 'during-n2@example.com'" 2>/dev/null || echo "?")"
    if [ "$(revision)" = "ci0rehearsal1" ] && [ "$live" = "written under N+1" ] && [ "$(login before@example.com)" = "200" ] \
       && [ "$(login during-n1@example.com)" = "200" ] && [ "$(login during-n2@example.com)" = "401" ] && [ "$kept_n2" = "1" ]; then
      pass "rollback --restore-db" "N running; database back at ci0rehearsal1 (the pre-migration backup); rows of N and N+1 present, N+2's only in $kept; deep health $(deep) ($(( $(date +%s) - t0 )) s)"
    else fail "rollback --restore-db" "schema $(revision), marker '$live', logins $(login before@example.com)/$(login during-n1@example.com)/$(login during-n2@example.com), kept copy $kept has during-n2: $kept_n2"; fi
  else fail "rollback --restore-db" "exit $(cat "$LOGS/rollback-restore.rc"), running $(current)"; fi
  if dcp exec -T backend python -m app.audit.verify_chain > "$LOGS/audit-2.log" 2>&1; then pass "audit chain after restore" "intact"
  else fail "audit chain after restore" "see logs/audit-2.log"; fi
  if atp health health; then pass "atp.sh health" "$(grep -E '^health:' "$LOGS/health.log" | tail -n 1)"
  else fail "atp.sh health" "$(grep -E '^health:|FAIL' "$LOGS/health.log" | head -n 3 | tr '\n' ' ')"; fi
}

write_report() {
  "$CLONE/deploy/atp.sh" releases > "$LOGS/releases.log" 2>&1 || true
  {
    echo "Rollback rehearsal with deploy/atp.sh - $STARTED to $(date -u +%Y-%m-%dT%H:%M:%SZ) on $(hostname) ($(nproc) vCPU)."
    echo "N = ${N:0:12}, N+1 = ${N1:0:12} (ROLLBACK_SAFE migration), N+2 = ${N2:0:12} (migration declared not rollback-safe)."
    echo
    echo "| Step | Result | Detail |"; echo "|---|---|---|"
    printf '%s\n' "${RESULTS[@]}"
    echo; echo '```'; cat "$LOGS/releases.log"; echo '```'
  } > "$REPORT"
}

trap '[ "$DOWN" = "1" ] && dcp down -v --remove-orphans >/dev/null 2>&1 || true' EXIT
N="" N1="" N2="" STARTED=""
main || FAILED=1
write_report
echo; cat "$REPORT"
[ "$FAILED" = "0" ] && [ "${#RESULTS[@]}" -ge 10 ]
