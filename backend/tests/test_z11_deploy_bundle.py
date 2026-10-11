"""Z11: the Hostinger deploy bundle (deploy/atp.sh) run against a docker-less fake host.

Nothing here reaches a server, a registry or the network. Each test builds a throwaway host in tmp_path:
- a local bare git repository standing in for GitHub (commits with deploy/atp.sh on main, one commit off main);
- a clone of it as the install directory and a .env holding sentinel "secrets";
- fake `docker`, `curl`, `ss`, `crontab` on PATH (Python) that act on one JSON state file: images, running services
  and their release, the database revision per database, backups, R2 objects, feature flags. Every call is logged.
git, tar, openssl, sha256sum, flock and python3 are the real ones, so the files archive is really encrypted and
really verified. The script under test is the repository's deploy/atp.sh, run from where it is.

What is pinned: the order bootstrap -> deploy (pre-migration backup before the migration, API before worker) ->
health; fail-closed exits (missing / placeholder keys, changed SECRETS_ENCRYPTION_KEY, market open, unhealthy API,
unmerged ref, edited checkout); no secret in any output, argument or state file; .env never written; flags never
written; no `alembic downgrade`, no `docker compose config`; rollback to the previous image tag (code-only with the
DB-ahead mode, or --restore-db that keeps the live database); backups without R2 (graceful) and with R2 (verified).
"""
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ATP_SH = ROOT / "deploy" / "atp.sh"
SECRETS = {
    "POSTGRES_PASSWORD": "SENTINEL-pg-7c1d9e",
    "JWT_SECRET_KEY": "SENTINEL-jwt-55aa01",
    "SECRETS_ENCRYPTION_KEY": "SENTINEL-master-key-0b6f2c7f9a1e4d3c8b5a6f7e2d1c0b9a8f7e6d5c4b3=",
    "METRICS_TOKEN": "SENTINEL-metrics-31f0",
    "BACKUP_ENCRYPTION_PASSPHRASE": "SENTINEL-backup-pass-9e2b",
}
R2 = {"OFFSITE_S3_ENDPOINT": "https://example-account.r2.cloudflarestorage.com", "OFFSITE_S3_ACCESS_KEY": "SENTINEL-r2-access-11aa",
      "OFFSITE_S3_SECRET_KEY": "SENTINEL-r2-secret-22bb"}

pytestmark = pytest.mark.skipif(not all(shutil.which(t) for t in ("bash", "git", "openssl", "tar", "flock", "sha256sum")),
                                reason="needs bash, git, openssl, tar, flock, sha256sum")

