"""A1 (production readiness): the off-site backup scripts, without a database or a bucket.

- backup.sh keeps the first dump of each week / month as a hard link in weekly/ and monthly/ (the seal covers the name,
  so the name is kept), the newest BACKUP_KEEP_WEEKLY / _MONTHLY of each; daily retention removes the seal too;
- offsite_sync.sh copies only encrypted, sealed files, the tiers to their own prefixes, and never the WAL archive;
- offsite_prune.sh keeps the newest N sets per prefix and everything inside the bucket lock (OFFSITE_LOCK_DAYS + 1),
  removes raw WAL left off-site, says what it would do under --dry-run and fails when a delete is refused;
- base_backup.sh --stdin encrypts and seals a base backup (no plaintext left), records its first WAL file and prunes
  the archive before the oldest kept one; pitr_restore.sh restores it only with a matching seal.

pg_dump and rclone are small stand-ins on PATH; openssl, tar and sh are the real ones (the scripts' own crypto runs).
"""
import io
import os
import re
import shutil
import subprocess
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts" / "backup"
PASS = "FAKEFAKE-a1-backup-passphrase"

pytestmark = pytest.mark.skipif(not all(shutil.which(t) for t in ("sh", "openssl", "tar", "gzip")), reason="needs sh, openssl, tar, gzip")

FAKE_PG_DUMP = """#!/bin/sh
for a; do case "$a" in --file=*) printf 'PGDMP fake dump %s' "$$" > "${a#--file=}" ;; esac; done
"""

# Enough rclone for the scripts, over a local directory standing in for the bucket ("remote:" = $FAKE_BUCKET).
# Filters follow rclone's rules for what the scripts use: a leading "/" anchors at the source root, "*" stays inside
# one path segment, "{a,b}" is either. Every call is logged.
FAKE_RCLONE = r'''#!/usr/bin/env python3
import os, re, shutil, sys, time
args = sys.argv[1:]
with open(os.environ["FAKE_RCLONE_LOG"], "a") as fh:
    fh.write(" ".join(args) + "\n")
bucket = os.environ["FAKE_BUCKET"]
def local(p):
    return os.path.join(bucket, p.split(":", 1)[1]) if ":" in p and not p.startswith("/") else p
def rx(pat):
    anchored = pat.startswith("/")
    out, i = "", 0
    pat = pat.lstrip("/")
    while i < len(pat):
        c = pat[i]
        if c == "*":
            out += "[^/]*"
        elif c == "{":
            j = pat.index("}", i)
            out += "(" + "|".join(re.escape(x) for x in pat[i + 1:j].split(",")) + ")"
            i = j
        else:
            out += re.escape(c)
        i += 1
    return re.compile(("^" if anchored else "^(.*/)?") + out + "$")
cmd, rest = args[0], args[1:]
inc = [rx(rest[k + 1]) for k, a in enumerate(rest) if a == "--include"]
pos = [a for k, a in enumerate(rest) if not a.startswith("-") and (k == 0 or rest[k - 1] not in ("--include", "--transfers", "--min-age"))]
fail = os.environ.get("FAKE_RCLONE_FAIL", "")
if fail and fail in " ".join(args):
    sys.exit(1)
if cmd == "copy":
    src, dst = pos[0], local(pos[1])
    for root, _, files in os.walk(src):
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), src)
            if inc and not any(r.match(rel) for r in inc):
                continue
            os.makedirs(os.path.dirname(os.path.join(dst, rel)), exist_ok=True)
            shutil.copy(os.path.join(root, f), os.path.join(dst, rel))
elif cmd == "lsf":
    d = local(pos[0])
    if not os.path.isdir(d):
        sys.exit(3)
    min_age = [rest[k + 1] for k, a in enumerate(rest) if a == "--min-age"]
    for name in sorted(os.listdir(d)):
        full = os.path.join(d, name)
        if "--dirs-only" in rest:
            if os.path.isdir(full):
                print(name + "/")
            continue
        if not os.path.isfile(full) or (inc and not any(r.match(name) for r in inc)):
            continue
        if min_age and time.time() - os.path.getmtime(full) < int(min_age[0].rstrip("d")) * 86400:
            continue
        print(name)
elif cmd == "deletefile":
    p = local(pos[0])
    if not os.path.isfile(p):
        sys.exit(4)
    os.remove(p)
elif cmd == "delete":
    shutil.rmtree(local(pos[0]))
elif cmd == "copyto":
    shutil.copy(local(pos[0]), local(pos[1]))
else:
    sys.exit(2)
'''


