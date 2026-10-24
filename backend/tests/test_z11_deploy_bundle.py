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
    "POSTGRES_PASSWORD": "FAKEFAKE-pg-7c1d9e",
    "JWT_SECRET_KEY": "FAKEFAKE-jwt-55aa01",
    "SECRETS_ENCRYPTION_KEY": "FAKEFAKE-master-key-first",
    "METRICS_TOKEN": "FAKEFAKE-metrics-31f0",
    "BACKUP_ENCRYPTION_PASSPHRASE": "FAKEFAKE-backup-pass-9e2b",
}
R2 = {"OFFSITE_S3_ENDPOINT": "https://example-account.r2.cloudflarestorage.com", "OFFSITE_S3_ACCESS_KEY": "FAKEFAKE-r2-access-11aa",
      "OFFSITE_S3_SECRET_KEY": "FAKEFAKE-r2-secret-22bb"}

pytestmark = pytest.mark.skipif(not all(shutil.which(t) for t in ("bash", "git", "openssl", "tar", "flock", "sha256sum")),
                                reason="needs bash, git, openssl, tar, flock, sha256sum")

FAKE_DOCKER = r'''
import json, os, sys, time
STATE = os.environ["FAKE_STATE"]
with open(STATE) as fh:
    st = json.load(fh)
argv = sys.argv[1:]
st["calls"].append({"argv": argv, "release": os.environ.get("ATP_RELEASE", ""), "ahead": os.environ.get("MIGRATION_ALLOW_DB_AHEAD", ""),
                   "phase": os.environ.get("ATP_DEPLOYMENT_PHASE", "")})
rc = 0
out = []
APP = {"backend": "atp-backend", "worker": "atp-worker", "frontend": "atp-frontend", "research-worker": "atp-research-worker", "backup": "atp-backup"}
ALL = ["postgres", "redis", "backend", "worker", "frontend", "caddy", "backup", "offsite"]
rel = os.environ.get("ATP_RELEASE", "")
db = st["db_name"]

def finish():
    with open(STATE, "w") as fh:
        json.dump(st, fh)
    if out:
        sys.stdout.write("\n".join(out) + "\n")
    sys.exit(rc)

def dump_bytes(name):
    return "DUMPDATA:" + name + ("TAMPERED" if name in st.get("tampered", []) else "")

def backup_by_name(name):
    name = name.replace("/backups/", "").replace("/restore/", "")
    return next((b for b in st["backups"] if b["name"] == name or b["name"].endswith("/" + name) or name.endswith(b["name"])), None)

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

rest, i = [], 1
while i < len(argv):
    if argv[i] in ("-f", "-p"):
        i += 2; continue
    rest.append(argv[i]); i += 1
sub, args = rest[0], rest[1:]

def opts_and_services(a, with_value=("-e", "-v", "--entrypoint", "--format", "--tail", "-w", "--wait-timeout")):
    flags, pos, j = {}, [], 0
    while j < len(a):
        if pos and sub in ("run", "exec"):
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
        if svc == "caddy" and ("caddy" not in st["running"] or "--force-recreate" in flags):
            # Caddy reads the Caddyfile only when its container is created (a single-file bind mount)
            env = dict(l.split("=", 1) for l in open(".env").read().splitlines() if "=" in l and not l.startswith("#"))
            path = os.path.join("deploy", env.get("CADDYFILE") or "Caddyfile")
            st["caddy_conf"] = open(path).read() if os.path.exists(path) else ""
        st["running"][svc] = {"release": rel if svc in APP else "infra", "ahead": os.environ.get("MIGRATION_ALLOW_DB_AHEAD", "")}
    if "backend" in targets and st.get("seed_on_boot") and os.environ.get("ATP_DEPLOYMENT_PHASE") == "phase0":
        # the API's own Phase 0 first boot (app/platform/deployment_phase.py): absent entries only
        st["flags_table"] = True
        row = json.loads(st["flags_json"] or "{}")
        flags = row.setdefault("flags", {})
        for name, on in (("live_trading", False), ("phase0_paper_only", True)):
            flags.setdefault(name, {"on": on, "tenants": []})
        st["flags_json"] = json.dumps(row)
    finish()
if sub == "stop":
    for svc in args:
        st["running"].pop(svc, None)
    finish()
if sub == "ps" and "--services" in args:
    out.extend(st["running"]); finish()
if sub == "ps":
    for svc, info in st["running"].items():
        health = "healthy" if svc in ("postgres", "redis", "backend", "worker") else ""
        out.append(f"{svc}|running|{health}")
    finish()
if sub == "logs":
    flags, svcs = opts_and_services(args)
    if svcs and svcs[0] == "offsite":
        out.extend("2026-10-11T00:00:00Z offsite: synced to offsite:atp-backups" for _ in st["offsite_pushes"][-1:])
    finish()

def run_sql(target, sqls):
    global rc
    if st.get("db_down"):
        rc = 2; return
    for sql in sqls:
        low = sql.lower()
        if low.startswith("create database"):
            name = sql.split('"')[1]
            st["databases"][name] = {"rev": "-"}
            if "_rb_" in name and st.get("hold_scratch"):
                st["held"].append(name)
        elif low.startswith("drop database"):
            st["databases"].pop(sql.split('"')[1], None)
        elif low.startswith("begin;"):
            stmts = [s.strip() for s in sql.split(";") if s.strip().lower().startswith("alter database")]
            if st.get("swap_fail") or any(s.split('"')[1] in st["held"] for s in stmts):
                print("ERROR: database is being accessed by other users", file=sys.stderr); rc = 1; return
            for s in stmts:
                parts = s.split('"')
                st["databases"][parts[3]] = st["databases"].pop(parts[1])
        elif "pg_terminate_backend" in low:
            names = [n.strip(" '") for n in low.split("in (")[1].split(")")[0].split(",")]
            st["terminated"].append(names)
            st["held"] = [h for h in st["held"] if h.lower() not in names]
        elif low.startswith("show archive_mode"):
            out.append(st.get("archive_mode", "off"))
        elif "from pg_tables" in low:                                       # restore-offsite: is the database empty?
            if target not in st["databases"]:
                rc = 2; return
            d = st["databases"][target]
            out.append(str(d.get("tables", 0 if d["rev"] == "-" else 40)))
        elif "to_regclass('public.alembic_version')" in low:
            if target not in st["databases"]:
                rc = 2; return
            out.append("" if st["databases"][target]["rev"] == "-" else "alembic_version")
        elif "to_regclass('public.platform_controls')" in low:
            out.append("platform_controls" if st["flags_table"] else "")
        elif "alembic_version" in low:
            r = st["databases"].get(target, {}).get("rev", "-")
            if r == "-":
                rc = 1
            else:
                out.append(r)
        elif "select value_json from platform_controls" in low:
            if st["flags_json"] is not None:
                out.append(st["flags_json"])
        elif "md5(value_json)" in low:
            if st["flags_json"] is not None:
                import hashlib
                out.append(hashlib.md5(st["flags_json"].encode()).hexdigest())
        else:
            st.setdefault("unexpected_sql", []).append(sql); rc = 5

if sub == "exec":
    flags, pos = opts_and_services(args)
    svc, cmd = pos[0], pos[1:]
    if svc == "backend" and cmd[:3] == ["python", "-m", "app.option_chain.probe"]:   # B2b: probe-chain (own tests: test_b2_as_of_spot)
        st.setdefault("chain_probes", []).append(cmd[3:])
        out.append(f"probe-chain {' '.join(cmd[3:])}"); finish()
    if svc == "postgres" and cmd[:2] == ["sh", "-c"] and "/wal_archive" in cmd[2]:   # health: files and MB in the archive
        out.append(f"{st.get('wal_files', 0)} {st.get('wal_files', 0) * 16}"); finish()
    if svc == "postgres" and cmd[:1] == ["pg_basebackup"]:
        # piped into a `run` of the backup service: both processes hold the state, so this one leaves it alone (the
        # reader's write would lose it) and logs its argv beside it
        with open(STATE + ".basebackup", "a") as fh:
            fh.write(json.dumps(cmd) + "\n")
        sys.stdout.write("BASETAR"); sys.exit(0)
    if svc == "postgres":
        target, sqls = db, []
        for k in range(len(cmd) - 1):
            if cmd[k] == "-d":
                target = cmd[k + 1]
            elif cmd[k] == "-c":
                sqls.append(cmd[k + 1])
        if not sqls and "-c" not in cmd:                     # SQL on stdin (Z11: the verify role's one-run password)
            piped = sys.stdin.read().strip()
            if piped.lower().startswith("alter role"):
                st.setdefault("role_sql", []).append(piped)
                if st.get("role_login_fails") and " login " in f" {piped.lower()} ":
                    rc = 1
                finish()
        run_sql(target, sqls)
        finish()
    if svc == "caddy" and cmd == ["cat", "/etc/caddy/Caddyfile"]:
        if "caddy" not in st["running"]:
            rc = 1; finish()
        sys.stdout.write(st.get("caddy_conf", "")); finish()
    if svc == "backup" and cmd[:2] == ["cat", "/backups/LAST_BASE_BACKUP_OK"]:
        if st.get("last_base_ok"):
            out.append(st["last_base_ok"])
        else:
            rc = 1
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
        if cmd[:2] == ["python", "scripts/deploy_audit.py"]:              # A3: the untested deploy's audit row, reason on stdin
            st.setdefault("audit", []).append({"argv": cmd[2:], "reason": sys.stdin.readline().strip(), "release": rel})
            finish()
        if cmd[:2] == ["python", "scripts/check_secrets_key.py"]:
            out.append("secrets key: ok - 2 organisation data keys open"); finish()
        if cmd[:2] == ["python", "scripts/ops_check.py"]:                  # A2: the host facts arrive on stdin
            st["ops_check_input"] = json.loads(sys.stdin.read() or "{}")
            out.append("ops-check: disk=ok | 0 change(s) to report"); finish()
        if cmd[:2] == ["sh", "-c"] and "flag_posture.py" in cmd[2]:
            if st["posture"] is None:
                rc = 1
            else:
                out.append(json.dumps(st["posture"]))
            finish()
    rc = 6; finish()

if sub == "run":
    flags, pos = opts_and_services(args)
    svc, cmd = pos[0], pos[1:]
    entry = (flags.get("--entrypoint") or [""])[0]
    envs = dict(e.split("=", 1) if "=" in e else (e, os.environ.get(e, "")) for e in flags.get("-e", []))   # -e NAME: from the environment
    if svc == "worker" and cmd[:3] == ["python", "-m", "app.universe.run_now"]:   # WL1-4: lists-sync (its own tests: test_wl1_run_now)
        st.setdefault("lists_sync", []).append({"argv": cmd[3:], "release": rel})
        out.append("Files (HTTP status as seen from this server)" if "--check" in cmd else "Indices - 1 list(s)")
        rc = st.get("lists_sync_rc", 0); finish()
    if svc == "backend" and cmd[:4] == ["python", "-m", "alembic", "heads"]:
        out.append(f"{st['heads'][rel]} (head)"); finish()
    if svc == "backend" and cmd[:2] == ["python", "scripts/migrate_guard.py"]:
        if st["market_open"]:
            print("guard: REFUSING to migrate - NSE session is open", file=sys.stderr); rc = 3; finish()
        if st.get("migrate_fail"):
            st["databases"][db]["rev"] = "partial"; rc = 1; finish()
        st["databases"][db]["rev"] = st["heads"][rel]; finish()
    if svc == "backend" and cmd[:2] == ["sh", "-c"]:
        script = cmd[2]
        if "session_check.py" in script:
            if rel in st.get("no_session_script", []):
                rc = 1; finish()
            s = dict(st["session"])
            if "--orders-since" in cmd:
                s["orders_since"] = st["session"].get("orders_since", 0)
            out.append(json.dumps(s)); finish()
        if "migration_rollback_safety.py" in script:
            st["safety_checks"].append({"release": rel, "db": cmd[3], "head": cmd[4]})
            ok = st["rollback_safe"]
            out.append(json.dumps({"db": cmd[3], "target_head": cmd[4], "descendant": True, "unsafe": [] if ok else [cmd[3]]}))
            rc = 0 if ok else 1; finish()
        if "verify_chain" in script:
            st["chain_checked"].append(envs.get("ATP_VERIFY_DB"))
            import subprocess as sp                                  # the container's own sh: what DATABASE_URL / login it builds
            probe = sp.run(["sh", "-c", script.replace("python -m app.audit.verify_chain", """sh -c 'printf "%s|%s" "$DATABASE_URL" "${PGPASSWORD:-}"'""")],
                           env={"PATH": "/usr/bin:/bin", "DATABASE_URL": "postgresql+asyncpg://atp_user:app-db-pw@postgres:5432/" + db, **envs},
                           capture_output=True, text=True)
            st.setdefault("chain_login", []).append(probe.stdout if probe.returncode == 0 else None)
            rc = 1 if st.get("chain_broken") else 0; finish()
    if svc == "backup" and entry == "cat":
        path = cmd[0].replace("/backups/", "", 1)
        if path.endswith(".hmac"):
            mac = st["hmacs"].get(path[:-5])
            if mac is None:
                rc = 1
            else:
                out.append(mac)
            finish()
        sys.stdout.write(dump_bytes(path.split("/")[-1])); finish()
    if svc == "backup" and entry == "sh":
        if cmd[:1] == ["/scripts/backup.sh"]:
            name = f"atp_{db}_2026101{len(st['backups'])}T183000Z.dump.enc"
            st["backups"].append({"name": name, "rev": st["databases"][db]["rev"]})
            import hashlib, hmac                                    # backup.sh seals what it writes (common.sh hmac_file)
            key = hashlib.pbkdf2_hmac("sha256", os.environ["FAKE_BACKUP_PASS"].encode(), b"atpmacv1", 200000, 32)
            st["hmacs"][name] = hmac.new(key, (name + "\n" + dump_bytes(name)).encode(), "sha256").hexdigest()
            st["latest"] = name
            st["last_backup_ok"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            finish()
        if cmd[:2] == ["/scripts/base_backup.sh", "--stdin"]:
            assert sys.stdin.read() == "BASETAR"
            stamp = f"2026101{len(st.setdefault('base', []))}T040000Z"
            st["base"].append(stamp)
            import hashlib, hmac
            key = hashlib.pbkdf2_hmac("sha256", os.environ["FAKE_BACKUP_PASS"].encode(), b"atpmacv1", 200000, 32)
            name = f"base/{stamp}/base.tar.gz.enc"
            st["hmacs"][name] = "0" * 64 if st.get("base_bad_seal") else hmac.new(key, (name + "\n" + dump_bytes("base.tar.gz.enc")).encode(), "sha256").hexdigest()
            st["last_base_ok"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            finish()
        if cmd[:2] == ["-c", "readlink /backups/base/latest"]:
            if st.get("base"):
                out.append(st["base"][-1])
            finish()
        if cmd[:1] == ["-c"] and "find /wal_archive" in cmd[1]:
            if "-delete" in cmd[1]:
                st["wal_files"] = 0
            else:
                out.append(str(st.get("wal_files", 0)))
            finish()
        if cmd[:1] == ["/scripts/restore.sh"]:                              # restore-offsite: into the configured database
            assert envs.get("RESTORE_CONFIRM") == "yes"
            src = backup_by_name(cmd[1]) or {"rev": "-"}
            st["databases"][db] = {"rev": src["rev"]}
            st.setdefault("restored", []).append(cmd[1])
            finish()
        if cmd[:2] == ["-c", "readlink /backups/latest"]:
            if st.get("latest"):
                out.append(st["latest"])
            finish()
        if cmd[:1] == ["-c"] and "cat > " in cmd[1]:
            st["hmacs"][cmd[2]] = sys.stdin.read().strip(); finish()
        if cmd[:1] == ["-c"] and "/backups/pre-migration" in cmd[1] and "ln -f" in cmd[1]:
            src = backup_by_name(cmd[2])
            st["backups"].append({"name": "pre-migration/" + cmd[2], "rev": src["rev"]})
            if cmd[2] in st["hmacs"]:
                st["hmacs"]["pre-migration/" + cmd[2]] = st["hmacs"][cmd[2]]
            finish()
        if cmd[:1] == ["-c"] and "ls -1 /backups/pre-migration" in cmd[1]:
            out.extend(b["name"].split("/", 1)[1] for b in st["backups"] if b["name"].startswith("pre-migration/")); finish()
        if cmd[:1] == ["-c"] and "rm -f" in cmd[1] and "pre-migration" in cmd[1]:
            st["backups"] = [b for b in st["backups"] if b["name"] != "pre-migration/" + cmd[2]]; finish()
        if cmd[:1] == ["-c"] and "restore.sh" in cmd[1]:
            src = next(b for b in st["backups"] if b["name"] == cmd[2])
            st["databases"][envs["ATP_TARGET_DB"]] = {"rev": src["rev"]}
            finish()
        if cmd[:1] == ["/scripts/verify_backup.sh"]:
            target = cmd[1]
            name = st.get("latest") if target == "latest" else target.split("/")[-1]
            src = backup_by_name(name) or {"rev": "-"}
            st["verified"].append(target)
            live = st["databases"][db]["rev"]
            status = st["verify_status"] if src["rev"] == live else "schema_mismatch"
            scratch = None
            if envs.get("VERIFY_KEEP_SCRATCH") == "1":
                scratch = f"{db}_verify_{len(st['verified'])}"
                st["databases"][scratch] = {"rev": src["rev"]}
            out.append(json.dumps({"status": status, "file": name, "restored_into": scratch, "alembic": {"source": live, "copy": src["rev"]},
                                   "counts": {"tenants": {"source": 2, "copy": 2}, "orders": {"source": 5, "copy": 5}}, "audit_chain": "not_run", "notes": ""}))
            finish()
    if svc == "offsite":
        if cmd == ["--once"]:
            if st.get("offsite_fail"):
                rc = 1; finish()
            st["offsite_pushes"].append(len(st["backups"])); finish()
        if entry == "sh" and cmd[:1] == ["/scripts/offsite_prune.sh"]:
            st.setdefault("prunes", []).append(cmd[1:])
            rc = st.get("prune_rc", 0); finish()
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
            top = [b for b in st["backups"] + st.get("bucket_extra", []) if "/" not in b["name"]]
            top.sort(key=lambda b: b["name"])
            if envs.get("OFFSITE_REQUIRE_ENCRYPTED") == "1":
                top = [b for b in top if b["name"].endswith(".dump.enc")]
            name = top[-1]["name"]
            with open(os.path.join(st["restore_dir"], name), "w") as fh:
                fh.write(dump_bytes(name))
            if name in st["hmacs"]:
                with open(os.path.join(st["restore_dir"], name + ".hmac"), "w") as fh:
                    fh.write(st["hmacs"][name] + "\n")
            out.append("/restore/" + name); finish()
        if entry == "sh" and cmd[:1] == ["-c"] and "rclone lsf" in cmd[1]:
            bucket = st["bucket_files_dir"]
            names = sorted(f for f in os.listdir(bucket) if f.endswith(".enc")) if os.path.isdir(bucket) else []
            if names:
                for f in (names[-1], names[-1] + ".sha256", names[-1] + ".hmac"):
                    if os.path.exists(os.path.join(bucket, f)):
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
if url.startswith("https://api.github.com/"):        # A3: the public GitHub API the deploy gate reads (no token)
    gh = st["github"]
    gh["calls"].append(url)
    with open(os.environ["FAKE_STATE"], "w") as fh:
        json.dump(st, fh)
    assert not any(a.lower().startswith("authorization") for a in args), "the gate sends no token"
    if gh.get("down"):
        print("curl: (6) Could not resolve host: api.github.com", file=sys.stderr); sys.exit(6)
    path, _, query = url.split("/repos/", 1)[1].partition("?")
    repo, _, rest = path.partition("/actions/")
    q = dict(kv.split("=", 1) for kv in query.split("&") if "=" in kv)
    if repo != gh["repo"]:
        print("curl: (22) The requested URL returned error: 404", file=sys.stderr); sys.exit(22)
    def runs_for(sha):
        if sha in gh["commits"]:
            return gh["commits"][sha]
        return gh["default"] if gh["default"] is not None else []
    def public(run):
        return {k: v for k, v in run.items() if k != "jobs"}
    every = [r for sha in gh["commits"] for r in gh["commits"][sha]]
    if rest == "runs":
        print(json.dumps({"workflow_runs": [public(dict(r, head_sha=q["head_sha"])) for r in runs_for(q["head_sha"])]})); sys.exit(0)
    if rest.startswith("runs/") and rest.endswith("/jobs"):
        rid = int(rest.split("/")[1])
        run = [r for r in every + (gh["default"] or []) if r["id"] == rid][0]
        print(json.dumps({"jobs": [{"name": n, "status": "completed" if c not in ("in_progress", "queued") else c,
                                    "conclusion": None if c in ("in_progress", "queued") else c} for n, c in run["jobs"].items()]})); sys.exit(0)
    if rest.startswith("workflows/") and rest.endswith("/runs"):
        wf = rest.split("/")[1]
        ok = [dict(public(r), head_sha=sha) for sha in reversed(list(gh["commits"])) for r in gh["commits"][sha]
              if r["path"].endswith("/" + wf) and r["conclusion"] == q.get("status")]
        print(json.dumps({"workflow_runs": ok})); sys.exit(0)
    print("curl: (22) The requested URL returned error: 404", file=sys.stderr); sys.exit(22)
backend = st["running"].get("backend")
db_rev = st["databases"][st["db_name"]]["rev"]
up = backend is not None and backend["release"] not in st["bad_releases"]
if up:
    head = st["heads"].get(backend["release"])
    if db_rev not in (head, "-") and backend["ahead"] != db_rev:
        up = False          # an older image on a newer database starts only when told that exact revision
pub = st.get("public")
local = "--resolve" in args                          # through Caddy on the host; otherwise through public DNS (D1)
if pub is not None and url.startswith("https://") and "-D" in args:   # the edge headers (A4: the app page and the API)
    print("\r\n".join(["HTTP/2 200", *pub["headers"]]) + "\r\n" if pub["headers"] is not None else "", end=""); sys.exit(0)
if pub is not None and url.startswith("https://") and not (local and url.endswith("/api/system/health")):
    if "://www." in url:
        print(pub["www_local"] if local else pub["www"], end=""); sys.exit(0)
    print(pub["health"], end=""); sys.exit(0)
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
                      "worker": {"status": "ok", "seconds_since_heartbeat": 0}, "redis": {"status": "ok"}}}))
    sys.exit(0)
sys.exit(22)
'''


