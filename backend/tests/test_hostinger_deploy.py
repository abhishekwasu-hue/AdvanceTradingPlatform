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


# --- H-2 (answered 2026-10-10): Cloudflare R2 off-site, B2 alternative, restore test from R2 -----------------------

_FAKE_RCLONE = r'''#!/bin/sh
# Just enough rclone for offsite_fetch.sh against a local directory standing in for the bucket.
cmd="$1"; shift
case "$cmd" in
  lsf) dir="$1"; shift; pats=""
       while [ $# -gt 0 ]; do case "$1" in --include) pats="$pats $2"; shift 2;; *) shift;; esac; done
       for f in "$dir"/*; do b="$(basename "$f")"; for p in $pats; do case "$b" in $p) echo "$b";; esac; done; done ;;
  copyto) [ -f "$1" ] || exit 3; cp "$1" "$2" ;;
  *) exit 2 ;;
esac
'''


def test_restore_test_fetches_the_newest_dump_and_its_checksum_from_off_site(tmp_path):
    if not shutil.which("sh"):
        pytest.skip("no sh")
    bindir, bucket, dest = tmp_path / "bin", tmp_path / "bucket", tmp_path / "restore"
    bindir.mkdir(); (bucket / "backups").mkdir(parents=True); dest.mkdir()
    (bindir / "rclone").write_text(_FAKE_RCLONE); (bindir / "rclone").chmod(0o755)
    for name in ("atp_atp_20261008T183000Z.dump.enc", "atp_atp_20261009T183000Z.dump.enc", "notes.txt"):
        (bucket / "backups" / name).write_text(name)
    (bucket / "backups" / "atp_atp_20261009T183000Z.dump.enc.sha256").write_text("abc")
    (dest / "atp_atp_20261001T183000Z.dump.enc").write_text("old test copy")
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "OFFSITE_REMOTE": str(bucket)}
    script = str(ROOT / "scripts/backup/offsite_fetch.sh")
    out = subprocess.run(["sh", script, str(dest)], env=env, capture_output=True, text=True, check=True).stdout.strip()
    assert out == str(dest / "atp_atp_20261009T183000Z.dump.enc")                    # the newest by its UTC stamp
    assert sorted(p.name for p in dest.iterdir()) == ["atp_atp_20261009T183000Z.dump.enc", "atp_atp_20261009T183000Z.dump.enc.sha256"]
    for f in (bucket / "backups").iterdir():
        if f.name.startswith("atp_"):
            f.unlink()
    assert subprocess.run(["sh", script, str(dest)], env=env, capture_output=True, text=True).returncode == 1   # nothing off-site: fails


def test_r2_is_the_off_site_default_with_b2_as_the_alternative_and_no_credential_outside_dotenv():
    overlay = (ROOT / "docker-compose.hostinger.yml").read_text()
    for line in ("OFFSITE_REMOTE: ${OFFSITE_REMOTE:-offsite:atp-backups}", "RCLONE_CONFIG_OFFSITE_PROVIDER: ${OFFSITE_S3_PROVIDER:-Cloudflare}",
                 "RCLONE_CONFIG_OFFSITE_ACCESS_KEY_ID: ${OFFSITE_S3_ACCESS_KEY:-}", "RCLONE_CONFIG_B2_TYPE: b2",
                 "RCLONE_CONFIG_B2_KEY: ${OFFSITE_B2_APPLICATION_KEY:-}", "/restore:ro"):
        assert line in overlay, line
    deploy = (ROOT / "deploy/hostinger/deploy.sh").read_text()
    for key in ("OFFSITE_S3_ENDPOINT", "OFFSITE_S3_ACCESS_KEY", "OFFSITE_S3_SECRET_KEY"):
        assert f"s/^{key}=.*/{key}=CHANGE_ME/" in deploy, key                          # the deploy stops until R2 is filled in
    assert "s/^OFFSITE_REMOTE=.*/OFFSITE_REMOTE=offsite:atp-backups/" in deploy and "OFFSITE_S3_PROVIDER=Cloudflare" in deploy
    runbook = (ROOT / "docs/DEPLOY_HOSTINGER_MR.md").read_text()
    assert "offsite_fetch.sh /restore" in runbook and "verify_backup.sh \"$F\"" in runbook and "Object Read & Write" in runbook
    assert "/var/backups/atp-restore-test" in (ROOT / "deploy/hostinger/bootstrap.sh").read_text()