FAKE_DOCKER = r'''
import json, os, sys, time
STATE = os.environ["FAKE_STATE"]
with open(STATE) as fh:
    st = json.load(fh)
argv = sys.argv[1:]
st["calls"].append({"argv": argv, "release": os.environ.get("ATP_RELEASE", ""), "ahead": os.environ.get("MIGRATION_ALLOW_DB_AHEAD", "")})
rc = 0
out = []
APP = {"backend": "atp-backend", "worker": "atp-worker", "frontend": "atp-frontend", "research-worker": "atp-research-worker"}
ALL = ["postgres", "redis", "backend", "worker", "frontend", "caddy", "backup", "offsite"]
rel = os.environ.get("ATP_RELEASE", "")
db = st["db_name"]

def finish():
    with open(STATE, "w") as fh:
        json.dump(st, fh)
    if out:
        print("\n".join(out))
    sys.exit(rc)

if argv[:2] == ["compose", "version"]:
    out.append("2.29.1"); finish()
if argv[:1] == ["info"]:
    finish()
if argv[:2] == ["image", "inspect"]:
    name = argv[-1]
    if name in st["images"] or ":" in name and name.split(":")[0] in ("postgres", "redis", "caddy", "rclone/rclone"):
        if "--format" in argv:
            out.append(name.split(":")[0] + "@sha256:0123456789abcdef0123")
    else:
        rc = 1
    finish()
if argv[:2] == ["image", "ls"]:
    repo = argv[-1]
    out.extend(i.split(":", 1)[1] for i in st["images"] if i.split(":", 1)[0] == repo); finish()
if argv[:2] == ["image", "rm"]:
    if argv[-1] in st["images"]:
        st["images"].remove(argv[-1])
    finish()
if argv[0] != "compose":
    rc = 2; finish()

# strip compose globals: -f X ... -p atp
rest, i = [], 1
while i < len(argv):
    if argv[i] in ("-f", "-p"):
        i += 2; continue
    rest.append(argv[i]); i += 1
sub, args = rest[0], rest[1:]

def svc_seen(pos):
    return True

def opts_and_services(a, with_value=("-e", "-v", "--entrypoint", "--format", "--tail", "-w", "--wait-timeout")):
    flags, pos, j = {}, [], 0
    while j < len(a):
        if pos and sub in ("run", "exec") and svc_seen(pos):
            pos.append(a[j]); j += 1        # after the service name everything is the command
        elif a[j] in with_value:
            flags.setdefault(a[j], []).append(a[j + 1]); j += 2
        elif a[j].startswith("-"):
            flags.setdefault(a[j], []).append(True); j += 1
        else:
            pos.append(a[j]); j += 1
    return flags, pos

if sub == "config":
    rc = 99; finish()
if sub == "build":
    for svc, img in APP.items():
        if svc != "research-worker":
            st["images"].append(f"{img}:{rel}")
    st["images"] = sorted(set(st["images"]))
    finish()
if sub == "up":
    flags, svcs = opts_and_services(args)
    targets = svcs or ALL
    for svc in targets:
        if svc in APP and f"{APP[svc]}:{rel}" not in st["images"]:
            print(f"pull access denied for {APP[svc]}", file=sys.stderr); rc = 1; finish()
    for svc in targets:
        st["running"][svc] = {"release": rel if svc in APP else "infra", "ahead": os.environ.get("MIGRATION_ALLOW_DB_AHEAD", "")}
    finish()
if sub == "stop":
    for svc in args:
        st["running"].pop(svc, None)
    st["stopped_at_call"] = len(st["calls"])
    finish()
if sub == "ps":
    for svc, info in st["running"].items():
        health = "healthy" if svc in ("postgres", "redis", "backend", "worker") else ""
        out.append(f"{svc}|running|{health}")
    finish()
if sub == "logs":
    flags, svcs = opts_and_services(args)
    if svcs and svcs[0] == "offsite":
        out.extend(f"2026-10-11T00:00:00Z offsite: synced to offsite:atp-backups" for _ in st["offsite_pushes"][-1:])
    finish()
if sub == "exec":
    flags, pos = opts_and_services(args, with_value=("-U", "-d", "-c", "-v", "-e"))
    svc, cmd = pos[0], pos[1:]
    if svc == "postgres":
        target, sqls = db, []
        for k in range(len(cmd) - 1):
            if cmd[k] == "-d":
                target = cmd[k + 1]
            elif cmd[k] == "-c":
                sqls.append(cmd[k + 1])
        for sql in sqls:
            low = sql.lower()
            if low.startswith("create database"):
                st["databases"][sql.split('"')[1]] = {"rev": "-"}
            elif low.startswith("alter database"):
                parts = sql.split('"')
                st["databases"][parts[3]] = st["databases"].pop(parts[1])
            elif "pg_terminate_backend" in low:
                pass
            elif "alembic_version" in low:
                r = st["databases"].get(target, {}).get("rev", "-")
                if r == "-":
                    rc = 1
                else:
                    out.append(r)
            elif "md5(value_json)" in low:
                if not st["flags_table"]:
                    rc = 1
                elif st["flags_json"] is not None:
                    import hashlib
                    out.append(hashlib.md5(st["flags_json"].encode()).hexdigest())
            elif "select value_json" in low:
                if st["flags_json"] is not None:
                    out.append(st["flags_json"])
            else:
                st.setdefault("unexpected_sql", []).append(sql); rc = 5
        finish()
    if svc == "backup" and cmd[:2] == ["cat", "/backups/LAST_BACKUP_OK"]:
        if st["last_backup_ok"]:
            out.append(st["last_backup_ok"])
        else:
            rc = 1
        finish()
    if svc == "backend":
        if cmd[:2] == ["test", "-f"]:
            finish()
        if cmd[:2] == ["python", "scripts/check_secrets_key.py"]:
            out.append("secrets key: ok - 2 organisation data keys open"); finish()
    rc = 6; finish()
if sub == "run":
    flags, pos = opts_and_services(args)
    svc, cmd = pos[0], pos[1:]
    entry = (flags.get("--entrypoint") or [""])[0]
    envs = dict(e.split("=", 1) for e in flags.get("-e", []))
    if svc == "backend" and cmd[:4] == ["python", "-m", "alembic", "heads"]:
        out.append(f"{st['heads'][rel]} (head)"); finish()
    if svc == "backend" and cmd[:2] == ["python", "scripts/migrate_guard.py"]:
        if st["market_open"]:
            print("guard: REFUSING to migrate - NSE session is open", file=sys.stderr); rc = 3; finish()
        st["databases"][db]["rev"] = st["heads"][rel]; finish()
    if svc == "backup" and entry == "sh":
        if cmd[:1] == ["/scripts/backup.sh"]:
            name = f"atp_{db}_2026101{len(st['backups'])}T183000Z.dump.enc"
            st["backups"].append({"name": name, "rev": st["databases"][db]["rev"]})
            st["last_backup_ok"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            finish()
        if cmd[:2] == ["-c", "readlink /backups/latest"]:
            out.append(st["backups"][-1]["name"]); finish()
        if cmd[:1] == ["-c"] and "restore.sh" in cmd[1]:
            name = cmd[2]
            src = next(b for b in st["backups"] if b["name"] == name)
            st["databases"][envs["ATP_TARGET_DB"]] = {"rev": src["rev"]}
            finish()
        if cmd[:1] == ["/scripts/verify_backup.sh"]:
            st["verified"].append(cmd[1])
            out.append(json.dumps({"status": st["verify_status"], "file": os.path.basename(cmd[1]), "alembic": {"source": "r", "copy": "r"},
                                   "counts": {"tenants": {"source": 2, "copy": 2}, "orders": {"source": 5, "copy": 5}}, "notes": ""}))
            finish()
    if svc == "offsite":
        if cmd == ["--once"]:
            st["offsite_pushes"].append(len(st["backups"])); finish()
        if entry == "sh" and cmd[:1] == ["-c"] and "rclone copy /files" in cmd[1]:
            host_dir = [v for v in flags.get("-v", []) if v.endswith(":/files:ro")][0].rsplit(":/files:ro", 1)[0]
            bucket = st["bucket_files_dir"]
            os.makedirs(bucket, exist_ok=True)
            for f in os.listdir(host_dir):
                if f.startswith("atp_files_"):
                    with open(os.path.join(host_dir, f), "rb") as a, open(os.path.join(bucket, f), "wb") as b:
                        b.write(a.read())
            finish()
        if entry == "sh" and cmd[:1] == ["/scripts/offsite_fetch.sh"]:
            out.append("/restore/" + st["backups"][-1]["name"]); finish()
        if entry == "sh" and cmd[:1] == ["-c"] and "rclone lsf" in cmd[1]:
            bucket = st["bucket_files_dir"]
            names = sorted(f for f in os.listdir(bucket) if f.endswith(".enc")) if os.path.isdir(bucket) else []
            if names:
                for f in (names[-1], names[-1] + ".sha256"):
                    with open(os.path.join(bucket, f), "rb") as a, open(os.path.join(st["restore_dir"], f), "wb") as b:
                        b.write(a.read())
                out.append(names[-1])
            else:
                out.append("")
            finish()
    rc = 7; finish()
rc = 8; finish()
'''