CI_JOBS = ("lint", "backend", "backend-lookahead", "perf", "frontend", "container-scan")
STACK_JOBS = ("gate", "acceptance", "load-500", "rollback", "offsite")


def ci_runs(base_id, ci=None, stack=None, ci_status="completed", stack_status="completed", only=None):
    """A3: what the GitHub API holds for one main commit - a CI run and a staging-stack run, every job passed unless
    `ci` / `stack` name other conclusions ({job: "failure" | "in_progress" | ...}); `only` keeps those workflows."""
    out = []
    for n, (wf, names, over, status) in enumerate((("ci.yml", CI_JOBS, ci, ci_status), ("staging-stack.yml", STACK_JOBS, stack, stack_status))):
        if only is not None and wf not in only:
            continue
        jobs = {j: "success" for j in names}
        jobs.update(over or {})
        bad = [c for c in jobs.values() if c not in ("success", "skipped", "in_progress", "queued")]
        out.append({"id": base_id * 10 + n, "path": f".github/workflows/{wf}", "status": status,
                    "conclusion": None if status != "completed" else ("failure" if bad else "success"), "jobs": jobs})
    return out


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
                      "bucket_files_dir": str(tmp / "bucket-files"), "restore_dir": str(self.restore_dir),
                      "hmacs": {}, "held": [], "terminated": [], "safety_checks": [], "chain_checked": [], "rollback_safe": True,
                      "session": {"open_sessions": [], "open_positions": 0, "orders_since": 0, "now_ist": "2026-10-11 20:00"},
                      # A3: every commit passed its checks on main unless a test says otherwise (ci_runs / Host.ci)
                      "github": {"repo": "example-owner/atp", "calls": [], "down": False, "commits": {}, "default": ci_runs(9)},
                      "posture": {"live_trading": {"on": False, "tenants": 0, "stored": True},
                                  "phase0_paper_only": {"known": True, "on": True, "stored": True}, "stored_on": ["phase0_paper_only"]}}
        self.save()
        py = sys.executable
        for name, body in (("docker", FAKE_DOCKER), ("curl", FAKE_CURL)):
            (self.bin / name).write_text(f"#!{py} -S\n{body}")       # -S: stdlib only, starts faster
        (self.bin / "ss").write_text("#!/bin/sh\n[ -n \"$FAKE_SS\" ] && { printf '%s\\n' \"$FAKE_SS\"; exit 0; }\n"
                                     "printf 'LISTEN 0 4096 0.0.0.0:22 0.0.0.0:*\\nLISTEN 0 4096 0.0.0.0:443 0.0.0.0:*\\n"
                                     "LISTEN 0 4096 127.0.0.1:8000 0.0.0.0:*\\n'\n")
        (self.bin / "df").write_text("#!/bin/sh\nprintf 'Filesystem 1024-blocks Used Available Capacity Mounted on\\n/dev/fake 100 40 60 40%% /\\n'\n")
        (self.bin / "crontab").write_text(f"#!/bin/sh\nF='{self.crontab}'\nif [ \"$1\" = -l ]; then [ -f \"$F\" ] && cat \"$F\" || exit 1; else cat > \"$F\"; fi\n")
        if os.getuid() == 0:   # the script refuses root; the CI runner is not root, this sandbox may be
            (self.bin / "id").write_text("#!/bin/sh\ncase \"$1\" in -u) echo 1000;; -un) echo atp;; *) exec /usr/bin/id \"$@\";; esac\n")
        for f in self.bin.iterdir():
            f.chmod(0o755)

    def write_env(self, extra=None, drop=(), mode=0o600):
        values = {"POSTGRES_USER": "atp_user", "POSTGRES_DB": "advance_trading_platform", "DOMAIN": "203.0.113.7",
                  "ACME_EMAIL": "ops@localhost", "CADDYFILE": "Caddyfile.ip", "OFFSITE_REMOTE": "offsite:atp-backups",
                  "OFFSITE_RESTORE_DIR": str(self.restore_dir), "OFFSITE_LOCAL_DIR": str(self.tmp / "offsite-local"),
                  "BACKUP_EXTRA_DIRS": "", "CI_GATE_REPO": "example-owner/atp", **SECRETS, **(extra or {})}
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

    def ci(self, sha, runs, default=False):
        """A3: set what the GitHub API says about `sha`; default=None stops passing every other commit."""
        self.load()
        self.state["github"]["commits"][sha] = runs
        if default is None:
            self.state["github"]["default"] = None
        self.save()

    def load(self):
        self.state = json.loads(self.state_path.read_text())
        return self.state

    def run(self, *args, extra_env=None, timeout=120):
        env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "FAKE_STATE": str(self.state_path), "ATP_DIR": str(self.dir),
               "ATP_HEALTH_TIMEOUT": "2", "ATP_POLL_SECONDS": "1", "ATP_FILES_BACKUP_DIR": str(self.files_dir), "HOME": str(self.tmp),
               "FAKE_BACKUP_PASS": SECRETS["BACKUP_ENCRYPTION_PASSPHRASE"], **(extra_env or {})}
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
        keys = (" up ", " build", " stop ", " run ", "image rm", "CREATE DATABASE", "ALTER DATABASE", "DROP DATABASE")
        read_only = ("session_check.py", "alembic heads", "migration_rollback_safety.py", "readlink /backups/latest", "--entrypoint cat",
                     "flag_posture.py", "ls -1 /backups/pre-migration")
        return [c for c in self.calls(since) if any(k in f" {c} " for k in keys) and not any(r in c for r in read_only)]


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
    order = [host.index(" build --pull"), host.index("up -d --wait postgres redis"), host.index("alembic heads"),
             host.index("/scripts/backup.sh"), host.index("scripts/migrate_guard.py"), host.index("up -d --no-deps --no-build backend"),
             host.index("up -d --no-build")]
    assert -1 not in order and order == sorted(order), calls
    assert host.state["databases"]["advance_trading_platform"]["rev"] == "rev1"
    assert host.state["running"]["worker"]["release"] == host.sha_b[:12] and host.state["running"]["backend"]["release"] == host.sha_b[:12]
    line = (host.dir / ".deploy/releases.log").read_text().split()
    dump = host.state["backups"][0]["name"]
    assert line[1:] == ["deploy", host.sha_b, "-", "pre-migration/" + dump, "-", "rev1"]   # kept out of the normal retention
    assert host.state["hmacs"][dump] == host.state["hmacs"]["pre-migration/" + dump] and len(host.state["hmacs"][dump]) == 64   # sealed
    assert host.index("up -d --no-deps --no-build worker") < host.index("up -d --no-build")      # the worker straight after the API
    assert (host.dir / ".deploy/current").read_text().strip() == host.sha_b
    fingerprint = (host.dir / ".deploy/secrets_key.fingerprint").read_text().strip()
    salt = (host.dir / ".deploy/key_salt").read_text().strip()
    assert len(fingerprint) == 64 and len(salt) == 32 and SECRETS["SECRETS_ENCRYPTION_KEY"] not in fingerprint
    assert fingerprint != hashlib.sha256(SECRETS["SECRETS_ENCRYPTION_KEY"].encode()).hexdigest()   # salted scrypt, not a bare hash
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
    host.write_env(extra={"SECRETS_ENCRYPTION_KEY": "FAKEFAKE-master-key-different"})
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
    assert host.state["running"]["backend"] == {"release": host.sha_a[:12], "ahead": "rev2"}   # previous image, DB-ahead mode for that exact revision
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
    assert host.state["running"]["backend"] == {"release": host.sha_a[:12], "ahead": "rev2"}
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
    before = len(host.state["calls"])
    res = host.run("health")
    assert res.returncode == 0, res.all
    assert "live_trading is off" in res.stdout and "phase0_paper_only is on" in res.stdout
    assert "LIVE switches set in .env: LIVE_EXIT_IF_NO_STOP" in res.stdout and "flags stored on: phase0_paper_only" in res.stdout
    assert host.mutating(before) == []
    res = host.run("health", extra_env={"FAKE_SS": "LISTEN 0 4096 0.0.0.0:5432 0.0.0.0:*"})
    assert res.returncode == 1 and "public TCP ports besides 22/80/443: 5432" in res.stdout


