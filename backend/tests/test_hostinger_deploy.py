"""Hostinger KVM 2 production host: the three blocks (bootstrap, deploy, rollback), status, the 8 GB overlay.

Nothing here touches a server - it checks the scripts and the compose files the operator will run:
- every script is valid bash; the one-line blocks in docs/ are exactly the scripts (base64) - no drift;
- the merged production config publishes only Caddy's 80/443 and keeps Postgres / Redis / the API on 127.0.0.1;
- the memory limits fit the 8 GB budget with room for the OS;
- the deploy block never carries a secret: it creates .env from .env.example with CHANGE_ME placeholders (mode 600)
  and refuses to go on until they are filled; it refuses to run as root;
- the IP-only Caddyfile routes exactly like the domain one, over HTTPS (Secure cookies).
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ["bootstrap", "deploy", "rollback", "status", "oneliners"]


@pytest.mark.parametrize("name", SCRIPTS)
def test_scripts_are_valid_bash(name):
    subprocess.run(["bash", "-n", str(ROOT / "deploy/hostinger" / f"{name}.sh")], check=True)


def test_the_one_line_blocks_are_the_scripts():
    generated = subprocess.run(["bash", str(ROOT / "deploy/hostinger/oneliners.sh")], capture_output=True, text=True, check=True).stdout
    committed = (ROOT / "docs/DEPLOY_HOSTINGER_ONELINERS.txt").read_text()
    body = "\n".join(l for l in committed.splitlines() if not l.startswith("# Hostinger") and not l.startswith("# do not edit"))
    assert body.strip() == generated.strip(), "run: bash deploy/hostinger/oneliners.sh >> docs/DEPLOY_HOSTINGER_ONELINERS.txt (see its header)"
    for name in ("bootstrap", "deploy", "rollback", "status"):
        assert re.search(rf"# {name}\.sh  sha256 [0-9a-f]{{64}}\nbash -c \"\$\(echo [A-Za-z0-9+/=]+ \| base64 -d\)\"", committed), name


def test_deploy_block_never_carries_a_secret_and_refuses_root():
    text = (ROOT / "deploy/hostinger/deploy.sh").read_text()
    assert 'id -u)" != "0"' in text                                       # not as root
    for key in ("POSTGRES_PASSWORD", "JWT_SECRET_KEY", "SECRETS_ENCRYPTION_KEY", "METRICS_TOKEN", "DOMAIN", "ACME_EMAIL"):
        assert f"s/^{key}=.*/{key}=CHANGE_ME/" in text, key
    assert "BACKUP_ENCRYPTION_PASSPHRASE=CHANGE_ME" in text
    assert "chmod 600 .env" in text and "grep -q 'CHANGE_ME' .env" in text and "exit 10" in text
    assert not re.search(r"(?i)(password|secret|token)=['\"]?[A-Za-z0-9_\-]{16,}", text)   # no literal credential
    boot = (ROOT / "deploy/hostinger/bootstrap.sh").read_text()
    for must in ("PasswordAuthentication no", "ufw allow 22/tcp", "ufw allow 80/tcp", "ufw allow 443/tcp", "fail2ban",
                 "unattended-upgrades", "docker-compose-plugin", "set-timezone Asia/Kolkata", "chrony", "/swapfile",
                 'Automatic-Reboot "false"'):
        assert must in boot, must


def _merged():
    if not shutil.which("docker") or subprocess.run(["docker", "compose", "version"], capture_output=True).returncode:
        pytest.skip("docker compose not available")
    env = ROOT / "backend/.pytest_env_hostinger"
    env.write_text((ROOT / ".env.example").read_text() + "\nDOMAIN=203.0.113.7\nACME_EMAIL=ops@localhost\nCADDYFILE=Caddyfile.ip\n")
    try:
        out = subprocess.run(["docker", "compose", "--env-file", str(env), "-f", "docker-compose.yml", "-f", "docker-compose.prod.yml",
                              "-f", "docker-compose.hostinger.yml", "-p", "atp", "config"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    finally:
        os.unlink(env)
    return yaml.safe_load(out)


def test_only_caddy_is_public_and_the_limits_fit_eight_gigabytes():
    cfg = _merged()
    services = cfg["services"]
    for name, svc in services.items():
        for port in svc.get("ports") or []:
            if name == "caddy":
                assert str(port["published"]) in ("80", "443")
            else:
                assert port.get("host_ip") == "127.0.0.1", (name, port)          # Docker bypasses ufw: never 0.0.0.0
    limits = {name: int(svc["deploy"]["resources"]["limits"]["memory"]) for name, svc in services.items()}
    assert set(limits) >= {"postgres", "redis", "backend", "worker", "frontend", "caddy", "backup", "offsite"}
    assert sum(limits.values()) <= 7 * 1024 ** 3                                  # room for the OS on an 8 GB host
    pg = " ".join(services["postgres"]["command"])
    assert "archive_mode=on" in pg and "shared_buffers=512MB" in pg              # WAL archiving kept under the tuning
    assert "volatile-lru" in services["redis"]["command"]                         # never evicts a key without a TTL
    assert services["caddy"]["volumes"][0]["source"].endswith("deploy/Caddyfile.ip")


def test_ip_caddyfile_routes_like_the_domain_one_over_https():
    domain = (ROOT / "deploy/Caddyfile").read_text()
    ip = (ROOT / "deploy/Caddyfile.ip").read_text()
    assert "tls internal" in ip and "redir https://{host}{uri}" in ip
    for route in ("handle /api/*", "reverse_proxy backend:8000", "reverse_proxy frontend:80", "@metrics path /metrics", "max_size 8MB",
                  "Content-Security-Policy"):
        assert route in domain and route in ip, route


def test_deploy_sh_takes_the_host_overlay():
    text = (ROOT / "scripts/deploy.sh").read_text()
    assert 'FILES="$FILES ${COMPOSE_OVERLAYS:-}"' in text
    for script in ("deploy", "rollback"):
        assert 'COMPOSE_OVERLAYS="-f docker-compose.hostinger.yml" sh scripts/deploy.sh production' in (ROOT / f"deploy/hostinger/{script}.sh").read_text()
    overlay = (ROOT / "docker-compose.hostinger.yml").read_text()             # compose-only tags (!override): read as text
    for knob in ("${REDIS_MAXMEMORY:-384mb}", "${MEM_POSTGRES:-2g}", "${MEM_BACKEND:-1536m}", "${MEM_WORKER:-1536m}", "${PG_SHARED_BUFFERS:-512MB}"):
        assert knob in overlay, knob                                            # every number overridable from .env