FAKE_CURL = r'''
import json, os, sys
with open(os.environ["FAKE_STATE"]) as fh:
    st = json.load(fh)
args = sys.argv[1:]
url = [a for a in args if a.startswith("http")][-1]
backend = st["running"].get("backend")
db_rev = st["databases"][st["db_name"]]["rev"]
up = backend is not None and backend["release"] not in st["bad_releases"]
if up:
    head = st["heads"].get(backend["release"])
    if db_rev not in (head, "-") and backend["ahead"] != "1":
        up = False          # an older image on a newer database refuses to start without the DB-ahead mode
if "-w" in args:
    print("200" if up and "caddy" in st["running"] else "000", end="")
    sys.exit(0)
if not up:
    sys.exit(7)
if url.endswith("/api/system/health"):
    print(json.dumps({"status": "ok"})); sys.exit(0)
if url.endswith("/health/deep"):
    mig = "ok" if db_rev == st["heads"].get(backend["release"]) else "behind"
    print(json.dumps({"status": "ok" if mig == "ok" else "degraded", "checks": {"database": {"status": "ok"}, "migrations": {"status": mig},
                      "worker": {"status": "ok"}, "redis": {"status": "ok"}}}))
    sys.exit(0)
sys.exit(22)
'''


def _git(cwd, *args, env=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True,
                          env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid", **(env or {})}).stdout.strip()


class Host:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.origin = tmp / "origin.git"
        self.work = tmp / "work"
        self.dir = tmp / "opt-atp"
        self.bin = tmp / "bin"
        self.state_path = tmp / "state.json"
        self.crontab = tmp / "crontab.txt"
        self.restore_dir = tmp / "restore"
        self.files_dir = tmp / "files"
        for d in (self.bin, self.restore_dir, tmp / "offsite-local"):
            d.mkdir()
        _git(tmp, "init", "-q", "--bare", "-b", "main", str(self.origin))
        _git(tmp, "clone", "-q", str(self.origin), str(self.work))
        (self.work / "deploy").mkdir()
        shutil.copy(ATP_SH, self.work / "deploy" / "atp.sh")
        (self.work / "README.md").write_text("release A\n")
        _git(self.work, "add", "-A"); _git(self.work, "commit", "-q", "-m", "release A")
        self.sha_a = _git(self.work, "rev-parse", "HEAD")
        (self.work / "README.md").write_text("release B\n")
        _git(self.work, "commit", "-q", "-am", "release B")
        self.sha_b = _git(self.work, "rev-parse", "HEAD")
        _git(self.work, "push", "-q", "origin", "main")
        _git(self.work, "checkout", "-q", "-b", "side")
        (self.work / "README.md").write_text("unmerged\n")
        _git(self.work, "commit", "-q", "-am", "unmerged")
        self.sha_side = _git(self.work, "rev-parse", "HEAD")
        _git(self.work, "push", "-q", "origin", "side")
        _git(tmp, "clone", "-q", str(self.origin), str(self.dir))
        self.write_env()
        rel_a, rel_b = self.sha_a[:12], self.sha_b[:12]
        self.state = {"calls": [], "images": [], "running": {}, "db_name": "advance_trading_platform",
                      "databases": {"advance_trading_platform": {"rev": "-"}}, "heads": {rel_a: "rev1", rel_b: "rev1", "latest": "rev1"},
                      "market_open": False, "backups": [], "last_backup_ok": None, "offsite_pushes": [], "verified": [],
                      "verify_status": "ok", "bad_releases": [], "flags_table": False, "flags_json": None,
                      "bucket_files_dir": str(tmp / "bucket-files"), "restore_dir": str(self.restore_dir)}
        self.save()
        py = sys.executable
        for name, body in (("docker", FAKE_DOCKER), ("curl", FAKE_CURL)):
            (self.bin / name).write_text(f"#!{py} -S\n{body}")       # -S: stdlib only, starts faster
        (self.bin / "ss").write_text("#!/bin/sh\n[ -n \"$FAKE_SS\" ] && { printf '%s\\n' \"$FAKE_SS\"; exit 0; }\n"
                                     "printf 'LISTEN 0 4096 0.0.0.0:22 0.0.0.0:*\\nLISTEN 0 4096 0.0.0.0:443 0.0.0.0:*\\n"
                                     "LISTEN 0 4096 127.0.0.1:8000 0.0.0.0:*\\n'\n")
        (self.bin / "crontab").write_text(f"#!/bin/sh\nF='{self.crontab}'\nif [ \"$1\" = -l ]; then [ -f \"$F\" ] && cat \"$F\" || exit 1; else cat > \"$F\"; fi\n")
        if os.getuid() == 0:   # the script refuses root; the CI runner is not root, this sandbox may be
            (self.bin / "id").write_text("#!/bin/sh\ncase \"$1\" in -u) echo 1000;; -un) echo atp;; *) exec /usr/bin/id \"$@\";; esac\n")
        for f in self.bin.iterdir():
            f.chmod(0o755)

    def write_env(self, extra=None, drop=(), mode=0o600):
        values = {"POSTGRES_USER": "atp_user", "POSTGRES_DB": "advance_trading_platform", "DOMAIN": "203.0.113.7",
                  "ACME_EMAIL": "ops@localhost", "CADDYFILE": "Caddyfile.ip", "OFFSITE_REMOTE": "offsite:atp-backups",
                  "OFFSITE_RESTORE_DIR": str(self.restore_dir), "OFFSITE_LOCAL_DIR": str(self.tmp / "offsite-local"),
                  "BACKUP_EXTRA_DIRS": "", **SECRETS, **(extra or {})}
        lines = [f"{k}={v}" for k, v in values.items() if k not in drop]
        env = self.dir / ".env"
        env.write_text("# fake host .env\n" + "\n".join(lines) + "\n")
        env.chmod(mode)
        if os.getuid() == 0:
            os.chown(env, 1000, 1000)

    def env_digest(self):
        return hashlib.sha256((self.dir / ".env").read_bytes()).hexdigest()

    def save(self):
        self.state_path.write_text(json.dumps(self.state))

    def load(self):
        self.state = json.loads(self.state_path.read_text())
        return self.state

    def run(self, *args, extra_env=None, timeout=120):
        env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "FAKE_STATE": str(self.state_path), "ATP_DIR": str(self.dir),
               "ATP_HEALTH_TIMEOUT": "2", "ATP_POLL_SECONDS": "1", "ATP_FILES_BACKUP_DIR": str(self.files_dir), "HOME": str(self.tmp),
               **(extra_env or {})}
        env.pop("ATP_DRY_RUN", None)
        res = subprocess.run(["bash", str(ATP_SH), *args], env=env, capture_output=True, text=True, timeout=timeout)
        self.load()
        res.all = res.stdout + res.stderr
        for name, secret in {**SECRETS, **R2}.items():
            assert secret not in res.all, f"{name} printed"
        for call in self.state["calls"]:
            assert not any(secret in a for a in call["argv"] for secret in {**SECRETS, **R2}.values()), call
            assert call["argv"][:1] != ["compose"] or "config" not in call["argv"], "compose config prints secrets"
            assert "downgrade" not in " ".join(call["argv"])
        return res

    def calls(self, since=0):
        return [" ".join(c["argv"]) for c in self.state["calls"][since:]]

    def index(self, needle, since=0):
        for i, c in enumerate(self.calls(since)):
            if needle in c:
                return i
        return -1

    def mutating(self, since=0):
        keys = (" up ", " build", " stop ", " run ", "image rm", "CREATE DATABASE", "ALTER DATABASE")
        return [c for c in self.calls(since) if any(k in f" {c} " for k in keys)]