def test_live_reachable_is_an_owner_action_never_changed_by_the_script(host):
    assert host.run("deploy").returncode == 0
    host.state["posture"] = {"live_trading": {"on": True, "tenants": 0, "stored": False},
                             "phase0_paper_only": {"known": True, "on": False, "stored": False}, "stored_on": []}
    host.save()
    before = len(host.state["calls"])
    res = host.run("health")
    assert res.returncode == 10, res.all
    assert "OWNER ACTION NEEDED" in res.stdout and "turn live_trading OFF" in res.stdout and "turn phase0_paper_only ON" in res.stdout
    assert host.mutating(before) == [] and not host.state.get("unexpected_sql")       # read-only: no flag written
    host.state["posture"]["live_trading"] = {"on": False, "tenants": 2, "stored": True}
    host.state["posture"]["phase0_paper_only"] = {"known": True, "on": True, "stored": True}
    host.save()
    res = host.run("health")
    assert res.returncode == 10 and "for 2 organisation(s)" in res.stdout
    host.state["posture"]["live_trading"]["tenants"] = 0
    host.state["posture"]["phase0_paper_only"] = {"known": False, "on": False, "stored": False}   # a release without Z10-c
    host.save()
    assert host.run("health").returncode == 0


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


def test_phase0_first_boot_seed_is_the_one_flag_change_a_deploy_accepts(host):
    """Phase 0 deployment defaults: the API's first boot stores live_trading OFF / phase0_paper_only ON where no entry was
    stored - the deploy accepts exactly that (restrictive) change; any other value is still a failed deploy."""
    host.state["flags_table"] = True; host.state["flags_json"] = json.dumps({"flags": {"news_feed": {"on": True, "tenants": []}}})
    host.state["seed_on_boot"] = True; host.save()
    res = host.run("deploy")
    assert res.returncode == 0, res.all
    assert "except the Phase 0 first-boot seed of live_trading,phase0_paper_only" in res.stdout
    flags = json.loads(host.state["flags_json"])["flags"]
    assert flags == {"news_feed": {"on": True, "tenants": []}, "live_trading": {"on": False, "tenants": []},
                     "phase0_paper_only": {"on": True, "tenants": []}}
    assert {c["phase"] for c in host.state["calls"] if c["argv"][:2] == ["compose", "-f"]} == {"phase0"}   # no .env key needed
    again = host.run("deploy")                                                       # re-boot: nothing absent, nothing seeded
    assert again.returncode == 0 and "feature flags unchanged by the deploy" in again.stdout and "first-boot seed" not in again.stdout


def test_a_seed_shaped_change_to_an_existing_entry_still_fails_the_deploy(host):
    """Only ABSENT entries may be seeded: an owner's stored live_trading / phase0_paper_only is never overwritten."""
    host.state["flags_table"] = True
    host.state["flags_json"] = json.dumps({"flags": {"phase0_paper_only": {"on": False, "tenants": []}}}); host.save()
    assert host.run("deploy", host.sha_a).returncode == 0
    host.state["heads"][host.sha_b[:12]] = "rev2"; host.save()
    fake = (host.bin / "docker").read_text().replace(
        'st["databases"][db]["rev"] = st["heads"][rel]; finish()',
        'st["databases"][db]["rev"] = st["heads"][rel]; st["flags_json"] = "{\\"flags\\": {\\"phase0_paper_only\\": {\\"on\\": true, \\"tenants\\": []}}}"; finish()')
    (host.bin / "docker").write_text(fake)
    res = host.run("deploy")
    assert res.returncode == 1 and "feature flags CHANGED during the deploy" in res.stdout


