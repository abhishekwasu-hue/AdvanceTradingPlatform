#!/usr/bin/env bash
# Block 2 of 3 - deploy (first install and every later update). Run as the non-root user from block 1:
#
#   ssh atp@<server-ip>
#   bash -c "$(echo <base64 from docs/DEPLOY_HOSTINGER_MR.md> | base64 -d)"      # or, once cloned: bash /opt/atp/deploy/hostinger/deploy.sh
#
# Stops (exit 10) and tells you what to do at the two hand-made steps; run it again after each:
#   1. the read-only deploy key: printed here, you add it at GitHub > repo > Settings > Deploy keys (no write access);
#   2. /opt/atp/.env: created from .env.example with CHANGE_ME placeholders and mode 600 - you fill the secrets on
#      the server (never in chat); the run continues only when no CHANGE_ME is left.
# Then: migrations (alembic upgrade head, with the market-hours guard), services up, Caddy (a domain with Let's
# Encrypt, or the server IP with an internal certificate), health checks, first_paper_day_check.py.
#
#   ATP_DIR     install dir (default /opt/atp)          ATP_REF   git ref to deploy (default origin/main)
#   ATP_REPO    git@github.com:abhishekwasu-hue/AdvanceTradingPlatform.git
set -euo pipefail

ATP_DIR="${ATP_DIR:-/opt/atp}"
ATP_REF="${ATP_REF:-origin/main}"
ATP_REPO="${ATP_REPO:-git@github.com:abhishekwasu-hue/AdvanceTradingPlatform.git}"
KEY="$HOME/.ssh/atp_deploy_ed25519"
log() { printf '\n==> %s\n' "$*"; }
[ "$(id -u)" != "0" ] || { echo "run as the non-root user from block 1, not root" >&2; exit 1; }
command -v docker >/dev/null || { echo "docker missing - run block 1 (bootstrap) first" >&2; exit 1; }

log "deploy key (read-only)"
if [ ! -f "$KEY" ]; then
  ssh-keygen -q -t ed25519 -N "" -C "atp-deploy@$(hostname)" -f "$KEY"
fi
mkdir -p "$HOME/.ssh"; chmod 700 "$HOME/.ssh"
grep -q "Host github.com" "$HOME/.ssh/config" 2>/dev/null || cat >> "$HOME/.ssh/config" <<CFG
Host github.com
  IdentityFile $KEY
  IdentitiesOnly yes
CFG
chmod 600 "$HOME/.ssh/config"
ssh-keyscan -t ed25519 github.com 2>/dev/null >> "$HOME/.ssh/known_hosts"; sort -u -o "$HOME/.ssh/known_hosts" "$HOME/.ssh/known_hosts"
if ! git ls-remote "$ATP_REPO" HEAD >/dev/null 2>&1; then
  echo
  echo "Add this key at GitHub > AdvanceTradingPlatform > Settings > Deploy keys > Add deploy key"
  echo "(title: hostinger-$(hostname), 'Allow write access' OFF), then run this block again:"
  echo
  cat "$KEY.pub"
  exit 10
fi

log "repository $ATP_REF"
if [ ! -d "$ATP_DIR/.git" ]; then
  git clone -q "$ATP_REPO" "$ATP_DIR"
fi
cd "$ATP_DIR"
git fetch -q origin
PREV_SHA="$(git rev-parse HEAD)"
git checkout -q --detach "$ATP_REF"
SHA="$(git rev-parse HEAD)"