@pytest.fixture
def host(tmp_path):
    return Host(tmp_path)


def test_scripts_parse_and_pass_shellcheck():
    for script in (ATP_SH, ROOT / "deploy/hostinger/install.sh"):
        subprocess.run(["bash", "-n", str(script)], check=True)
        assert os.stat(script).st_mode & stat.S_IXUSR, f"{script} must be executable"
    if not shutil.which("shellcheck"):
        pytest.skip("shellcheck not installed (CI lint job runs it)")
    subprocess.run(["shellcheck", "-x", str(ATP_SH), str(ROOT / "deploy/hostinger/install.sh")], check=True)


def test_first_deploy_backs_up_then_migrates_then_api_then_worker_and_records_the_release(host):
    digest = host.env_digest()
    res = host.run("deploy")
    assert res.returncode == 0, res.all
    calls = host.calls()
    order = [host.index("up -d --wait postgres redis"), host.index(" build --pull"), host.index("alembic heads"),
             host.index("/scripts/backup.sh"), host.index("scripts/migrate_guard.py"), host.index("up -d --no-deps --no-build backend"),
             host.index("up -d --no-build")]
    assert -1 not in order and order == sorted(order), calls
    assert host.state["databases"]["advance_trading_platform"]["rev"] == "rev1"
    assert host.state["running"]["worker"]["release"] == host.sha_b[:12] and host.state["running"]["backend"]["release"] == host.sha_b[:12]
    line = (host.dir / ".deploy/releases.log").read_text().split()
    assert line[1:] == ["deploy", host.sha_b, "-", host.state["backups"][0]["name"], "-", "rev1"]
    assert (host.dir / ".deploy/current").read_text().strip() == host.sha_b
    fingerprint = (host.dir / ".deploy/secrets_key.sha256").read_text().strip()
    assert len(fingerprint) == 64 and SECRETS["SECRETS_ENCRYPTION_KEY"] not in fingerprint
    for f in (host.dir / ".deploy").rglob("*"):
        if f.is_file():
            assert not any(s in f.read_text(errors="ignore") for s in SECRETS.values()), f
    assert host.env_digest() == digest, ".env must never be written"
    assert "deploy/atp.sh backup --scheduled" in host.crontab.read_text() and "restore-check --scheduled" in host.crontab.read_text()
    assert "health: OK" in res.stdout and "deployed " + host.sha_b[:12] in res.stdout
    assert not host.state.get("unexpected_sql")                                   # only the read-only queries above ran
    assert "R2 not configured" in res.stdout or "off-site backups" in res.stdout   # the R2 keys are empty here: a warning, not a stop

    before = len(host.state["calls"])
    again = host.run("deploy")                                                     # idempotent: converges without work
    assert again.returncode == 0, again.all
    later = host.calls(before)
    assert not any(" build" in c for c in later) and not any("/scripts/backup.sh" in c for c in later)
    assert not any("migrate_guard" in c for c in later) and "schema at head" in again.stdout
    assert host.crontab.read_text().count("BEGIN atp.sh managed block") == 1