_EDGE_HEADERS = ["Strict-Transport-Security: max-age=31536000; includeSubDomains", "X-Content-Type-Options: nosniff",
                 "X-Frame-Options: DENY", "Content-Security-Policy: default-src 'self'; frame-ancestors 'none'", "Referrer-Policy: no-referrer",
                 "Permissions-Policy: camera=()"]


def test_health_checks_the_public_domain_its_headers_and_the_www_redirect(host):
    """D1: with a real domain (CADDYFILE=Caddyfile) health checks, through Caddy here, the apex's security headers and the
    www redirect (FAIL: they come from the release's Caddyfile), and through public DNS the apex and www (WARN only:
    DNS and certificates are not part of a release, so they never fail a deploy or a rollback)."""
    good = "301 https://atp.example.com/atp-health-probe?x=1"
    host.write_env(extra={"DOMAIN": "atp.example.com", "CADDYFILE": "Caddyfile"})
    host.state["public"] = {"health": "200", "headers": _EDGE_HEADERS, "www": good, "www_local": good}
    host.save()
    assert host.run("deploy").returncode == 0
    res = host.run("health")
    assert res.returncode == 0 and "WARN" not in res.stdout.split("==> public HTTPS")[1].split("==>")[0], res.all
    assert "https://atp.example.com/api/system/health -> 200 over public DNS, certificate trusted" in res.stdout
    assert "sends HSTS, CSP" in res.stdout and "www.atp.example.com -> permanent redirect to https://atp.example.com" in res.stdout
    host.state["public"].update(www="000 ", www_local="000 ", health="000"); host.save()   # no www certificate, no hairpin
    res = host.run("health")
    assert res.returncode == 0, res.all                                                    # warnings, never a failed gate
    assert "no certificate for it yet" in res.stdout and "over public DNS -> 000" in res.stdout
    host.state["public"].update(www_local="200 ", headers=_EDGE_HEADERS[1:]); host.save()  # the release's Caddyfile is wrong
    res = host.run("health")
    assert res.returncode == 1 and "expected '301 https://atp.example.com/atp-health-probe?x=1'" in res.stdout
    assert "is missing: strict-transport-security" in res.stdout
    assert "https://atp.example.com/api/system/health is missing: strict-transport-security" in res.stdout  # A4: the API too
    host.state["public"].update(headers=[h for h in _EDGE_HEADERS if not h.startswith("Permissions")]); host.save()
    res = host.run("health")
    assert res.returncode == 1 and "is missing: permissions-policy" in res.stdout                          # A4
    host.state["public"].update(headers=[h.replace("; frame-ancestors 'none'", "") for h in _EDGE_HEADERS]); host.save()
    res = host.run("health")
    assert res.returncode == 1 and "is missing: csp-frame-ancestors" in res.stdout                         # A4
    host.state["public"].update(www_local=good, headers=None); host.save()                 # no answer: one clear failure
    res = host.run("health")
    assert res.returncode == 1 and "gave no answer, so its security headers could not be checked" in res.stdout
    assert "is missing" not in res.stdout


def test_caddy_is_recreated_whenever_it_runs_another_caddyfile_than_the_checkout(host):
    """D1 review: Caddy reads deploy/$CADDYFILE through a single-file bind mount, so the file it runs is compared with
    the checkout's on every deploy and rollback (not two releases' files: the release that brings a change is deployed
    by the previous atp.sh). Unchanged: Caddy is left alone. Stale - even on a converge - it is recreated, and until
    then health fails."""
    def recreations(since):
        return [c["argv"] for c in host.state["calls"][since:] if "--force-recreate" in c["argv"]]

    def release(text, message):
        (host.work / "deploy" / "Caddyfile.ip").write_text(text)
        _git(host.work, "add", "-A"); _git(host.work, "commit", "-q", "-m", message)
        sha = _git(host.work, "rev-parse", "HEAD"); _git(host.work, "push", "-q", "origin", "main")
        host.state["heads"][sha[:12]] = "rev1"; host.save()
        return sha
    _git(host.work, "checkout", "-q", "main")
    sha_c = release("https://{$DOMAIN} {\n\ttls internal\n}\n", "release C")
    sha_d = release("www.{$DOMAIN} {\n\tredir https://{$DOMAIN}{uri} permanent\n}\n", "release D: www")
    assert host.run("deploy", sha_c).returncode == 0                                     # first deploy: created from the file
    before = len(host.state["calls"])
    assert host.run("deploy", sha_c).returncode == 0 and recreations(before) == []       # unchanged: left alone
    res = host.run("deploy", sha_d)
    assert res.returncode == 0, res.all
    assert recreations(before) and recreations(before)[0][-1] == "caddy" and "recreating it so it reads this one" in res.stdout
    host.state["caddy_conf"] = "an older file\n"; host.save()                           # e.g. deployed by an older atp.sh
    res = host.run("health")
    assert res.returncode == 1 and "caddy runs an older Caddyfile than deploy/Caddyfile.ip" in res.stdout
    before = len(host.state["calls"])
    res = host.run("deploy", sha_d)                                                      # a converge repairs it
    assert res.returncode == 0 and recreations(before), res.all
    assert host.run("health").returncode == 0
    before = len(host.state["calls"])
    res = host.run("rollback")                                                           # back to C: its file again
    assert res.returncode == 0 and recreations(before), res.all


def test_health_on_the_ip_caddyfile_skips_the_public_domain_checks(host):
    """Recovery path: with CADDYFILE=Caddyfile.ip there is no public name to ask, so none of the D1 checks run."""
    assert host.run("deploy").returncode == 0
    res = host.run("health")
    assert res.returncode == 0, res.all
    assert "over public DNS" not in res.stdout and "www." not in res.stdout


def test_health_passes_on_a_phase0_install_with_nothing_for_the_owner_to_set(host):
    """The seeded posture (what a Phase 0 first boot leaves) is healthy: exit 0, no OWNER ACTION."""
    host.state["posture"] = {"live_trading": {"on": False, "tenants": 0, "stored": True},
                             "phase0_paper_only": {"known": True, "on": True, "stored": True}, "stored_on": ["phase0_paper_only"],
                             "deployment_phase": "phase0"}
    host.save()
    res = host.run("install")
    assert res.returncode == 0, res.all
    assert "OWNER ACTION" not in res.all and "deployment phase: phase0" in res.stdout
    res = host.run("health")
    assert res.returncode == 0 and "OWNER ACTION" not in res.all and "nothing for the owner to set" in res.stdout
    host.write_env(extra={"ATP_DEPLOYMENT_PHASE": "standard"})
    host.state["posture"]["deployment_phase"] = "standard"; host.save()
    before = len(host.state["calls"])
    res = host.run("health")
    assert res.returncode == 0 and "deployment phase: standard" in res.stdout and "not seeded" in res.stdout
    assert {c["phase"] for c in host.state["calls"][before:] if c["argv"][:2] == ["compose", "-f"]} == {"standard"}


def test_release_overlay_pins_images_to_the_commit():
    text = (ROOT / "deploy/compose.release.yml").read_text()
    for img in ("atp-backend", "atp-worker", "atp-research-worker", "atp-frontend"):
        assert f"image: {img}:${{ATP_RELEASE:-latest}}" in text
    assert text.count("pull_policy: never") == 5 and "dockerfile: deploy/backup.Dockerfile" in text and "MIGRATION_ALLOW_DB_AHEAD: ${MIGRATION_ALLOW_DB_AHEAD:-}" in text
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
    # Phase 0 deployment defaults: the API and the worker run as phase0 on this host without any .env key
    assert services["backend"]["environment"]["ATP_DEPLOYMENT_PHASE"] == "phase0" == services["worker"]["environment"]["ATP_DEPLOYMENT_PHASE"]


def test_migrate_guard_starts_an_older_image_on_a_newer_database_only_when_told_that_exact_revision(monkeypatch):
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
    for wrong in ("1", "true", "eeee"):
        monkeypatch.setenv("MIGRATION_ALLOW_DB_AHEAD", wrong)
        assert guard.main() == 4 and calls == []                                  # only the revision atp.sh checked is accepted
    monkeypatch.setenv("MIGRATION_ALLOW_DB_AHEAD", "ffff")
    assert guard.main() == 0 and calls == []                                      # checked rollback: starts, migrates nothing


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


def test_install_runs_bootstrap_checks_then_deploy_then_the_first_backup_and_restore_check(host):
    res = host.run("install")
    assert res.returncode == 0, res.all
    out = res.stdout
    steps = [out.index("bootstrap checks"), out.index("==> preflight"), out.index("deployed "), out.index("first backup + restore check"),
             out.index("restore into a scratch database"), out.index("install: done")]
    assert steps == sorted(steps)
    assert host.state["verified"] == ["latest"]                                     # R2 not configured: the local copy was verified
    shutil.rmtree(host.restore_dir)
    res = host.run("install")
    assert res.returncode == 10 and "run the bootstrap block as root" in res.all


# --- review follow-ups (H1-H2, M1-M5, L1-L7) --------------------------------------------------------------------

def _deploy_a_then_migrating_b(host):
    assert host.run("deploy", host.sha_a).returncode == 0
    host.state["heads"][host.sha_b[:12]] = "rev2"; host.save()
    assert host.run("deploy").returncode == 0


def test_redeploying_the_same_release_keeps_rollback_working(host):
    _deploy_a_then_migrating_b(host)
    again = host.run("deploy")                                                       # deploy B a second time
    assert again.returncode == 0, again.all
    kinds = [line.split()[1] for line in (host.dir / ".deploy/releases.log").read_text().splitlines()]
    assert kinds == ["deploy", "deploy", "converge"]
    res = host.run("rollback", "--restore-db", extra_env={"ATP_CONFIRM_DB": "advance_trading_platform"})   # A, B, B -> restore
    assert res.returncode == 0 and "rolled back to " + host.sha_a[:12] in res.stdout, res.all
    assert host.state["databases"]["advance_trading_platform"]["rev"] == "rev1"
    assert host.run("deploy").returncode == 0 and host.run("deploy").returncode == 0  # B (migrates again), B again
    res = host.run("rollback")                                                       # A, B, B -> code-only
    assert res.returncode == 0 and "rolled back to " + host.sha_a[:12] in res.stdout, res.all


def test_backup_verify_and_install_fail_when_the_verify_fails(host):
    assert host.run("deploy").returncode == 0
    host.state["verify_status"] = "count_too_low"; host.save()
    res = host.run("backup", "--verify")
    assert res.returncode == 1 and "backup: FAILED" in res.stdout
    res = host.run("install")
    assert res.returncode == 1 and "install: done" not in res.stdout


def test_swap_terminates_both_databases_and_renames_in_one_transaction(host):
    _deploy_a_then_migrating_b(host)
    host.state["hold_scratch"] = True; host.save()                                   # something connected to the restored copy
    res = host.run("rollback", "--restore-db", extra_env={"ATP_CONFIRM_DB": "advance_trading_platform"})
    assert res.returncode == 0, res.all
    terminated = host.state["terminated"][-1]
    assert "advance_trading_platform" in terminated and any("_rb_" in n for n in terminated)
    swap = next(c for c in host.calls() if "ALTER DATABASE" in c)
    assert "BEGIN;" in swap and "COMMIT;" in swap and swap.count("ALTER DATABASE") == 2