def _bin(tmp: Path) -> Path:
    b = tmp / "bin"
    b.mkdir(exist_ok=True)
    for name, body in (("pg_dump", FAKE_PG_DUMP), ("rclone", FAKE_RCLONE)):
        (b / name).write_text(body)
        (b / name).chmod(0o755)
    return b


def _env(tmp: Path, **extra) -> dict:
    return {**os.environ, "PATH": f"{_bin(tmp)}:{os.environ['PATH']}", "DATABASE_URL": "postgresql://atp_user:pw@localhost/atp",
            "BACKUP_DIR": str(tmp / "backups"), "BACKUP_ENCRYPTION_PASSPHRASE": PASS, "FAKE_BUCKET": str(tmp / "bucket"),
            "FAKE_RCLONE_LOG": str(tmp / "rclone.log"), "OFFSITE_REMOTE": "offsite:atp-backups", **extra}


def _sh(script: str, env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["sh", str(SCRIPTS / script), *args], env=env, capture_output=True, text=True)


def _stamp(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y%m%dT%H%M%SZ")


def _set(d: Path, name: str):
    d.mkdir(parents=True, exist_ok=True)
    for suffix in ("", ".sha256", ".hmac"):
        (d / (name + suffix)).write_text("x")


# --- backup.sh: the weekly / monthly tiers ------------------------------------------------------------------------

def test_the_first_dump_of_a_week_and_a_month_is_kept_in_its_tier_with_its_name_and_seal(tmp_path):
    env = _env(tmp_path)
    first = _sh("backup.sh", env)
    assert first.returncode == 0, first.stderr
    backups = tmp_path / "backups"
    (dump,) = [p for p in backups.glob("atp_*.dump.enc")]
    for tier in ("weekly", "monthly"):
        kept = backups / tier / dump.name
        assert kept.exists() and os.stat(kept).st_ino == os.stat(dump).st_ino            # a hard link, not a copy
        assert (backups / tier / (dump.name + ".hmac")).read_text() == (backups / (dump.name + ".hmac")).read_text()
        assert (backups / tier / "PERIOD").read_text().strip()
    assert "weekly: kept" in first.stderr and "monthly: kept" in first.stderr
    second = _sh("backup.sh", env)                                                       # the same week and month
    assert second.returncode == 0 and "weekly: kept" not in second.stderr
    assert len(list((backups / "weekly").glob("atp_*.dump.enc"))) == 1


def test_each_tier_keeps_its_newest_n_and_a_zero_count_turns_it_off(tmp_path):
    env = _env(tmp_path, BACKUP_KEEP_WEEKLY="4", BACKUP_KEEP_MONTHLY="0")
    weekly = tmp_path / "backups" / "weekly"
    for days in (60, 50, 40, 30, 20):
        _set(weekly, f"atp_atp_{_stamp(days)}.dump.enc")
    (weekly / "PERIOD").write_text("2000-W01\n")
    res = _sh("backup.sh", env)
    assert res.returncode == 0, res.stderr
    names = sorted(p.name for p in weekly.glob("atp_*.dump.enc"))
    assert len(names) == 4                                                               # the two oldest went
    assert not any(_stamp(60)[:8] in n or _stamp(50)[:8] in n for n in names) and any(_stamp(0)[:8] in n for n in names)
    assert all((weekly / (n + ".hmac")).exists() for n in names) and len(list(weekly.glob("*.hmac"))) == 4   # sidecars with them
    assert not (tmp_path / "backups" / "monthly").exists()


def test_daily_retention_removes_the_seal_with_the_dump(tmp_path):
    env = _env(tmp_path, BACKUP_RETENTION_DAYS="7", BACKUP_KEEP_MIN="1", BACKUP_KEEP_WEEKLY="0", BACKUP_KEEP_MONTHLY="0")
    backups = tmp_path / "backups"
    old = f"atp_atp_{_stamp(30)}.dump.enc"
    _set(backups, old)
    past = (datetime.now() - timedelta(days=30)).timestamp()
    for p in backups.iterdir():
        os.utime(p, (past, past))
    res = _sh("backup.sh", env)
    assert res.returncode == 0 and "retention: removing" in res.stderr, res.stderr
    assert not (backups / old).exists() and not (backups / (old + ".hmac")).exists() and not (backups / (old + ".sha256")).exists()
    assert len(list(backups.glob("atp_*.dump.enc.hmac"))) == 1                          # the new dump's seal stays


# --- offsite_sync.sh: what leaves the host -------------------------------------------------------------------------

def test_only_encrypted_sealed_files_leave_the_host_the_tiers_to_their_own_prefixes_and_never_the_wal(tmp_path):
    env = _env(tmp_path, WAL_ARCHIVE_DIR=str(tmp_path / "wal"))
    assert _sh("backup.sh", env).returncode == 0
    backups = tmp_path / "backups"
    (backups / "atp_atp_20260101T000000Z.dump").write_text("PGDMP plain")                 # a plain dump never goes
    _set(backups / "pre-migration", "atp_atp_20261001T000000Z.dump.enc")
    base = backups / "base" / "20261001T000000Z"
    _set(base, "base.tar.gz.enc")
    (base / "start_wal").write_text("000000010000000000000003\n")
    (base / "base.tar.gz").write_text("plain tarball")                                    # a plaintext base never goes
    (tmp_path / "wal").mkdir()
    (tmp_path / "wal" / "000000010000000000000003").write_text("raw WAL")
    res = _sh("offsite_sync.sh", env, "--once")
    assert res.returncode == 0, res.stderr
    bucket = tmp_path / "bucket" / "atp-backups"
    sent = sorted(str(p.relative_to(bucket)) for p in bucket.rglob("*") if p.is_file())
    dump = next(backups.glob("atp_*.dump.enc")).name
    assert f"backups/{dump}" in sent and f"backups/{dump}.hmac" in sent and f"backups/{dump}.sha256" in sent
    assert f"backups-weekly/{dump}" in sent and f"backups-monthly/{dump}.hmac" in sent
    assert "backups/pre-migration/atp_atp_20261001T000000Z.dump.enc.hmac" in sent
    assert "backups/base/20261001T000000Z/base.tar.gz.enc" in sent and "backups/base/20261001T000000Z/start_wal" in sent
    assert not any(s.endswith((".dump", "base.tar.gz", "LAST_BACKUP_OK", "PERIOD", "latest")) for s in sent), sent
    assert not any(s.startswith("wal_archive") or s.endswith("000000010000000000000003") for s in sent)
    assert str(tmp_path / "wal") not in (tmp_path / "rclone.log").read_text()
    for f in bucket.rglob("*"):
        assert not f.is_file() or b"PGDMP" not in f.read_bytes()                          # nothing in the clear


# --- offsite_prune.sh: retention off-site, the lock respected ----------------------------------------------------

def _bucket(tmp: Path) -> Path:
    b = tmp / "bucket" / "atp-backups"
    for days in range(0, 40, 2):                                                       # 20 daily sets over 40 days
        _set(b / "backups", f"atp_atp_{_stamp(days)}.dump.enc")
    for days in (5, 12, 19, 26, 33, 40, 47):
        _set(b / "backups-weekly", f"atp_atp_{_stamp(days)}.dump.enc")
    for days in (20, 50, 80, 110, 140, 170, 200, 230):
        _set(b / "backups-monthly", f"atp_atp_{_stamp(days)}.dump.enc")
    for days in (1, 10, 20, 30):
        _set(b / "backups" / "pre-migration", f"atp_atp_{_stamp(days)}.dump.enc")
    for days in (1, 8, 15, 22, 29, 36):
        stamp = _stamp(days)
        (b / "backups" / "base" / stamp).mkdir(parents=True)
        (b / "backups" / "base" / stamp / "base.tar.gz.enc").write_text("x")
    (b / "wal_archive").mkdir()
    for k in range(3):
        f = b / "wal_archive" / f"00000001000000000000000{k}"
        f.write_text("raw")
        past = (datetime.now() - timedelta(days=40)).timestamp()
        os.utime(f, (past, past))
    return b


def _names(d: Path):
    return sorted(p.name for p in d.glob("atp_*.dump.enc"))


def test_prune_keeps_the_newest_n_and_everything_inside_the_lock(tmp_path):
    b = _bucket(tmp_path)
    res = _sh("offsite_prune.sh", _env(tmp_path))
    assert res.returncode == 0, res.stderr
    daily = _names(b / "backups")
    # newest 7 kept, and nothing younger than max(retention 7 + 1, lock 14 + 1) = 15 days goes: days 0..14 stay (8 sets)
    assert len(daily) == 8 and all(n >= f"atp_atp_{_stamp(15)}" for n in daily)
    assert not any(p.name.endswith((".sha256", ".hmac")) and not (p.parent / p.name.rsplit(".", 1)[0]).exists() for p in (b / "backups").iterdir())
    assert len(_names(b / "backups-weekly")) == 4                                        # 4 newest (all older ones past 15 days)
    assert len(_names(b / "backups-monthly")) == 6                                       # 6 newest
    assert len(_names(b / "backups" / "pre-migration")) == 4                             # 6 kept: only 4 there
    assert len([d for d in (b / "backups" / "base").iterdir()]) == 4                     # BASE_BACKUP_KEEP
    assert not list((b / "wal_archive").iterdir())                                       # raw WAL off-site goes
    assert "backups: 20 off-site, kept 8, removed 12" in res.stderr


def test_a_longer_lock_keeps_everything_younger_than_it(tmp_path):
    b = _bucket(tmp_path)
    assert _sh("offsite_prune.sh", _env(tmp_path, OFFSITE_LOCK_DAYS="60")).returncode == 0
    assert len(_names(b / "backups")) == 20 and len(_names(b / "backups-weekly")) == 7     # all younger than 61 days
    assert len(_names(b / "backups-monthly")) == 6                                        # 230, 200 days old went


def test_dry_run_removes_nothing_and_a_refused_delete_fails_the_run(tmp_path):
    b = _bucket(tmp_path)
    before = sorted(str(p) for p in b.rglob("*"))
    dry = _sh("offsite_prune.sh", _env(tmp_path), "--dry-run")
    assert dry.returncode == 0 and "would remove" in dry.stderr
    assert sorted(str(p) for p in b.rglob("*")) == before
    refused = _sh("offsite_prune.sh", _env(tmp_path, FAKE_RCLONE_FAIL="deletefile offsite:atp-backups/backups-monthly"))
    assert refused.returncode == 1 and "could not remove" in refused.stderr
    assert len(_names(b / "backups")) == 8                                               # the rest was still pruned


def test_an_empty_bucket_is_nothing_to_prune_and_a_listing_error_fails(tmp_path):
    (tmp_path / "bucket" / "atp-backups").mkdir(parents=True)
    assert _sh("offsite_prune.sh", _env(tmp_path)).returncode == 0                        # every prefix: "directory not found"
    _bucket(tmp_path)
    res = _sh("offsite_prune.sh", _env(tmp_path, FAKE_RCLONE_FAIL="lsf offsite:atp-backups/files"))
    assert res.returncode == 1 and "could not list" in res.stderr


# --- base_backup.sh --stdin and pitr_restore.sh ----------------------------------------------------------------

def _basebackup_tar() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, body in (("backup_label", b"START WAL LOCATION: 0/3000028 (file 000000010000000000000003)\nCHECKPOINT LOCATION: 0/3000060\n"),
                           ("PG_VERSION", b"17\n"), ("pg_wal/000000010000000000000003", b"wal")):
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return buf.getvalue()


def test_a_base_backup_is_encrypted_sealed_and_prunes_the_wal_archive(tmp_path):
    b = _bin(tmp_path)
    (b / "pg_archivecleanup").write_text(f"#!/bin/sh\necho \"$@\" >> {tmp_path / 'cleanup.log'}\n")
    (b / "pg_archivecleanup").chmod(0o755)
    (tmp_path / "wal").mkdir()
    env = _env(tmp_path, WAL_ARCHIVE_DIR=str(tmp_path / "wal"), BASE_BACKUP_KEEP="2")
    old = tmp_path / "backups" / "base" / "20260101T000000Z"
    old.mkdir(parents=True)
    (old / "start_wal").write_text("000000010000000000000001\n")
    res = subprocess.run(["sh", str(SCRIPTS / "base_backup.sh"), "--stdin"], input=_basebackup_tar(), env=env, capture_output=True)
    assert res.returncode == 0, res.stderr
    stamp = os.readlink(tmp_path / "backups" / "base" / "latest")
    dest = tmp_path / "backups" / "base" / stamp
    assert sorted(p.name for p in dest.iterdir()) == [".complete", "base.tar.gz.enc", "base.tar.gz.enc.hmac", "base.tar.gz.enc.sha256", "start_wal"]
    assert b"backup_label" not in (dest / "base.tar.gz.enc").read_bytes()                # not a plain tarball
    assert (dest / "start_wal").read_text().strip() == "000000010000000000000003"
    assert (tmp_path / "cleanup.log").read_text().split() == [str(tmp_path / "wal"), "000000010000000000000001"]  # oldest kept
    assert (tmp_path / "backups" / "LAST_BASE_BACKUP_OK").exists()

    restored = tmp_path / "pitr"
    ok = _sh("pitr_restore.sh", env, "latest", "2026-10-23 11:00:00+05:30", str(restored))
    assert ok.returncode == 0, ok.stderr
    assert (restored / "backup_label").exists() and (restored / "recovery.signal").exists() and "seal verified" in ok.stderr
    assert "recovery_target_time = '2026-10-23 11:00:00+05:30'" in (restored / "postgresql.auto.conf").read_text()

    # the seal covers the directory: the same file under another stamp, or changed, is refused
    other = tmp_path / "backups" / "base" / "20991231T000000Z"
    shutil.copytree(dest, other)
    moved = _sh("pitr_restore.sh", env, str(other), "2026-10-23 11:00:00+05:30", str(tmp_path / "pitr2"))
    assert moved.returncode == 1 and "does not match its seal" in moved.stderr
    with open(dest / "base.tar.gz.enc", "ab") as fh:
        fh.write(b"x")
    (dest / "base.tar.gz.enc.sha256").write_text(subprocess.run(["sha256sum", str(dest / "base.tar.gz.enc")], capture_output=True, text=True).stdout.split()[0])
    changed = _sh("pitr_restore.sh", env, "latest", "2026-10-23 11:00:00+05:30", str(tmp_path / "pitr3"))
    assert changed.returncode == 1 and "does not match its seal" in changed.stderr


def test_a_stream_that_is_not_a_base_backup_leaves_nothing_behind(tmp_path):
    env = _env(tmp_path)
    for data in (b"", b"not gzip at all"):
        res = subprocess.run(["sh", str(SCRIPTS / "base_backup.sh"), "--stdin"], input=data, env=env, capture_output=True)
        assert res.returncode != 0
    assert not [d for d in (tmp_path / "backups" / "base").iterdir()]
    assert not (tmp_path / "backups" / "LAST_BASE_BACKUP_OK").exists()


def test_the_scripts_parse_and_no_backup_script_sends_the_wal_archive_off_site():
    for script in SCRIPTS.glob("*.sh"):
        assert subprocess.run(["sh", "-n", str(script)], capture_output=True).returncode == 0, script
        assert os.access(script, os.X_OK) or script.name == "offsite_fetch.sh", f"{script} must be executable"
    sync = (SCRIPTS / "offsite_sync.sh").read_text()
    assert not re.search(r"rclone (sync|copy)[^\n]*wal", sync, re.I)


def test_ci_rehearses_the_clean_machine_restore_with_the_real_deploy_bundle():
    import yaml
    script = ROOT / "deploy" / "ci" / "offsite_rehearsal.sh"
    assert os.access(script, os.X_OK) and subprocess.run(["bash", "-n", str(script)]).returncode == 0
    text = script.read_text()
    for step in ("backup --verify", "restore-offsite", "down -v --remove-orphans", "serve s3", "verify_chain", "ESCROW=("):
        assert step in text, step
    job = yaml.safe_load((ROOT / ".github/workflows/staging-stack.yml").read_text())["jobs"]["offsite"]
    assert job["needs"] == "gate" and any(s.get("run") == "deploy/ci/offsite_rehearsal.sh --down" for s in job["steps"])