def test_missing_or_placeholder_secrets_stop_before_anything_runs(host):
    (host.dir / ".env").unlink()
    res = host.run("deploy")
    assert res.returncode == 10 and "cp " in res.all and not (host.dir / ".env").exists()
    assert host.mutating() == []

    host.write_env(extra={"JWT_SECRET_KEY": "CHANGE_ME"}, drop=("BACKUP_ENCRYPTION_PASSPHRASE",))
    res = host.run("deploy")
    assert res.returncode == 10 and "JWT_SECRET_KEY" in res.all and "BACKUP_ENCRYPTION_PASSPHRASE" in res.all
    host.write_env(extra={"POSTGRES_PASSWORD": "atp_dev_password"})
    assert host.run("deploy").returncode == 10
    host.write_env(mode=0o644)
    res = host.run("deploy")
    assert res.returncode == 10 and "chmod 600" in res.all
    assert host.mutating() == []


def test_a_changed_secrets_key_is_refused_and_never_rotated(host):
    assert host.run("deploy", host.sha_a).returncode == 0
    before = len(host.state["calls"])
    host.write_env(extra={"SECRETS_ENCRYPTION_KEY": "SENTINEL-master-key-DIFFERENT-000000000000000000000000="})
    res = host.run("deploy")
    assert res.returncode == 20 and "NOT the key this server deployed with" in res.all
    assert host.mutating(before) == []
    health = host.run("health")
    assert health.returncode == 1 and "differs from the one this host deployed with" in health.stdout