def test_a_failed_swap_restarts_the_release_that_was_running_on_the_unchanged_database(host):
    _deploy_a_then_migrating_b(host)
    host.state["swap_fail"] = True; host.save()
    res = host.run("rollback", "--restore-db", extra_env={"ATP_CONFIRM_DB": "advance_trading_platform"})
    assert res.returncode == 1 and "database swap failed (nothing renamed)" in res.all
    assert host.state["running"]["backend"]["release"] == host.sha_b[:12]           # B again, not A
    assert host.state["databases"]["advance_trading_platform"]["rev"] == "rev2"
    assert _git(host.dir, "rev-parse", "HEAD") == host.sha_b
    assert (host.dir / ".deploy/current").read_text().strip() == host.sha_b


def test_open_session_with_open_positions_blocks_the_worker_restart(host):
    _deploy_a_then_migrating_b(host)
    host.state["session"] = {"open_sessions": ["MCX"], "open_positions": 2, "orders_since": 0, "now_ist": "2026-10-11 21:10"}
    host.state["heads"]["latest"] = "rev2"; host.save()
    before = len(host.state["calls"])
    res = host.run("rollback")
    assert res.returncode == 3 and "MCX session open and 2 position(s) open" in res.all and "23:30" in res.all
    assert host.mutating(before) == [] and _git(host.dir, "rev-parse", "HEAD") == host.sha_b
    res = host.run("rollback", extra_env={"ATP_ALLOW_SESSION_RESTART": "1"})
    assert res.returncode == 0, res.all
    host.state["session"]["open_positions"] = 0; host.save()                           # open session, flat book: fine
    assert host.run("deploy").returncode == 0


def test_restore_db_refuses_to_orphan_positions_or_orders_without_the_typed_acknowledgement(host):
    _deploy_a_then_migrating_b(host)
    host.state["session"]["open_positions"] = 1; host.state["session"]["orders_since"] = 4; host.save()
    before = len(host.state["calls"])
    res = host.run("rollback", "--restore-db", extra_env={"ATP_CONFIRM_DB": "advance_trading_platform"})
    assert res.returncode == 1 and "not acknowledged" in res.all and "1 open position(s) and 4 order(s)" in res.all
    assert host.mutating(before) == []
    res = host.run("rollback", "--restore-db", extra_env={"ATP_CONFIRM_DB": "advance_trading_platform", "ATP_CONFIRM_ORPHANS": "ORPHAN"})
    assert res.returncode == 0, res.all


def test_code_only_rollback_is_refused_past_a_migration_that_is_not_rollback_safe(host):
    _deploy_a_then_migrating_b(host)
    host.state["rollback_safe"] = False; host.save()
    res = host.run("rollback")
    assert res.returncode == 1 and "not ROLLBACK_SAFE" in res.all and "--restore-db" in res.all
    assert host.state["safety_checks"][-1] == {"release": host.sha_b[:12], "db": "rev2", "head": "rev1"}   # the newer image decides
    assert host.state["running"]["backend"]["release"] == host.sha_b[:12]           # nothing restarted
    assert _git(host.dir, "rev-parse", "HEAD") == host.sha_b                         # checkout put back


def test_a_deploy_that_failed_after_migrating_is_logged_and_undone_by_restore_db(host):
    assert host.run("deploy", host.sha_a).returncode == 0
    host.state["heads"][host.sha_b[:12]] = "rev2"; host.state["bad_releases"] = [host.sha_b[:12]]; host.save()
    assert host.run("deploy").returncode == 1
    last = (host.dir / ".deploy/releases.log").read_text().splitlines()[-1].split()
    assert last[1:3] == ["failed", host.sha_b] and last[3] == host.sha_a and last[4].startswith("pre-migration/")
    res = host.run("rollback", "--restore-db", extra_env={"ATP_CONFIRM_DB": "advance_trading_platform"})
    assert res.returncode == 0, res.all
    assert host.state["databases"]["advance_trading_platform"]["rev"] == "rev1"
    assert host.state["running"]["backend"] == {"release": host.sha_a[:12], "ahead": ""}
    assert (host.dir / ".deploy/releases.log").read_text().splitlines()[-1].split()[1] == "restore"


def test_failures_before_the_restart_put_the_checkout_back(host):
    assert host.run("deploy", host.sha_a).returncode == 0
    host.state["heads"][host.sha_b[:12]] = "rev2"; host.state["market_open"] = True; host.save()
    assert host.run("deploy").returncode == 3
    assert _git(host.dir, "rev-parse", "HEAD") == host.sha_a
    host.state["market_open"] = False; host.state["migrate_fail"] = True; host.save()
    res = host.run("deploy")
    assert res.returncode == 1 and "migration failed" in res.all and _git(host.dir, "rev-parse", "HEAD") == host.sha_a
    assert (host.dir / ".deploy/releases.log").read_text().splitlines()[-1].split()[1] == "failed"


def test_an_unreadable_database_is_a_failure_not_unchanged(host):
    assert host.run("deploy").returncode == 0
    host.state["db_down"] = True; host.save()
    res = host.run("deploy")
    assert res.returncode == 1 and "cannot read the database revision" in res.all


def test_env_values_are_read_like_compose_reads_them(host):
    env = host.dir / ".env"
    text = env.read_text().replace("CADDYFILE=Caddyfile.ip", "CADDYFILE=Caddyfile.ip   # IP until DNS")
    text = text.replace(f"BACKUP_ENCRYPTION_PASSPHRASE={SECRETS['BACKUP_ENCRYPTION_PASSPHRASE']}",
                        f"BACKUP_ENCRYPTION_PASSPHRASE=\"{SECRETS['BACKUP_ENCRYPTION_PASSPHRASE']} #kept\"")
    env.write_text(text)
    real_pass = {"FAKE_BACKUP_PASS": SECRETS["BACKUP_ENCRYPTION_PASSPHRASE"] + " #kept"}   # what backup.sh seals with
    assert host.run("deploy", extra_env=real_pass).returncode == 0                   # the comment is not part of CADDYFILE
    assert host.run("backup", extra_env=real_pass).returncode == 0
    archive = sorted(host.files_dir.glob("atp_files_*.tar.gz.enc"))[-1]
    dec = subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-pass", "env:P", "-in", str(archive)],
                         env={**os.environ, "P": SECRETS["BACKUP_ENCRYPTION_PASSPHRASE"] + " #kept"}, capture_output=True)
    assert dec.returncode == 0                                                       # the quoted '#' is part of the passphrase


def test_a_tampered_backup_fails_its_seal(host):
    assert host.run("deploy").returncode == 0
    assert host.run("backup").returncode == 0
    host.state["tampered"] = [host.state["latest"]]; host.save()
    res = host.run("restore-check")
    assert res.returncode == 1 and "does not match its seal" in res.stdout
    archive = sorted(host.files_dir.glob("atp_files_*.tar.gz.enc"))[-1]
    data = bytearray(archive.read_bytes()); data[-1] ^= 1; archive.write_bytes(bytes(data))
    (archive.parent / (archive.name + ".sha256")).write_text(hashlib.sha256(bytes(data)).hexdigest() + "\n")   # sha256 recomputed
    host.state["tampered"] = []; host.save()
    res = host.run("restore-check")
    assert res.returncode == 1 and "does not match its seal (changed after it was written)" in res.stdout


def test_restore_check_runs_the_audit_chain_and_tolerates_a_backup_from_before_the_last_migration(host):
    assert host.run("deploy", host.sha_a).returncode == 0
    assert host.run("backup").returncode == 0                                         # dump at rev1
    host.state["heads"][host.sha_b[:12]] = "rev2"; host.save()
    assert host.run("deploy").returncode == 0                                         # pre-migration dump only lives under pre-migration/
    host.state["latest"] = [b["name"] for b in host.state["backups"] if "/" not in b["name"]][0]; host.save()
    res = host.run("restore-check")
    assert res.returncode == 0, res.all
    assert "predates the last migration" in res.stdout and "audit hash chain intact" in res.stdout
    assert host.state["chain_checked"] and host.state["chain_checked"][-1] not in host.state["databases"]   # scratch dropped
    host.state["chain_broken"] = True; host.save()
    assert host.run("restore-check").returncode == 1


def test_restore_check_reads_the_copy_as_the_restore_role_with_a_one_run_password(host):
    assert host.run("deploy").returncode == 0
    assert host.run("backup").returncode == 0
    res = host.run("restore-check")
    assert res.returncode == 0, res.all
    scratch = host.state["chain_checked"][-1]
    url, pw = host.state["chain_login"][-1].split("|")
    assert url == f"postgresql+asyncpg://atp_restore@postgres:5432/{scratch}"           # not the app / superuser login
    assert len(pw) == 48 and "app-db-pw" not in url
    login, nologin = host.state["role_sql"][-2:]
    assert login.startswith('ALTER ROLE "atp_restore" LOGIN NOSUPERUSER') and f"PASSWORD '{pw}'" in login
    assert nologin == 'ALTER ROLE "atp_restore" NOLOGIN PASSWORD NULL;'                  # NOLOGIN again right after
    assert not any(pw in a for c in host.state["calls"] for a in c["argv"])               # the password never reaches argv
    chain_call = next(c for c in host.calls() if "verify_chain" in c)
    assert "-e ATP_VERIFY_PW " in chain_call + " " and "ATP_VERIFY_PW=" not in chain_call
    assert "read as atp_restore" in res.stdout

    host.run("restore-check")                                                        # a second run: a new password each time
    assert host.state["chain_login"][-1].split("|")[1] != pw

    host.state["role_login_fails"] = True; host.save()                               # cannot log in as the role: fail, never superuser
    before = len(host.state["chain_checked"])
    res = host.run("restore-check")
    assert res.returncode == 1 and "not checked as the superuser" in res.stdout
    assert len(host.state["chain_checked"]) == before and "chain=not_checked" in (host.dir / ".deploy/last_restore_check").read_text()
    assert host.state["role_sql"][-1] == 'ALTER ROLE "atp_restore" NOLOGIN PASSWORD NULL;'   # a failed LOGIN is still undone
    assert "VERIFY_ROLE_LOGIN=1   # before the call" in ATP_SH.read_text()


