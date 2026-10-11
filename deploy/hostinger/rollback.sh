#!/usr/bin/env bash
# Block 3 of 3 - rollback to the commit that ran before the last deploy (or to a ref you name), as the non-root user:
#
#   bash /opt/atp/deploy/hostinger/rollback.sh            # the previous deployed commit (.deploy/history)
#   bash /opt/atp/deploy/hostinger/rollback.sh <sha|tag>  # a specific one
#
# Same rolling path as a deploy (build at that commit, migration guard, API first and only then the worker): the
# schema is NOT rolled back - every migration is additive and an older image reads the newer schema (ADR in
# scripts/deploy.sh). Positions are untouched; the worker restarts and reconciles. Prefer outside market hours.
set -euo pipefail
ATP_DIR="${ATP_DIR:-/opt/atp}"
cd "$ATP_DIR"
TARGET="${1:-}"
if [ -z "$TARGET" ]; then
  [ -s .deploy/history ] || { echo "no previous deploy recorded in $ATP_DIR/.deploy/history - name a commit: rollback.sh <sha>" >&2; exit 2; }
  TARGET="$(tail -n1 .deploy/history | awk '{print $2}')"
fi
CURRENT="$(cat .deploy/current 2>/dev/null || git rev-parse HEAD)"
echo "rolling back $CURRENT -> $TARGET"
git fetch -q origin
git cat-file -e "$TARGET^{commit}" || { echo "unknown commit $TARGET" >&2; exit 2; }
COMPOSE_OVERLAYS="-f docker-compose.hostinger.yml" sh scripts/deploy.sh production "$TARGET"
docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.hostinger.yml -p atp up -d
mkdir -p .deploy
echo "$(date -Iseconds) $CURRENT rolled-back-from" >> .deploy/rollbacks
sed -i '$d' .deploy/history 2>/dev/null || true
git rev-parse HEAD > .deploy/current
echo "now running $(git rev-parse --short HEAD) - check: bash $ATP_DIR/deploy/hostinger/status.sh"