log ".env (secrets stay on this server)"
if [ ! -f .env ]; then
  umask 077
  sed -e 's/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=CHANGE_ME/' \
      -e 's/^JWT_SECRET_KEY=.*/JWT_SECRET_KEY=CHANGE_ME/' \
      -e 's/^SECRETS_ENCRYPTION_KEY=.*/SECRETS_ENCRYPTION_KEY=CHANGE_ME/' \
      -e 's/^METRICS_TOKEN=.*/METRICS_TOKEN=CHANGE_ME/' \
      -e 's/^DOMAIN=.*/DOMAIN=CHANGE_ME/' \
      -e 's/^ACME_EMAIL=.*/ACME_EMAIL=CHANGE_ME/' \
      -e 's/^OFFSITE_REMOTE=.*/OFFSITE_REMOTE=offsite:atp-backups/' \
      -e 's/^OFFSITE_S3_ENDPOINT=.*/OFFSITE_S3_ENDPOINT=CHANGE_ME/' \
      -e 's/^OFFSITE_S3_ACCESS_KEY=.*/OFFSITE_S3_ACCESS_KEY=CHANGE_ME/' \
      -e 's/^OFFSITE_S3_SECRET_KEY=.*/OFFSITE_S3_SECRET_KEY=CHANGE_ME/' \
      .env.example > .env
  cat >> .env <<'ENV'

# --- Hostinger KVM 2 (docker-compose.hostinger.yml) ---
ENVIRONMENT=production
# CADDYFILE=Caddyfile with a domain (Let's Encrypt), Caddyfile.ip with DOMAIN=<server IP> until DNS exists
CADDYFILE=Caddyfile.ip
# off-site copy (H-2): Cloudflare R2 - OFFSITE_S3_ENDPOINT=https://<account id>.r2.cloudflarestorage.com and a
# bucket-scoped "Object Read & Write" token above. Backblaze B2 instead: OFFSITE_REMOTE=b2:<bucket> + the two lines below.
OFFSITE_S3_PROVIDER=Cloudflare
OFFSITE_B2_KEY_ID=
OFFSITE_B2_APPLICATION_KEY=
BACKUP_ENCRYPTION_PASSPHRASE=CHANGE_ME
ENV
  chmod 600 .env
fi
chmod 600 .env
if grep -q 'CHANGE_ME' .env; then
  echo
  echo "Fill these in $ATP_DIR/.env on the server (nano .env), then run this block again:"
  grep -n 'CHANGE_ME' .env | sed 's/=.*//'
  echo "  JWT_SECRET_KEY / METRICS_TOKEN / passwords: python3 -c \"import secrets;print(secrets.token_urlsafe(48))\""
  echo "  SECRETS_ENCRYPTION_KEY: the SAME 44-character key the PAPER PC uses (never a new one - stored broker keys depend on it)"
  echo "  DOMAIN: your domain, or this server's IP with CADDYFILE=Caddyfile.ip"
  echo "  OFFSITE_S3_*: the Cloudflare R2 bucket's endpoint and token (docs/DEPLOY_HOSTINGER_MR.md section 5)"
  exit 10
fi

COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.hostinger.yml -p atp"
log "build + migrations + rolling restart (scripts/deploy.sh)"
COMPOSE_OVERLAYS="-f docker-compose.hostinger.yml" sh scripts/deploy.sh production "$SHA"
$COMPOSE up -d               # caddy, backup, offsite and anything not yet running
$COMPOSE exec -T backend alembic current

log "health"
for i in $(seq 1 60); do
  curl -sf http://127.0.0.1:8000/api/system/health/deep >/dev/null && break; sleep 2
done
curl -s http://127.0.0.1:8000/api/system/health; echo
DOMAIN="$(grep -E '^DOMAIN=' .env | cut -d= -f2-)"
curl -sk -o /dev/null -w "public https://$DOMAIN/api/system/health -> %{http_code}\n" "https://$DOMAIN/api/system/health" || true

# record what runs, for rollback.sh
mkdir -p .deploy
[ "$PREV_SHA" = "$SHA" ] || echo "$(date -Iseconds) $PREV_SHA" >> .deploy/history
echo "$SHA" > .deploy/current

log "first PAPER day check (read-only)"
$COMPOSE exec -T backend python scripts/first_paper_day_check.py || \
  echo "(an organisation is needed first: register at https://$DOMAIN, then re-run: $COMPOSE exec backend python scripts/first_paper_day_check.py --tenant <email>)"
echo
echo "deployed $SHA - status: bash $ATP_DIR/deploy/hostinger/status.sh   rollback: bash $ATP_DIR/deploy/hostinger/rollback.sh"