def test_every_migration_declares_rollback_safety_and_the_lint_catches_risky_ones(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("migration_rollback_safety", ROOT / "backend/scripts/migration_rollback_safety.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.lint() == []                                                           # every committed migration declares it
    head = 'revision = "x1"\ndown_revision = "x0"\n'
    cases = {
        "drop.py": (head + "ROLLBACK_SAFE = True\n\ndef upgrade():\n    op.drop_column('t', 'c')\n", True),
        "rename.py": (head + "ROLLBACK_SAFE = True\n\ndef upgrade():\n    op.alter_column('t', 'c', new_column_name='d')\n", True),
        "notnull.py": (head + "ROLLBACK_SAFE = True\n\ndef upgrade():\n    op.add_column('t', sa.Column('c', sa.Integer(), nullable=False))\n", True),
        "sql.py": (head + "ROLLBACK_SAFE = True\n\ndef upgrade():\n    op.execute('ALTER TABLE t ALTER COLUMN c SET NOT NULL')\n", True),
        "missing.py": (head + "\ndef upgrade():\n    op.create_table('t')\n", True),
        "unique.py": (head + "ROLLBACK_SAFE = True\n\ndef upgrade():\n    op.create_unique_constraint('uq', 't', ['a'])\n", True),
        "unique_index.py": (head + "ROLLBACK_SAFE = True\n\ndef upgrade():\n    op.create_index('ix', 't', ['a'], unique=True)\n", True),
        "fk.py": (head + "ROLLBACK_SAFE = True\n\ndef upgrade():\n    op.create_foreign_key('fk', 't', 'u', ['a'], ['id'])\n", True),
        "drop_default.py": (head + "ROLLBACK_SAFE = True\n\ndef upgrade():\n    op.alter_column('t', 'c', server_default=None)\n", True),
        "rewrite.py": (head + "ROLLBACK_SAFE = True\n\ndef upgrade():\n    op.execute('UPDATE t SET c = 1')\n", True),
        "qualified_update.py": (head + "ROLLBACK_SAFE = True\n\ndef upgrade():\n    op.execute('UPDATE public.\"orders\" SET c = 1')\n", True),
        "non_literal.py": (head + "ROLLBACK_SAFE = True\nSQL = 'x'\n\ndef upgrade():\n    op.execute(SQL)\n", True),
        "batch_fk.py": (head + "ROLLBACK_SAFE = True\n\ndef upgrade():\n    with op.batch_alter_table('t') as batch_op:\n"
                        "        batch_op.create_foreign_key('fk', 'u', ['a'], ['id'])\n", True),
        "sql_constraint.py": (head + "ROLLBACK_SAFE = True\n\ndef upgrade():\n    op.execute('ALTER TABLE t ADD CONSTRAINT x UNIQUE (a)')\n", True),
        "new_table_keys.py": (head + "ROLLBACK_SAFE = True\n\ndef upgrade():\n    op.create_table('n', sa.Column('id', sa.Integer()))\n"
                              "    op.create_index('ix', 'n', ['id'], unique=True)\n    op.create_foreign_key('fk', 'n', 'u', ['id'], ['id'])\n", False),
        "additive.py": (head + "ROLLBACK_SAFE = True\n\ndef upgrade():\n    op.add_column('t', sa.Column('c', sa.Integer(), nullable=False, server_default='0'))\n"
                        "    op.create_table('u', sa.Column('id', sa.Integer(), nullable=False))\n\ndef downgrade():\n    op.drop_table('u')\n", False),
        "marked_unsafe.py": (head + "ROLLBACK_SAFE = False\n\ndef upgrade():\n    op.drop_table('t')\n", False),
        "reviewed.py": (head + "ROLLBACK_SAFE = True\nROLLBACK_SAFE_REASON = 'column unused since #120'\n\ndef upgrade():\n    op.drop_column('t', 'c')\n", False),
        # SQL built at run time: the literal parts ("UPDATE ", " SET c = 1") do not show what runs - review needed
        "fstring.py": (head + "ROLLBACK_SAFE = True\n\ndef upgrade():\n    for t in ('a', 'b'):\n        op.execute(f\"UPDATE {t} SET c = 1\")\n", True),
        "fstring_text.py": (head + "ROLLBACK_SAFE = True\n\ndef upgrade():\n    op.execute(sa.text(f\"DELETE FROM {T} WHERE x\"))\n", True),
        "format.py": (head + "ROLLBACK_SAFE = True\n\ndef upgrade():\n    op.execute('UPDATE {} SET c = 1'.format(T))\n", True),
        "percent.py": (head + "ROLLBACK_SAFE = True\n\ndef upgrade():\n    op.execute('UPDATE %s SET c = 1' % T)\n", True),
        "concat.py": (head + "ROLLBACK_SAFE = True\n\ndef upgrade():\n    op.execute('UPDATE ' + T + ' SET c = 1')\n", True),
        "fstring_select.py": (head + "ROLLBACK_SAFE = True\n\ndef upgrade():\n    op.execute(f\"SELECT create_hypertable('{t}', 'ts')\")\n", True),
        "fstring_reviewed.py": (head + "ROLLBACK_SAFE = True\nROLLBACK_SAFE_REASON = 'hypertables on tables made here'\n\ndef upgrade():\n"
                                "    op.execute(f\"SELECT create_hypertable('{t}', 'ts')\")\n", False),
        "fstring_plain.py": (head + "ROLLBACK_SAFE = True\n\ndef upgrade():\n    op.execute(f\"CREATE INDEX ix ON n (a)\")\n"
                             "    op.execute('CREATE INDEX ' 'iy ON n (b)')\n", False),   # no placeholder, implicit concatenation: literal
        # review LOW-6: any statement that is not ONE literal, exec_driver_sql too, and the helpers upgrade() calls
        "driver_sql.py": (head + "ROLLBACK_SAFE = True\n\ndef upgrade():\n    op.get_bind().exec_driver_sql(STATEMENT)\n", True),
        "driver_sql_update.py": (head + "ROLLBACK_SAFE = True\n\ndef upgrade():\n    op.get_bind().exec_driver_sql('UPDATE t SET c = 1')\n", True),
        "core_construct.py": (head + "ROLLBACK_SAFE = True\n\ndef upgrade():\n    op.get_bind().execute(t.update().values(c=1))\n", True),
        "helper_sql.py": (head + "ROLLBACK_SAFE = True\n\ndef _sql(t):\n    return 'UPDATE ' + t + ' SET c = 1'\n\n"
                          "def upgrade():\n    op.execute(_sql('t'))\n", True),
        "helper_drop.py": (head + "ROLLBACK_SAFE = True\n\ndef _inner():\n    op.drop_column('t', 'c')\n\ndef _outer():\n    _inner()\n\n"
                           "def upgrade():\n    _outer()\n", True),
        "helper_unused.py": (head + "ROLLBACK_SAFE = True\n\ndef _never_called():\n    op.drop_column('t', 'c')\n\n"
                             "def upgrade():\n    op.create_table('n', sa.Column('id', sa.Integer()))\n", False),
        "text_literal.py": (head + "ROLLBACK_SAFE = True\n\ndef upgrade():\n    op.execute(sa.text('CREATE INDEX ix ON n (a)'))\n"
                            "    op.get_bind().execute(text('SELECT 1').bindparams())\n", False),
        "text_update.py": (head + "ROLLBACK_SAFE = True\n\ndef upgrade():\n    op.get_bind().execute(sa.text('UPDATE t SET c = :v').bindparams(v=1))\n", True),
    }
    for name, (src, _) in cases.items():
        (tmp_path / name).write_text(src)
    flagged = {p.split(":")[0] for p in mod.lint(tmp_path)}
    assert flagged == {name for name, (_, bad) in cases.items() if bad}
    message = "\n".join(mod.lint(tmp_path))
    assert "missing.py: missing ROLLBACK_SAFE (Z11). Add ONE of these lines" in message and "ROLLBACK_SAFE = True     # the previous" in message
    assert "ROLLBACK_SAFE = False    # it does not" in message and "unsure means False" in message
    assert "fstring.py: ROLLBACK_SAFE = True but upgrade() has execute with SQL that is not a single literal (built at run time): review needed" in message
    assert "driver_sql.py: ROLLBACK_SAFE = True but upgrade() has exec_driver_sql with SQL that is not a single literal" in message
    assert "helper_drop.py: ROLLBACK_SAFE = True but upgrade() has drop_column" in message


# --- N1: only encrypted, sealed dumps are ever restored; the gate runs before Postgres / Redis are touched -----------

def test_restore_check_refuses_an_unsealed_r2_dump_and_never_picks_a_plain_one(host):
    host.write_env(extra=R2)
    assert host.run("deploy").returncode == 0
    assert host.run("backup").returncode == 0
    newest = host.state["latest"]
    host.state["bucket_extra"] = [{"name": "atp_advance_trading_platform_20261099T000000Z.dump", "rev": "rev1"}]   # newer, plain
    host.save()
    res = host.run("restore-check")
    assert res.returncode == 0, res.all
    fetch = next(c for c in host.calls() if "offsite_fetch.sh" in c)
    assert "-e OFFSITE_REQUIRE_ENCRYPTED=1" in fetch and host.state["verified"][-1] == "/restore/" + newest
    del host.state["hmacs"][newest]; host.save()                                     # stripped seal
    (host.restore_dir / (newest + ".hmac")).unlink(missing_ok=True)
    res = host.run("restore-check")
    assert res.returncode == 1 and "no seal - refused" in res.stdout
    assert host.state["verified"][-1] == "/restore/" + newest and len([v for v in host.state["verified"] if v == "/restore/" + newest]) == 1


def test_restore_db_refuses_a_pre_migration_dump_without_its_seal(host):
    _deploy_a_then_migrating_b(host)
    pre = [b["name"] for b in host.state["backups"] if b["name"].startswith("pre-migration/")][-1]   # the one B's migration took
    del host.state["hmacs"][pre]; host.save()
    before = len(host.state["calls"])
    res = host.run("rollback", "--restore-db", extra_env={"ATP_CONFIRM_DB": "advance_trading_platform"})
    assert res.returncode == 1 and "has no seal - refusing to restore it" in res.all
    assert not any("restore.sh" in c or "CREATE DATABASE" in c for c in host.calls(before))
    assert host.state["databases"]["advance_trading_platform"]["rev"] == "rev2"


def test_the_session_gate_runs_before_postgres_and_redis_are_touched(host):
    assert host.run("deploy", host.sha_a).returncode == 0
    before = len(host.state["calls"])
    assert host.run("deploy").returncode == 0
    gate, up = host.index("session_check.py", before), host.index("up -d --wait postgres redis", before)
    assert -1 < gate < up and host.index(" build --pull", before) < gate


# --- A1: the off-site backup (schedule, retention off-site, health ages, the WAL archive, the clean-machine restore) ---

def _ago(hours: float) -> str:
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - hours * 3600))


def test_deploy_schedules_a_nightly_verified_backup_and_a_weekly_restore_check_and_health_sees_it(host):
    assert host.run("deploy").returncode == 0
    cron = host.crontab.read_text()
    assert f"17 2 * * * {host.dir}/deploy/atp.sh backup --scheduled --verify >>" in cron
    assert f"43 3 * * 0 {host.dir}/deploy/atp.sh restore-check --scheduled >>" in cron
    assert "base-backup" not in cron                                                     # point-in-time recovery off
    res = host.run("health")
    assert "schedule installed: daily 02:17 backup + R2 copy + restore check, Sunday 03:43 restore check" in res.stdout
    host.write_env(extra={"PG_ARCHIVE_MODE": "on"})
    assert "outdated" in host.run("health").stdout or "older than this release" in host.run("health").stdout
    assert host.run("schedule").returncode == 0
    assert f"29 4 * * 6 {host.dir}/deploy/atp.sh base-backup --scheduled" in host.crontab.read_text()
    assert host.crontab.read_text().count("BEGIN atp.sh managed block") == 1
    host.crontab.write_text("MAILTO=\"\"\n")
    assert "no backup schedule in the crontab" in host.run("health").stdout


def test_health_shows_the_last_good_backup_local_and_off_site_and_fails_past_the_limit(host):
    host.write_env(extra=R2)
    assert host.run("deploy").returncode == 0
    assert host.run("backup").returncode == 0
    off = (host.dir / ".deploy/last_offsite_ok").read_text().split()
    assert off[1] == host.state["latest"]                                                # the newest dump that went to R2
    res = host.run("health")
    assert "last database backup" in res.stdout and "last off-site copy (R2)" in res.stdout and "(0 h ago)" in res.stdout, res.stdout
    status = host.run("backup-status")
    js = json.loads(status.stdout.strip().splitlines()[-1])
    assert status.returncode == 0 and js["status"] == "ok" and js["limit_hours"] == 26
    assert js["local"]["age_hours"] == 0 and js["offsite"]["configured"] is True and js["offsite"]["newest"] == host.state["latest"]

    host.state["last_backup_ok"] = _ago(30); host.save()                                 # the nightly dump stopped
    res = host.run("health")
    assert res.returncode == 1 and "FAIL  last database backup" in res.stdout and "30 h old (limit 26 h" in res.stdout
    status = host.run("backup-status")
    assert status.returncode == 1 and json.loads(status.stdout)["status"] == "fail" and "local backup 30 h old" in status.stdout
    host.write_env(extra={**R2, "BACKUP_MAX_AGE_HOURS": "48"})
    assert host.run("health").returncode == 0 and host.run("backup-status").returncode == 0

    host.write_env(extra=R2)
    (host.dir / ".deploy/last_offsite_ok").write_text(f"{_ago(40)} {host.state['latest']}\n")
    host.state["last_backup_ok"] = _ago(1); host.save()
    res = host.run("health")
    assert res.returncode == 1 and "FAIL  last off-site copy (R2)" in res.stdout
    deploy = host.run("deploy")                                                          # inside a deploy: a warning only
    assert deploy.returncode == 0 and "WARN  last off-site copy (R2)" in deploy.stdout, deploy.all