def test_market_open_refuses_the_migration_and_restarts_nothing(host):
    assert host.run("deploy", host.sha_a).returncode == 0
    host.state["heads"][host.sha_b[:12]] = "rev2"; host.state["market_open"] = True; host.save()
    before = len(host.state["calls"])
    res = host.run("deploy")
    assert res.returncode == 3 and "NSE session open" in res.all
    assert host.index("/scripts/backup.sh", before) != -1                          # the backup was taken first
    assert not any("up -d --no-deps --no-build backend" in c for c in host.calls(before))
    assert host.state["running"]["backend"]["release"] == host.sha_a[:12]
    assert (host.dir / ".deploy/current").read_text().strip() == host.sha_a


def test_an_unhealthy_new_api_puts_the_previous_one_back_without_touching_the_worker(host):
    assert host.run("deploy", host.sha_a).returncode == 0
    host.state["heads"][host.sha_b[:12]] = "rev2"; host.state["bad_releases"] = [host.sha_b[:12]]; host.save()
    res = host.run("deploy")
    assert res.returncode == 1 and "failed at the API health check" in res.all
    assert host.state["running"]["backend"] == {"release": host.sha_a[:12], "ahead": "1"}   # previous image, DB-ahead mode
    assert host.state["running"]["worker"]["release"] == host.sha_a[:12]
    assert (host.dir / ".deploy/current").read_text().strip() == host.sha_a
    assert (host.dir / ".deploy/db_ahead").read_text().strip() == "rev2"
    assert _git(host.dir, "rev-parse", "HEAD") == host.sha_a


def test_rollback_reuses_the_previous_images_and_runs_on_the_newer_schema(host):
    assert host.run("deploy", host.sha_a).returncode == 0
    host.state["heads"][host.sha_b[:12]] = "rev2"; host.save()
    assert host.run("deploy").returncode == 0
    assert host.state["databases"]["advance_trading_platform"]["rev"] == "rev2"
    dry_from = len(host.state["calls"])
    dry = host.run("rollback", "--dry-run")
    assert dry.returncode == 0 and "would" in dry.stdout and host.mutating(dry_from) == []
    assert (host.dir / ".deploy/current").read_text().strip() == host.sha_b
    before = len(host.state["calls"])
    res = host.run("rollback")
    assert res.returncode == 0, res.all
    assert not any(" build" in c for c in host.calls(before))                     # images of A still there: no rebuild
    assert host.state["running"]["backend"] == {"release": host.sha_a[:12], "ahead": "1"}
    assert host.state["running"]["worker"]["release"] == host.sha_a[:12]
    assert host.state["databases"]["advance_trading_platform"]["rev"] == "rev2"   # schema kept (additive)
    assert (host.dir / ".deploy/current").read_text().strip() == host.sha_a
    assert "database is ahead" in res.stdout and "health: OK" in res.stdout
    again = host.run("rollback")
    assert again.returncode == 2 and "first release deployed" in again.all        # nothing before A
    fwd = host.run("deploy", host.sha_b)                                           # forward again clears the DB-ahead mode
    assert fwd.returncode == 0 and not (host.dir / ".deploy/db_ahead").exists()
    assert host.state["running"]["backend"] == {"release": host.sha_b[:12], "ahead": ""}


def test_rollback_with_restore_db_keeps_the_live_database_and_needs_the_typed_name(host):
    assert host.run("deploy", host.sha_a).returncode == 0
    host.state["heads"][host.sha_b[:12]] = "rev2"; host.save()
    assert host.run("deploy").returncode == 0
    before = len(host.state["calls"])
    res = host.run("rollback", "--restore-db", extra_env={"ATP_CONFIRM_DB": "wrong"})
    assert res.returncode == 1 and "not confirmed" in res.all and host.mutating(before) == []
    res = host.run("rollback", "--restore-db", extra_env={"ATP_CONFIRM_DB": "advance_trading_platform"})
    assert res.returncode == 0, res.all
    dbs = host.state["databases"]
    assert dbs["advance_trading_platform"]["rev"] == "rev1"                        # the pre-migration backup
    kept = [n for n in dbs if n.startswith("advance_trading_platform_pre_rollback_")]
    assert len(kept) == 1 and dbs[kept[0]]["rev"] == "rev2"                       # nothing destroyed
    later = host.calls(before)
    swap = next(i for i, c in enumerate(later) if "ALTER DATABASE" in c)
    restore = next(i for i, c in enumerate(later) if "restore.sh" in c)
    stop = next(i for i, c in enumerate(later) if c.startswith("compose") and " stop " in f" {c} ")
    assert restore < stop < swap                                                   # restored beside the live DB, then swapped
    assert host.state["running"]["backend"] == {"release": host.sha_a[:12], "ahead": ""}
    assert not (host.dir / ".deploy/db_ahead").exists()


def test_dry_run_changes_nothing(host):
    head = _git(host.dir, "rev-parse", "HEAD")
    res = host.run("deploy", "--dry-run")
    assert res.returncode == 0, res.all
    assert "dry-run" in res.stdout and "+ docker compose up -d --wait postgres redis" in res.stdout and "build --pull" in res.stdout
    assert host.mutating() == [] and not host.crontab.exists()
    assert not (host.dir / ".deploy").exists() and _git(host.dir, "rev-parse", "HEAD") == head


