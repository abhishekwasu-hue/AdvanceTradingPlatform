#!/bin/sh
# The API container's command (backend/Dockerfile CMD; the CI overlay runs it too): apply pending migrations, then serve.
#
# LD-7: API_WORKERS uvicorn processes (default 1). Everything shared between requests is in Postgres or Redis (rate
# limits, the worker replica lock, the Phase 0 lock - read per cycle -, sessions, caches with a Redis copy); what each
# process keeps for itself is a TTL'd copy or a key ring (docs/PERFORMANCE.md "API processes"). With more than one
# process the Prometheus series go to a shared directory, emptied here, and /metrics merges them.
# --proxy-headers: behind Caddy (prod) the client IP comes from X-Forwarded-For, but only from the proxies named in
# FORWARDED_ALLOW_IPS (default 127.0.0.1 - never from a container peer unless the overlay says so) - P0.2 / S1.
set -eu

workers="${API_WORKERS:-1}"
case "$workers" in
    ''|*[!0-9]*) echo "serve_api: API_WORKERS must be a whole number, got '$workers'" >&2; exit 2 ;;
esac
[ "$workers" -ge 1 ] || workers=1

python scripts/migrate_guard.py

if [ "$workers" -gt 1 ]; then
    PROMETHEUS_MULTIPROC_DIR="${PROMETHEUS_MULTIPROC_DIR:-/tmp/atp-prometheus}"
    export PROMETHEUS_MULTIPROC_DIR
    rm -rf "${PROMETHEUS_MULTIPROC_DIR:?}"
    mkdir -p "$PROMETHEUS_MULTIPROC_DIR"
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers \
    --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}" --workers "$workers"