def test_backup_prunes_old_r2_copies_and_a_prune_failure_only_warns(host):
    host.write_env(extra=R2)
    assert host.run("deploy").returncode == 0
    res = host.run("backup")
    assert res.returncode == 0 and host.state["prunes"] == [[]] and "old R2 copies pruned" in res.stdout
    assert host.index("offsite --once") < host.index("/scripts/offsite_prune.sh")         # pruned after the copy
    assert "never inside the 14-day lock" in res.stdout and "WAL" not in res.stdout.split("off-site copy (R2)")[1].split("==>")[0]
    host.state["prune_rc"] = 1; host.save()
    res = host.run("backup")
    assert res.returncode == 0 and "pruning old R2 copies failed" in res.stdout
    host.state["offsite_fail"] = True; host.save()
    before = (host.dir / ".deploy/last_offsite_ok").read_text()
    res = host.run("backup")
    assert res.returncode == 1 and (host.dir / ".deploy/last_offsite_ok").read_text() == before   # a failed copy is not recorded


def test_no_backup_without_r2_never_prunes_and_status_warns(host):
    assert host.run("deploy").returncode == 0
    assert host.run("backup").returncode == 0
    assert "prunes" not in host.state and not (host.dir / ".deploy/last_offsite_ok").exists()
    status = host.run("backup-status")
    js = json.loads(status.stdout)
    assert status.returncode == 0 and js["status"] == "warn" and "off-site not configured" in js["problems"]


def test_the_wal_archive_left_from_before_is_reported_and_emptied_only_while_archiving_is_off(host):
    assert host.run("deploy").returncode == 0
    host.state["wal_files"] = 300; host.save()
    res = host.run("health")
    assert "WARN  WAL archiving is off, but 300 file(s) (4800 MB)" in res.stdout and "atp.sh wal-cleanup" in res.stdout
    host.state["archive_mode"] = "on"; host.save()
    refused = host.run("wal-cleanup")
    assert refused.returncode == 2 and "nothing removed" in refused.all and host.state["wal_files"] == 300
    host.state["archive_mode"] = "off"; host.save()
    dry = host.run("wal-cleanup", "--dry-run")
    assert dry.returncode == 0 and host.state["wal_files"] == 300
    res = host.run("wal-cleanup")
    assert res.returncode == 0 and "300 file(s) removed" in res.stdout and host.state["wal_files"] == 0
    assert "WAL archiving off (recovery point: the daily encrypted dump" in host.run("health").stdout
    assert "already empty" in host.run("wal-cleanup").stdout


def test_point_in_time_recovery_on_takes_an_encrypted_sealed_base_backup_and_health_watches_it(host):
    host.write_env(extra={**R2, "PG_ARCHIVE_MODE": "on"})
    off = host.run("deploy")
    assert off.returncode == 0
    host.state["archive_mode"] = "on"; host.save()
    assert "no base backup yet - run:" in host.run("health").stdout
    n = len(host.state["calls"])
    res = host.run("base-backup")
    assert res.returncode == 0 and "encrypted and sealed" in res.stdout, res.all
    (cmd,) = [json.loads(line) for line in Path(str(host.state_path) + ".basebackup").read_text().splitlines()]
    assert cmd[:3] == ["pg_basebackup", "-U", "atp_user"] and "-D" in cmd and cmd[cmd.index("-D") + 1] == "-" and "fetch" in cmd
    assert -1 < host.index("/scripts/base_backup.sh --stdin", n) < host.index("offsite --once", n)
    assert "WAL archiving on: last base backup" in host.run("health").stdout
    host.state["last_base_ok"] = _ago(24 * 9); host.save()
    assert "FAIL  WAL archiving on: the last base backup is 216 h old" in host.run("health").stdout
    host.state["base_bad_seal"] = True; host.save()
    assert host.run("base-backup").returncode == 1
    host.write_env(extra=R2)                                                             # off again in .env
    res = host.run("base-backup")
    assert res.returncode == 2 and "point-in-time recovery is off" in res.all


def test_escrow_lines_show_since_when_the_keys_are_unchanged_and_a_new_passphrase_is_said_once(host):
    assert host.run("deploy").returncode == 0
    res = host.run("health")
    assert "escrow: SECRETS_ENCRYPTION_KEY unchanged since" in res.stdout
    assert "escrow: BACKUP_ENCRYPTION_PASSPHRASE unchanged since" in res.stdout
    fp = (host.dir / ".deploy/backup_key.fingerprint").read_text()
    assert SECRETS["BACKUP_ENCRYPTION_PASSPHRASE"] not in fp and len(fp.strip()) == 64
    host.write_env(extra={"BACKUP_ENCRYPTION_PASSPHRASE": "FAKEFAKE-backup-pass-NEW-41"})
    assert "WARN  BACKUP_ENCRYPTION_PASSPHRASE changed since" in host.run("health").stdout
    res = host.run("backup", extra_env={"FAKE_BACKUP_PASS": "FAKEFAKE-backup-pass-NEW-41"})
    assert res.returncode == 0 and "need the OLD value" in res.stdout and "FAKEFAKE-backup-pass-NEW-41" not in res.all
    assert "passphrase changed" in (host.dir / ".deploy/backup_key.changes").read_text()
    assert "unchanged since" in host.run("health").stdout.split("escrow: BACKUP_ENCRYPTION_PASSPHRASE")[1][:20]


def _lost_server(host):
    """Deploy with R2 and back up; then the server is gone: a new, empty database, nothing but R2 and the .env."""
    host.write_env(extra=R2)
    assert host.run("deploy").returncode == 0
    assert host.run("backup").returncode == 0
    host.state["databases"] = {host.state["db_name"]: {"rev": "-"}}
    host.save()


def test_restore_offsite_fills_an_empty_database_from_r2_then_deploy_starts_on_it(host):
    _lost_server(host)
    res = host.run("restore-offsite", extra_env={"ATP_CONFIRM_DB": "advance_trading_platform"})
    assert res.returncode == 0, res.all
    assert host.state["databases"]["advance_trading_platform"]["rev"] == "rev1"
    assert host.state["restored"] == ["/restore/" + host.state["latest"]]
    assert "seal (HMAC) verified" in res.stdout and "restore-offsite: done" in res.stdout and "decrypts" in res.stdout
    assert host.index("up -d --wait postgres") < host.index("offsite_fetch.sh") < host.index("/scripts/restore.sh")
    assert "status=restored" in (host.dir / ".deploy/restore-checks.log").read_text()
    deploy = host.run("deploy")
    assert deploy.returncode == 0 and "schema at head - no migration" in deploy.stdout


def test_restore_offsite_never_replaces_data_and_needs_r2_the_right_passphrase_and_the_typed_name(host):
    _lost_server(host)
    host.state["databases"]["advance_trading_platform"] = {"rev": "rev1"}; host.save()
    res = host.run("restore-offsite", extra_env={"ATP_CONFIRM_DB": "advance_trading_platform"})
    assert res.returncode == 2 and "only fills an EMPTY database" in res.all and "restored" not in host.state
    host.state["databases"]["advance_trading_platform"] = {"rev": "-"}; host.save()
    res = host.run("restore-offsite")                                                    # no terminal, no ATP_CONFIRM_DB
    assert res.returncode == 1 and "not confirmed" in res.all and "restored" not in host.state
    host.write_env(extra={**R2, "BACKUP_ENCRYPTION_PASSPHRASE": "FAKEFAKE-not-the-escrowed-one"})
    res = host.run("restore-offsite", extra_env={"ATP_CONFIRM_DB": "advance_trading_platform"})
    assert res.returncode == 1 and "does not match its seal" in res.all and "restored" not in host.state
    host.write_env()
    res = host.run("restore-offsite", extra_env={"ATP_CONFIRM_DB": "advance_trading_platform"})
    assert res.returncode == 10 and "R2 is not configured" in res.all


def test_backup_status_fails_when_the_marker_vanishes_after_backups_have_run(host):
    assert json.loads(host.run("backup-status").stdout)["status"] == "warn"           # a fresh host: nothing yet
    assert host.run("deploy").returncode == 0 and host.run("backup").returncode == 0
    host.state["last_backup_ok"] = None; host.save()                                  # the backups volume is gone
    res = host.run("backup-status")
    assert res.returncode == 1 and "marker unreadable" in res.stdout
    (host.dir / ".deploy/last_restore_check").write_text(f"{_ago(1)} local restore_failed chain=not_run\n")
    host.state["last_backup_ok"] = _ago(1); host.save()
    res = host.run("backup-status")
    assert res.returncode == 1 and json.loads(res.stdout)["restore_check"]["status"] == "restore_failed"


# --- A2: the server checks (ops-check) --------------------------------------------------------------------------------

def test_ops_check_is_scheduled_and_hands_the_host_facts_to_the_backend(host):
    assert host.run("deploy").returncode == 0
    assert f"2-59/5 * * * * {host.dir}/deploy/atp.sh ops-check >> {host.dir}/.deploy/logs/ops-check.log 2>&1" in host.crontab.read_text()
    since = len(host.state["calls"])
    res = host.run("ops-check")
    assert res.returncode == 0, res.all
    assert "ops-check: disk=ok" in res.stdout
    facts = host.state["ops_check_input"]
    assert facts["disk_used_percent"] == 40 and facts["cert_note"] == "ip_only" and facts["cert_days_left"] is None
    assert facts["backup"]["status"] in ("ok", "warn", "fail") and "limit_hours" in facts["backup"]
    assert host.mutating(since) == []                                     # read-only on the host


def test_ops_check_skips_while_a_deploy_or_backup_holds_the_lock(host):
    import time
    assert host.run("deploy").returncode == 0
    host.state.pop("ops_check_input", None)
    host.save()
    holder = subprocess.Popen(["flock", str(host.dir / ".deploy" / "lock"), "sleep", "10"])
    try:
        time.sleep(0.5)
        res = host.run("ops-check")
    finally:
        holder.kill()
        holder.wait()
    assert res.returncode == 0 and "ops-check skipped" in res.stdout
    assert "ops_check_input" not in host.state


# --- A3: the deploy gate (review R3, RDY-9) -------------------------------------------------------------------------
def _gate_log(host):
    p = host.dir / ".deploy" / "gate.log"
    return p.read_text() if p.exists() else ""