def test_only_merged_code_from_a_clean_checkout_is_deployed(host):
    res = host.run("deploy", host.sha_side)
    assert res.returncode == 2 and "not on origin/main" in res.all and host.mutating() == []
    (host.dir / "README.md").write_text("edited on the server\n")
    res = host.run("deploy")
    assert res.returncode == 1 and "changed on the server" in res.all and host.mutating() == []


def _decrypt_list(path: Path) -> str:
    dec = subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-pass", "env:P", "-in", str(path)],
                         env={**os.environ, "P": SECRETS["BACKUP_ENCRYPTION_PASSPHRASE"]}, capture_output=True, check=True).stdout
    return subprocess.run(["tar", "-tzf", "-"], input=dec, capture_output=True, check=True).stdout.decode()


def test_backup_without_r2_stays_local_and_says_so(host):
    assert host.run("deploy").returncode == 0
    extra = host.tmp / "blobs"; extra.mkdir(); (extra / "chart.png").write_bytes(b"png")
    host.write_env(extra={"BACKUP_EXTRA_DIRS": f"{extra} /does/not/exist"})
    res = host.run("backup", "--verify")
    assert res.returncode == 0, res.all
    assert "R2 not configured yet" in res.stdout and "does not exist - skipped" in res.stdout
    assert not any("offsite" in c for c in host.calls())                          # nothing tried to reach R2
    archives = sorted(host.files_dir.glob("atp_files_*.tar.gz.enc"))
    assert len(archives) == 1 and (archives[0].parent / (archives[0].name + ".sha256")).exists()
    assert b"releases.log" not in archives[0].read_bytes()                       # encrypted, not a plain tarball
    listing = _decrypt_list(archives[0])
    assert ".deploy/releases.log" in listing and "chart.png" in listing and ".deploy/lock" not in listing
    assert "verify_backup.sh latest" in " ".join(host.calls()) and "decrypts" in res.stdout
    check = host.run("restore-check", "--require-offsite")
    assert check.returncode == 1 and "--require-offsite" in check.stdout
    host.state["verify_status"] = "count_mismatch"; host.save()
    assert host.run("restore-check").returncode == 1


def test_backup_with_r2_copies_off_site_and_the_restore_check_reads_back_from_r2(host):
    host.write_env(extra=R2)
    assert host.run("deploy").returncode == 0
    res = host.run("backup")
    assert res.returncode == 0, res.all
    assert host.index("offsite --once") != -1 and any("rclone copy /files" in c for c in host.calls())
    assert any(Path(host.state["bucket_files_dir"]).glob("atp_files_*.enc"))
    check = host.run("restore-check")
    assert check.returncode == 0, check.all
    assert host.state["verified"][-1].startswith("/restore/atp_advance_trading_platform_")   # the copy fetched from R2
    assert "R2 backup" in check.stdout and "decrypts" in check.stdout and "restore-check: OK" in check.stdout
    assert "source=R2 status=ok" in (host.dir / ".deploy/restore-checks.log").read_text()


def test_health_reports_live_and_flags_read_only_and_fails_on_an_extra_public_port(host):
    host.write_env(extra={"LIVE_EXIT_IF_NO_STOP": "true"})
    assert host.run("deploy").returncode == 0
    host.state["flags_table"] = True; host.state["flags_json"] = json.dumps({"flags": {"byob_data_tier": {"on": True, "tenants": []}}}); host.save()
    before = len(host.state["calls"])
    res = host.run("health")
    assert res.returncode == 0, res.all
    assert "live_trading is ON by default" in res.stdout and "LIVE switches set in .env: LIVE_EXIT_IF_NO_STOP" in res.stdout
    assert "stored ON: byob_data_tier" in res.stdout and host.mutating(before) == []
    res = host.run("health", extra_env={"FAKE_SS": "LISTEN 0 4096 0.0.0.0:5432 0.0.0.0:*"})
    assert res.returncode == 1 and "public TCP ports besides 22/80/443: 5432" in res.stdout


def test_flags_changed_during_a_deploy_fail_the_run(host):
    host.state["flags_table"] = True; host.state["flags_json"] = json.dumps({"flags": {}}); host.save()
    assert host.run("deploy", host.sha_a).returncode == 0
    host.state["heads"][host.sha_b[:12]] = "rev2"; host.save()
    # a migration that touched the flags row: simulated by changing it when the guard runs
    fake = (host.bin / "docker").read_text().replace('st["databases"][db]["rev"] = st["heads"][rel]; finish()',
                                                       'st["databases"][db]["rev"] = st["heads"][rel]; st["flags_json"] = "{\\"flags\\": {\\"live_trading\\": {\\"on\\": true}}}"; finish()')
    (host.bin / "docker").write_text(fake)
    res = host.run("deploy")
    assert res.returncode == 1 and "feature flags CHANGED during the deploy" in res.stdout and "rollback" in res.stdout


