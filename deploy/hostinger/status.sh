#!/usr/bin/env bash
# "What is running" - read-only, safe at any time (as the non-root user):  bash /opt/atp/deploy/hostinger/status.sh
set -uo pipefail
ATP_DIR="${ATP_DIR:-/opt/atp}"
cd "$ATP_DIR" 2>/dev/null || { echo "not installed in $ATP_DIR"; exit 1; }
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.hostinger.yml -p atp"
echo "== commit:   $(git rev-parse --short HEAD) $(git log -1 --format='%cd %s' --date=format:'%Y-%m-%d %H:%M' | cut -c1-90)"
echo "== previous: $(tail -n1 .deploy/history 2>/dev/null || echo none)"
echo "== time:     $(date '+%Y-%m-%d %H:%M %Z')  uptime $(uptime -p)"
echo "== containers"; $COMPOSE ps --format '{{.Service}}\t{{.Status}}' | column -t
echo "== memory (limit / used)"; docker stats --no-stream --format '{{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}' | column -t
echo "== host";     free -h | head -2; df -h / | tail -1; swapon --show --noheadings
echo "== api";      curl -s -m 5 http://127.0.0.1:8000/api/system/health; echo
echo "== deep";     curl -s -m 10 http://127.0.0.1:8000/api/system/health/deep | head -c 400; echo
echo "== worker heartbeat / market"; curl -s -m 5 http://127.0.0.1:8000/api/system/status | head -c 400; echo
echo "== migrations"; $COMPOSE exec -T backend alembic current 2>/dev/null | tail -1
echo "== last backup"; $COMPOSE exec -T backup sh -c 'ls -l /backups/latest 2>/dev/null; ls /backups | tail -3' 2>/dev/null
echo "== off-site";   ls -1t /var/backups/atp-offsite/backups 2>/dev/null | head -3 || true
echo "== public ports (only 22, 80, 443 expected)"; ss -tlnH | awk '{print $4}' | grep -vE '^(127\.0\.0\.1|\[::1\]|127\.0\.0\.53)' | sort -u
echo "== firewall";   sudo -n ufw status 2>/dev/null | head -8 || echo "(sudo needed)"
echo "== certificate"; DOMAIN="$(grep -E '^DOMAIN=' .env | cut -d= -f2-)"; echo | timeout 5 openssl s_client -connect "$DOMAIN:443" -servername "$DOMAIN" 2>/dev/null | openssl x509 -noout -issuer -enddate 2>/dev/null || echo "no TLS answer on $DOMAIN:443"
echo "== egress IP (register this with each broker - SEBI static IP)"; curl -s -m 5 https://api.ipify.org; echo