def test_a_commit_whose_checks_failed_on_main_is_refused_before_anything_changes(host):
    host.ci(host.sha_a, ci_runs(1))
    host.ci(host.sha_b, ci_runs(2, ci={"backend": "failure"}), default=None)
    head = _git(host.dir, "rev-parse", "HEAD")
    res = host.run("deploy")
    assert res.returncode == 2, res.all
    assert "did not pass its checks: CI job 'backend' failure" in res.all and "Nothing was changed" in res.all
    assert f"The newest commit that passed: {host.sha_a[:12]}" in res.all and f"atp.sh deploy {host.sha_a[:12]}" in res.all
    assert "ATP_ALLOW_UNTESTED=1 ATP_UNTESTED_REASON=" in res.all                  # the emergency way is named
    assert host.mutating() == [] and _git(host.dir, "rev-parse", "HEAD") == head and not (host.dir / ".deploy/current").exists()
    assert f"refused {host.sha_b} fail" in _gate_log(host)
    assert all("api.github.com/repos/example-owner/atp/" in u for u in host.state["github"]["calls"])
    res = host.run("deploy", host.sha_a[:12])                                       # the one it named goes out
    assert res.returncode == 0, res.all
    assert f"{host.sha_a[:12]} passed every required check on main" in res.all and f"pass {host.sha_a}" in _gate_log(host)


def test_checks_still_running_or_never_run_refuse_with_a_plain_message(host):
    host.ci(host.sha_b, ci_runs(2, stack={"acceptance": "in_progress"}, stack_status="in_progress"), default=None)
    res = host.run("deploy")
    assert res.returncode == 2 and "the checks for" in res.all and "staging stack still running" in res.all
    assert "Run the same command again once they finish" in res.all and host.mutating() == []
    host.ci(host.sha_b, ci_runs(2, only=("staging-stack.yml",)))                   # main CI cancelled before it started
    res = host.run("deploy")
    assert res.returncode == 2 and "has no CI result on main" in res.all and host.mutating() == []


def test_the_known_red_load_test_does_not_block_and_the_list_is_a_setting(host):
    host.ci(host.sha_b, ci_runs(2, stack={"load-500": "failure"}), default=None)
    res = host.run("deploy")
    assert res.returncode == 0, res.all
    assert "known red on main, allowed by CI_GATE_KNOWN_RED: staging stack 'load-500'" in res.all
    host.write_env(extra={"CI_GATE_KNOWN_RED": ""})                               # the owner empties the list in .env
    res = host.run("deploy")
    assert res.returncode == 2 and "'load-500' failure" in res.all


def test_github_unreachable_refuses_by_default_and_the_override_needs_a_reason(host):
    host.load(); host.state["github"]["down"] = True; host.save()
    res = host.run("deploy")
    assert res.returncode == 1 and "GitHub could not be reached" in res.all and "Try again in a few minutes" in res.all
    assert host.mutating() == [] and len(host.state["github"]["calls"]) == 1
    host.state["github"]["calls"] = []; host.save()
    res = host.run("deploy", extra_env={"ATP_ALLOW_UNTESTED": "1", "ATP_UNTESTED_REASON": "   "})
    assert res.returncode == 2 and "needs a reason" in res.all and host.mutating() == []
    reason = "login page broken for every user; CI runner outage"
    res = host.run("deploy", extra_env={"ATP_ALLOW_UNTESTED": "1", "ATP_UNTESTED_REASON": reason + "\x1b"})
    assert res.returncode == 0, res.all
    assert f"WITHOUT checking its tests (ATP_ALLOW_UNTESTED=1) - reason: {reason}" in res.all
    assert f"untested {host.sha_b} {reason}" in _gate_log(host)                     # the deploy log
    assert host.state["audit"] == [{"argv": ["deploy_untested", host.sha_b], "reason": reason, "release": host.sha_b[:12]}]
    assert "the untested deploy is in the audit trail" in res.all
    assert host.state["github"]["calls"] == []                                      # the override asks nothing
    res = host.run("health")
    assert f"{host.sha_b[:12]} was deployed WITHOUT its tests passing" in res.all and reason in res.all
    assert "checks on main before each deploy" in host.run("releases").stdout
    host.load(); host.state["github"]["down"] = False; host.save()
    assert host.run("deploy").returncode == 0                                       # a checked converge clears the warning
    assert "deployed WITHOUT" not in host.run("health").all


def test_the_gate_needs_to_know_the_repository(host):
    host.write_env(drop=("CI_GATE_REPO",))                                          # origin here is a local path, not GitHub
    res = host.run("deploy")
    assert res.returncode == 2 and "set CI_GATE_REPO=owner/name in .env" in res.all and host.mutating() == []


def test_the_rehearsals_and_the_deploy_line_keep_to_the_gate():
    for rehearsal in ("rollback_rehearsal.sh", "offsite_rehearsal.sh"):
        text = (ROOT / "deploy" / "ci" / rehearsal).read_text()
        assert "ATP_ALLOW_UNTESTED=1 ATP_UNTESTED_REASON=" in text, rehearsal     # its commits have no CI result yet
    text = ATP_SH.read_text()
    assert text.index('ci_gate "$sha" "$ref"') < text.index('run git -C "$ATP_DIR" checkout -q --detach "$sha"')
    assert "ci_gate" not in text[text.index("cmd_rollback() {"):text.index("cmd_backup() {")]   # rollback is never gated
    template = (ROOT / ".github" / "pull_request_template.md").read_text()
    for line in ("**Migration:**", "**Rollback-safe:**", '/opt/atp/deploy/atp.sh deploy && /opt/atp/deploy/atp.sh health',
                 "## Self-review notes", "## Screenshots"):
        assert line in template


# --- B2b: probe-chain (the owner's read-only option chain spot probe, numbers only) -----------------------------------

def test_probe_chain_is_read_only_and_checks_its_input(host):
    assert host.run("deploy").returncode == 0
    since = len(host.state["calls"])
    res = host.run("probe-chain", "NIFTY")
    assert res.returncode == 0, res.all
    assert "probe-chain NIFTY" in res.stdout and host.state["chain_probes"] == [["NIFTY"]] and host.mutating(since) == []
    for args in (("NIFTY; rm -rf /",), (), ("NIFTY", "BANKNIFTY"), ("N$FTY",)):
        assert host.run("probe-chain", *args).returncode == 2, args
    assert len(host.state["chain_probes"]) == 1


# --- WL1-4: env-set (the allowlisted .env switches) and lists-sync (the universe sync + system lists NOW) ------------

def _env_text(host):
    return (host.dir / ".env").read_text()


def test_env_set_keeps_every_other_line_backs_up_first_and_is_idempotent(host):
    env = host.dir / ".env"
    env.write_text(_env_text(host) + "# owner note: keep me\nexport UNIVERSE_SYNC_ENABLED=false   # off until the terms\n")
    env.chmod(0o600)
    if os.getuid() == 0:
        os.chown(env, 1000, 1000)
    before = _env_text(host)
    res = host.run("env-set", "UNIVERSE_SYNC_ENABLED=true", "INSTRUMENT_SYNC_EXCHANGES=NSE,BSE")
    assert res.returncode == 0, res.all
    after = _env_text(host)
    assert "set        UNIVERSE_SYNC_ENABLED=true (was false)" in res.stdout and "added      INSTRUMENT_SYNC_EXCHANGES=NSE,BSE" in res.stdout
    kept = [ln for ln in before.splitlines() if "UNIVERSE_SYNC_ENABLED" not in ln]
    assert [ln for ln in after.splitlines() if "UNIVERSE_SYNC_ENABLED" not in ln and "INSTRUMENT_SYNC_EXCHANGES" not in ln] == kept
    assert "export UNIVERSE_SYNC_ENABLED=true\n" in after and after.endswith("INSTRUMENT_SYNC_EXCHANGES=NSE,BSE\n")
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    backups = sorted((host.dir / ".deploy" / "env-backups").iterdir())
    assert len(backups) == 1 and backups[0].read_text() == before and stat.S_IMODE(backups[0].stat().st_mode) == 0o600
    digest = host.env_digest()
    again = host.run("env-set", "UNIVERSE_SYNC_ENABLED=true", "INSTRUMENT_SYNC_EXCHANGES=NSE,BSE")
    assert again.returncode == 0 and ".env unchanged - nothing written" in again.stdout
    assert host.env_digest() == digest and len(list((host.dir / ".deploy" / "env-backups").iterdir())) == 1
    assert host.mutating() == []                                              # no container touched


def test_env_set_refuses_secrets_live_switches_and_odd_values_without_writing(host):
    digest = host.env_digest()
    for args in (("JWT_SECRET_KEY=abc",), ("LIVE_EXIT_IF_NO_STOP=true",), ("UNIVERSE_SYNC_ENABLED=true; rm -rf /",),
                 ("UNIVERSE_SYNC_ENABLED",), ()):
        res = host.run("env-set", *args)
        assert res.returncode == 2, (args, res.all)
        assert "rm -rf" not in res.all
    assert host.env_digest() == digest and not (host.dir / ".deploy" / "env-backups").exists()
    dry = host.run("--dry-run", "env-set", "UNIVERSE_SYNC_ENABLED=true")
    assert dry.returncode == 0 and "would be written" in dry.stdout and host.env_digest() == digest


def test_lists_sync_refuses_until_the_owner_switched_the_sync_on(host):
    assert host.run("deploy").returncode == 0
    since = len(host.state["calls"])
    res = host.run("lists-sync")
    assert res.returncode == 2 and "UNIVERSE_SYNC_ENABLED is not true" in res.all and "env-set UNIVERSE_SYNC_ENABLED=true" in res.all
    assert "lists_sync" not in host.state and host.mutating(since) == []


def test_lists_sync_runs_the_sync_in_the_release_image_then_restarts_api_and_worker(host):
    assert host.run("deploy").returncode == 0
    host.write_env(extra={"UNIVERSE_SYNC_ENABLED": "true", "INSTRUMENT_SYNC_EXCHANGES": "NSE,BSE"})
    since = len(host.state["calls"])
    res = host.run("lists-sync")
    assert res.returncode == 0, res.all
    assert "Indices - 1 list(s)" in res.stdout and "lists-sync: done" in res.stdout
    assert host.state["lists_sync"] == [{"argv": [], "release": host.sha_b[:12]}]
    gate, sync = host.index("session_check.py", since), host.index("app.universe.run_now", since)
    api, worker = host.index("up -d --no-deps --no-build backend", since), host.index("up -d --no-deps --no-build worker", since)
    assert -1 < gate < sync < api < worker


def test_lists_sync_check_only_reads_the_files_and_restarts_nothing(host):
    assert host.run("deploy").returncode == 0
    host.write_env(extra={"UNIVERSE_SYNC_ENABLED": "true"})
    since = len(host.state["calls"])
    host.state["lists_sync_rc"] = 1; host.save()                             # some file answered 404
    res = host.run("lists-sync", "--check")
    assert res.returncode == 0 and "some files were not fetched" in res.stdout
    assert host.state["lists_sync"] == [{"argv": ["--check"], "release": host.sha_b[:12]}]
    assert [c for c in host.mutating(since) if "run_now" not in c] == []


def test_lists_sync_respects_the_session_gate_and_a_failed_sync_restarts_nothing(host):
    assert host.run("deploy").returncode == 0
    host.write_env(extra={"UNIVERSE_SYNC_ENABLED": "true"})
    host.state["session"].update(open_sessions=["NSE"], open_positions=2); host.save()
    since = len(host.state["calls"])
    res = host.run("lists-sync")
    assert res.returncode == 3 and "lists_sync" not in host.state and host.mutating(since) == []
    host.state["session"].update(open_sessions=[], open_positions=0); host.state["lists_sync_rc"] = 1; host.save()
    since = len(host.state["calls"])
    res = host.run("lists-sync")
    assert res.returncode == 1 and "the sync did not finish" in res.all
    assert [c for c in host.mutating(since) if "run_now" not in c] == []