def test_release_overlay_pins_images_to_the_commit():
    text = (ROOT / "deploy/compose.release.yml").read_text()
    for img in ("atp-backend", "atp-worker", "atp-research-worker", "atp-frontend"):
        assert f"image: {img}:${{ATP_RELEASE:-latest}}" in text
    assert text.count("pull_policy: never") == 4 and "MIGRATION_ALLOW_DB_AHEAD: ${MIGRATION_ALLOW_DB_AHEAD:-}" in text
    if not shutil.which("docker") or subprocess.run(["docker", "compose", "version"], capture_output=True).returncode:
        pytest.skip("docker compose not available")
    env = ROOT / "backend/.pytest_env_z11"
    env.write_text((ROOT / ".env.example").read_text() + "\nDOMAIN=203.0.113.7\nACME_EMAIL=ops@localhost\nCADDYFILE=Caddyfile.ip\n")
    try:
        out = subprocess.run(["docker", "compose", "--env-file", str(env), "-f", "docker-compose.yml", "-f", "docker-compose.prod.yml",
                              "-f", "docker-compose.hostinger.yml", "-f", "deploy/compose.release.yml", "-p", "atp", "config", "--format", "json"],
                             cwd=ROOT, capture_output=True, text=True, check=True, env={**os.environ, "ATP_RELEASE": "0123456789ab"}).stdout
    finally:
        os.unlink(env)
    services = json.loads(out)["services"]
    assert services["backend"]["image"] == "atp-backend:0123456789ab" and services["worker"]["image"] == "atp-worker:0123456789ab"
    assert services["worker"]["deploy"]["replicas"] == 1


def test_migrate_guard_starts_an_older_image_on_a_newer_database_only_when_told(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("migrate_guard_z11", ROOT / "backend/scripts/migrate_guard.py")
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)

    def ahead():
        guard._last_error = "FAILED: Can't locate revision identified by 'ffff'"
        return True
    calls = []
    monkeypatch.setattr(guard, "pending_migrations", ahead)
    monkeypatch.setattr(guard.subprocess, "call", lambda cmd: calls.append(cmd) or 0)
    monkeypatch.setattr("sys.argv", ["migrate_guard.py", "--force"])
    monkeypatch.delenv("MIGRATION_ALLOW_DB_AHEAD", raising=False)
    assert guard.main() == 4 and calls == []                                      # refuses: never runs upgrade on an unknown revision
    monkeypatch.setenv("MIGRATION_ALLOW_DB_AHEAD", "1")
    assert guard.main() == 0 and calls == []                                      # rollback mode: starts, migrates nothing


def test_secrets_key_check_counts_keys_that_do_not_open():
    import importlib.util

    from cryptography.fernet import Fernet
    spec = importlib.util.spec_from_file_location("check_secrets_key", ROOT / "backend/scripts/check_secrets_key.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    right, wrong = Fernet(Fernet.generate_key()), Fernet(Fernet.generate_key())
    wrapped = [right.encrypt(Fernet.generate_key()).decode() for _ in range(3)]
    assert mod.count_unwrappable(wrapped, right) == {"tenant_keys": 3, "open": 3, "do_not_open": 0}
    assert mod.count_unwrappable(wrapped + [wrong.encrypt(b"k").decode()], right)["do_not_open"] == 1
    assert mod.count_unwrappable([], right) == {"tenant_keys": 0, "open": 0, "do_not_open": 0}


def test_documented_one_off_backup_commands_override_the_service_entrypoint():
    """`compose run SERVICE sh /scripts/x.sh` keeps the backup/offsite entrypoint (the scheduler loop) and passes the
    command as its arguments, so the documented one-off never ran. Every documented command overrides the entrypoint."""
    for path in [ROOT / "docker-compose.yml", *ROOT.glob("docs/**/*.md")]:
        text = path.read_text(encoding="utf-8")
        assert "run --rm backup sh" not in text and "backup sh /scripts" not in text.replace("--entrypoint sh backup", ""), path
        assert "run --rm offsite sh /scripts/offsite_sync.sh" not in text, path
    runbook = (ROOT / "docs/deploy/HOSTINGER_RUNBOOK.md").read_text(encoding="utf-8")
    for command in ("/opt/atp/deploy/atp.sh install", "/opt/atp/deploy/atp.sh deploy", "/opt/atp/deploy/atp.sh health",
                    "/opt/atp/deploy/atp.sh rollback", "rollback --restore-db", "/opt/atp/deploy/atp.sh backup --verify",
                    "/opt/atp/deploy/atp.sh restore-check", "<SERVER_IP>", "Rollback rehearsal"):
        assert command in runbook, command
