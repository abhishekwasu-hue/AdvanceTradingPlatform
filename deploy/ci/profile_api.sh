#!/usr/bin/env bash
# LD-7 diagnostics: sample the API processes of the CI stack with py-spy while a load test runs, and print where the
# time goes (deploy/ci/profile_summary.py). CI only - it needs root (ptrace of the container's processes) and changes
# nothing in the stack.
#
#   deploy/ci/profile_api.sh DELAY_SECONDS DURATION_SECONDS OUT_PREFIX
set -Eeuo pipefail

delay="${1:?delay}" duration="${2:?duration}" out="${3:?out prefix}"
project="${ATP_CI_PROJECT:-atp-ci}"
sleep "$delay"
pid="$(docker top "${project}-backend-1" -o pid,args | awk '/uvicorn app.main:app/ {print $1; exit}')"
[ -n "$pid" ] || { echo "profile_api: no uvicorn process found" >&2; exit 1; }
# shellcheck disable=SC2024  # the log is the runner user's own file, on purpose
sudo "$(command -v py-spy)" record --pid "$pid" --subprocesses --nonblocking --rate 50 --duration "$duration" --format raw \
  --output "$out.raw" > "$out.log" 2>&1 || true
sudo chown "$(id -u):$(id -g)" "$out.raw" 2>/dev/null || true
python3 "$(dirname "$0")/profile_summary.py" "$out.raw" > "$out.txt" 2>&1 || true
