#!/usr/bin/env sh
# Phase P5 / section 51: one-command rolling deploy for a compose host.
#
#   scripts/deploy.sh staging|production [git ref]
#
# 1. checks out the ref (default: current HEAD) and builds the images,
# 2. runs the migration-hour guard in a one-off backend container (refuses schema changes while
#    the NSE session is open unless MIGRATION_FORCE=1 is exported),
# 3. restarts the API first and waits for /api/system/health/deep, then the worker, then the
#    frontend - the previous containers keep serving until the new one is healthy (rolling, not
#    blue-green: one host, one database; the worker is a single replica by design),
# 4. prints the versions running, or - when the new API never becomes healthy - re-tags the previous
#    backend image as the service image, restarts the API from it and waits for it to be healthy again
#    (P0.7: the rollback used to re-tag only, leaving the broken container running). The schema is not
#    rolled back: every migration since Phase N is additive and the previous image reads it.
#
# Environment: DEPLOY_TIMEOUT (seconds to wait for health, default 120). Run from the repo root.
set -eu
ENV_NAME="${1:-staging}"; REF="${2:-}"
case "$ENV_NAME" in
  staging)    FILES="-f docker-compose.yml -f docker-compose.staging.yml"; PROJECT="atp-staging"; API="http://localhost:18000" ;;
  production) FILES="-f docker-compose.yml -f docker-compose.prod.yml"; PROJECT="atp"; API="http://localhost:8000" ;;   # Phase AX: Caddy + off-site copy
  *) echo "usage: scripts/deploy.sh staging|production [git ref]" >&2; exit 2 ;;
esac
COMPOSE="docker compose $FILES -p $PROJECT"
TIMEOUT="${DEPLOY_TIMEOUT:-120}"
log() { printf '%s deploy[%s]: %s\n' "$(date -u +%H:%M:%S)" "$ENV_NAME" "$*"; }

# P0.1: fetch everything, then check the ref out detached - `origin/main`, a branch, a tag or a SHA all work
# (`git fetch origin origin/main` does not exist as a remote ref, and `checkout main` kept a stale local branch).
if [ -n "$REF" ]; then log "checking out $REF"; git fetch -q origin && git checkout -q --detach "$REF"; fi
# P0.7: `images -q` prints the image id the running container uses (`--format` takes only table|json, so the
# earlier `--format '{{.ID}}'` failed silently and the rollback never had an image to go back to).
PREVIOUS="$($COMPOSE images -q backend 2>/dev/null | head -n1 || true)"
# P0.7: keep the image the API runs now under a stable tag; `build` re-points `${PROJECT}-backend:latest`.
IMAGE="${PROJECT}-backend"
if [ -n "$PREVIOUS" ]; then docker tag "$PREVIOUS" "$IMAGE:previous" >/dev/null 2>&1 || PREVIOUS=""; fi
log "building images at $(git rev-parse --short HEAD)"
$COMPOSE build --pull backend worker frontend   # P0.7: the worker has its own build and was never rebuilt here
$COMPOSE up -d postgres redis
log "running the migration guard"
if ! $COMPOSE run --rm --no-deps backend python scripts/migrate_guard.py; then
  echo "deploy: migration guard refused (market open?) - nothing was restarted. Retry after 15:30 IST or export MIGRATION_FORCE=1." >&2
  exit 3
fi

wait_healthy() {
  i=0
  until curl -sf "$API/api/system/health/deep" >/dev/null 2>&1; do
    i=$((i+1)); [ "$i" -ge "$TIMEOUT" ] && return 1; sleep 1
  done
}

rollback() {
  echo "deploy: new API never became healthy" >&2
  $COMPOSE logs --tail=50 backend >&2 || true
  if [ -z "$PREVIOUS" ]; then
    echo "deploy: no previous backend image to roll back to - the new container stays up for inspection" >&2
    exit 4
  fi
  # P0.7: restart the API from the previous image. The worker and the frontend were not touched yet (they
  # restart only after the API is healthy), so only the backend service is recreated; `--no-build` keeps compose
  # from rebuilding the broken image, and the retag makes `$IMAGE:latest` the previous image again.
  log "rolling back API to image $PREVIOUS"
  docker tag "$IMAGE:previous" "$IMAGE:latest" >/dev/null 2>&1 || true
  $COMPOSE up -d --no-deps --no-build backend || true
  if wait_healthy; then
    log "rollback complete: API healthy on $PREVIOUS; the new image was not promoted (worker/frontend unchanged)"
  else
    echo "deploy: rollback API did not become healthy either - check '$COMPOSE logs backend' and the database" >&2
    $COMPOSE logs --tail=50 backend >&2 || true
  fi
  exit 4
}

log "restarting API"
$COMPOSE up -d --no-deps backend
wait_healthy || rollback
log "API healthy; restarting worker and frontend"
$COMPOSE up -d --no-deps worker frontend
log "done: $($COMPOSE ps --format '{{.Service}} {{.Status}}' | tr '\n' ';')"
curl -s "$API/api/system/status" | head -c 300; echo
