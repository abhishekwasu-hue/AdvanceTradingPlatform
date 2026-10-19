#!/usr/bin/env bash
# The 500-user load test in one command, for the owner on the Hostinger host (docs/PERFORMANCE.md, runbook section 12):
#
#   /opt/atp/deploy/ci/loadtest_500.sh                 500 users, think 30 s, 5 minutes after a 150 s login ramp
#   /opt/atp/deploy/ci/loadtest_500.sh --think 10      the busier profile CI also runs
#
# It brings up the CI staging stack (deploy/ci/stack.sh) as its own compose project `atp-ci` beside production: the
# release's images, a throwaway database and env file, a fake broker, no public port (127.0.0.1:28000 / :28443 only).
# It never touches the production project, its .env, its database or its flags. Then: 500 synthetic users -> the
# sharded load test (25 client IPs, so the per-IP login limit applies as for real users) -> the report -> the stack and
# its volumes are removed (--keep leaves it up). Run it as the `atp` user, when no beta user is on (it shares the CPU
# with production) - before the beta opens, or after the close.
#
# The report: $ATP_CI_STATE/load-hostinger.md (default ~/atp-loadtest/). Paste it into docs/reports/load_test_500.md.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export ATP_CI_STATE="${ATP_CI_STATE:-$HOME/atp-loadtest}"
export ATP_CI_CLOCK=real
users=500 think=30 seconds=300 keep=0
while [ $# -gt 0 ]; do
  case "$1" in
    --users) users="$2"; shift 2 ;; --think) think="$2"; shift 2 ;; --seconds) seconds="$2"; shift 2 ;;
    --keep) keep=1; shift ;;
    *) echo "usage: $0 [--users N] [--think S] [--seconds S] [--keep]" >&2; exit 2 ;;
  esac
done

[ "$(id -u)" != "0" ] || { echo "run as the deploy user (atp), not root" >&2; exit 2; }
[ "$keep" = "1" ] || trap '"$SCRIPT_DIR/stack.sh" down >/dev/null 2>&1 || true' EXIT

# LD-7: the API as production runs it on this host (docker-compose.hostinger.yml: 2 processes on the KVM 2), not the CI
# runner's 3 - the shell's value wins over the stack's env file.
export API_WORKERS="${API_WORKERS:-2}"
"$SCRIPT_DIR/stack.sh" up
"$SCRIPT_DIR/stack.sh" seed "$users"
host="$(hostname) ($(nproc) vCPU, $(free -g | awk '/Mem/ {print $2}') GB)"
rc=0
"$SCRIPT_DIR/stack.sh" load --name load-hostinger --users "$users" --seconds "$seconds" --ramp 150 --think "$think" \
  --label "$host, think ${think} s" --report "$ATP_CI_STATE/load-hostinger.md" || rc=$?
echo
echo "report: $ATP_CI_STATE/load-hostinger.md"
exit "$rc"
