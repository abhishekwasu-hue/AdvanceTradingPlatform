"""Phase E3: the backup/restore/verify scripts against a real Postgres. Skipped when pg_dump or
a migrated Postgres at DATABASE_URL is not available (the default unit-test run uses SQLite)."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "backup"
DB_URL = os.environ.get("DATABASE_URL", "postgresql+asyncpg://atp_user:atp_dev_password@localhost:5432/advance_trading_platform")


def _postgres_ready() -> bool:
    if not DB_URL.startswith("postgresql") or not shutil.which("pg_dump") or not shutil.which("psql"):
        return False
    url = DB_URL.replace("postgresql+asyncpg://", "postgresql://")
    try:
        out = subprocess.run(["psql", url, "-qAtc", "select version_num from alembic_version"], capture_output=True, text=True, timeout=10)
    except Exception:  # noqa: BLE001
        return False
    return out.returncode == 0 and bool(out.stdout.strip())


pytestmark = pytest.mark.skipif(not _postgres_ready(), reason="needs pg_dump and a migrated Postgres at DATABASE_URL")


def _env(tmp_path: Path, **extra) -> dict:
    env = {**os.environ, "DATABASE_URL": DB_URL, "BACKUP_DIR": str(tmp_path), "BACKEND_DIR": str(Path(__file__).resolve().parents[1])}
    env.update(extra)
    return env


def test_backup_then_verify_roundtrip(tmp_path):
    run = subprocess.run(["sh", str(SCRIPTS / "backup.sh")], env=_env(tmp_path), capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    dumps = list(tmp_path.glob("atp_*.dump"))
    assert len(dumps) == 1 and (tmp_path / "latest").exists() and (tmp_path / "LAST_BACKUP_OK").exists()
    assert (tmp_path / (dumps[0].name + ".sha256")).read_text().strip() != ""

    verify = subprocess.run(["sh", str(SCRIPTS / "verify_backup.sh"), "latest"], env=_env(tmp_path), capture_output=True, text=True)
    assert verify.returncode == 0, verify.stderr + verify.stdout
    report = json.loads(verify.stdout.strip().splitlines()[-1])
    assert report["status"] == "ok" and report["audit_chain"] == "intact"
    assert report["alembic"]["source"] == report["alembic"]["copy"]
    assert report["counts"]["tenants"]["copy"] == report["counts"]["tenants"]["source"]


def test_encrypted_backup_needs_the_passphrase(tmp_path):
    env = _env(tmp_path, BACKUP_ENCRYPTION_PASSPHRASE="unit-test-passphrase")
    assert subprocess.run(["sh", str(SCRIPTS / "backup.sh")], env=env, capture_output=True, text=True).returncode == 0
    assert list(tmp_path.glob("atp_*.dump.enc")) and not list(tmp_path.glob("atp_*.dump"))

    ok = subprocess.run(["sh", str(SCRIPTS / "verify_backup.sh"), "latest"], env=env, capture_output=True, text=True)
    assert ok.returncode == 0 and json.loads(ok.stdout.strip().splitlines()[-1])["status"] == "ok"

    wrong = subprocess.run(["sh", str(SCRIPTS / "verify_backup.sh"), "latest"], env=_env(tmp_path, BACKUP_ENCRYPTION_PASSPHRASE="wrong"), capture_output=True, text=True)
    assert wrong.returncode == 1
    assert json.loads(wrong.stdout.strip().splitlines()[-1])["status"] == "restore_failed"


def test_tampered_backup_is_refused(tmp_path):
    assert subprocess.run(["sh", str(SCRIPTS / "backup.sh")], env=_env(tmp_path), capture_output=True, text=True).returncode == 0
    dump = next(tmp_path.glob("atp_*.dump"))
    with dump.open("ab") as fh:
        fh.write(b"tamper")
    verify = subprocess.run(["sh", str(SCRIPTS / "verify_backup.sh"), "latest"], env=_env(tmp_path), capture_output=True, text=True)
    assert verify.returncode == 1 and "sha256 mismatch" in verify.stderr


# --- Z11 / N1: only encrypted, sealed dumps are restored; the verify copy is restored by a non-superuser role ---------

def _psql(sql: str, db: str = "") -> str:
    url = DB_URL.replace("postgresql+asyncpg://", "postgresql://")
    if db:
        url = url.rsplit("/", 1)[0] + "/" + db
    return subprocess.run(["psql", url, "-qAtc", sql], capture_output=True, text=True, check=True).stdout.strip()


def test_every_encrypted_dump_is_sealed_and_restored_by_a_non_superuser_role(tmp_path):
    env = _env(tmp_path, BACKUP_ENCRYPTION_PASSPHRASE="unit-test-passphrase", VERIFY_KEEP_SCRATCH="1")
    assert subprocess.run(["sh", str(SCRIPTS / "backup.sh")], env=env, capture_output=True, text=True).returncode == 0
    dump = next(tmp_path.glob("atp_*.dump.enc"))
    assert len((tmp_path / (dump.name + ".hmac")).read_text().strip()) == 64
    ok = subprocess.run(["sh", str(SCRIPTS / "verify_backup.sh"), "latest"], env=env, capture_output=True, text=True)
    report = json.loads(ok.stdout.strip().splitlines()[-1])
    try:
        assert report["status"] == "ok", ok.stderr
        assert _psql("select tableowner from pg_tables where tablename = 'tenants'", report["restored_into"]) == "atp_restore"
        assert _psql("select rolsuper::text || rolcanlogin::text from pg_roles where rolname = 'atp_restore'") == "falsefalse"   # NOLOGIN again
    finally:
        _psql(f'DROP DATABASE IF EXISTS "{report["restored_into"]}"')


def test_unsealed_plain_and_tampered_seal_dumps_are_refused(tmp_path):
    env = _env(tmp_path, BACKUP_ENCRYPTION_PASSPHRASE="unit-test-passphrase")
    assert subprocess.run(["sh", str(SCRIPTS / "backup.sh")], env=env, capture_output=True, text=True).returncode == 0
    dump = next(tmp_path.glob("atp_*.dump.enc"))
    seal = tmp_path / (dump.name + ".hmac")
    original = seal.read_text()

    seal.unlink()                                                                  # unsealed
    run = subprocess.run(["sh", str(SCRIPTS / "verify_backup.sh"), "latest"], env=env, capture_output=True, text=True)
    assert run.returncode == 1 and "no .hmac seal" in run.stderr

    seal.write_text("0" * 64 + "\n")                                               # a seal that does not match
    run = subprocess.run(["sh", str(SCRIPTS / "verify_backup.sh"), "latest"], env=env, capture_output=True, text=True)
    assert run.returncode == 1 and "does not match its seal" in run.stderr
    seal.write_text(original)

    plain_env = _env(tmp_path / "plain")
    assert subprocess.run(["sh", str(SCRIPTS / "backup.sh")], env=plain_env, capture_output=True, text=True).returncode == 0
    plain = next((tmp_path / "plain").glob("atp_*.dump"))                           # a .dump without .enc, read with a passphrase set
    run = subprocess.run(["sh", str(SCRIPTS / "verify_backup.sh"), str(plain)], env=env, capture_output=True, text=True)
    assert run.returncode == 1 and "not an encrypted dump" in run.stderr


def test_a_sealed_dump_cannot_be_replayed_under_another_name(tmp_path):
    env = _env(tmp_path, BACKUP_ENCRYPTION_PASSPHRASE="unit-test-passphrase")
    assert subprocess.run(["sh", str(SCRIPTS / "backup.sh")], env=env, capture_output=True, text=True).returncode == 0
    dump = next(tmp_path.glob("atp_*.dump.enc"))
    newer = tmp_path / "atp_advance_trading_platform_20991231T000000Z.dump.enc"       # an old dump renamed to look newest
    for suffix in ("", ".sha256", ".hmac"):
        (tmp_path / (dump.name + suffix)).rename(tmp_path / (newer.name + suffix))
    run = subprocess.run(["sh", str(SCRIPTS / "verify_backup.sh"), str(newer)], env=env, capture_output=True, text=True)
    assert run.returncode == 1 and "does not match its seal" in run.stderr


# --- Z11: every read of the restored copy logs in as the restore role, never as the superuser ------------------------

_TRAP_SQL = """
CREATE TABLE seen (tag text, who text);
CREATE FUNCTION probe(tag text) RETURNS SETOF text LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO seen VALUES (tag, current_user);
  RETURN NEXT 'trap_rev';
END $$;
CREATE VIEW alembic_version AS SELECT probe('alembic') AS version_num;
CREATE VIEW tenants AS SELECT probe('count') AS id;
CREATE TABLE orders (id int); CREATE TABLE trades (id int); CREATE TABLE audit_logs (id int);
"""

_CHAIN_PROBE = """import os, subprocess
url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
who = subprocess.run(["psql", url, "-qAtc", "select current_user"], capture_output=True, text=True, check=True).stdout.strip()
with open(os.environ["CHAIN_PROBE_OUT"], "w") as fh:
    fh.write(os.environ["DATABASE_URL"] + "|" + who)
"""


def test_counts_alembic_and_audit_chain_on_the_copy_run_as_the_restore_role(tmp_path):
    # A dump whose `tenants` / `alembic_version` are views over a plpgsql function that records who called it: the
    # copy's function runs with the querying user's rights, so it must only ever see atp_restore.
    src_db = f"lowfix_trap_{os.getpid()}"
    _psql(f'DROP DATABASE IF EXISTS "{src_db}"'); _psql(f'CREATE DATABASE "{src_db}"')
    src_url = DB_URL.rsplit("/", 1)[0] + "/" + src_db
    backend = tmp_path / "backend"                                                  # a stand-in verify_chain that reports its login
    (backend / "app" / "audit").mkdir(parents=True)
    (backend / "app" / "audit" / "verify_chain.py").write_text(_CHAIN_PROBE)
    probe = tmp_path / "chain_probe.txt"
    report = {}
    try:
        subprocess.run(["psql", src_url.replace("postgresql+asyncpg://", "postgresql://"), "-q", "-v", "ON_ERROR_STOP=1", "-c", _TRAP_SQL],
                       check=True, capture_output=True, text=True)
        env = _env(tmp_path / "dumps", DATABASE_URL=src_url, VERIFY_KEEP_SCRATCH="1", BACKEND_DIR=str(backend), CHAIN_PROBE_OUT=str(probe))
        assert subprocess.run(["sh", str(SCRIPTS / "backup.sh")], env=env, capture_output=True, text=True).returncode == 0
        run = subprocess.run(["sh", str(SCRIPTS / "verify_backup.sh"), "latest"], env=env, capture_output=True, text=True)
        report = json.loads(run.stdout.strip().splitlines()[-1])
        assert report["status"] == "ok", run.stderr + run.stdout
        assert report["alembic"]["copy"] == "trap_rev" and report["counts"]["tenants"]["copy"] == 1
        seen = _psql("select tag || ':' || who from seen order by 1", report["restored_into"]).splitlines()
        assert sorted(set(seen)) == ["alembic:atp_restore", "count:atp_restore"]      # never the superuser
        url, who = probe.read_text().split("|")
        assert who == "atp_restore" and url.startswith("postgresql+asyncpg://atp_restore@") and ":" not in url.split("//", 1)[1].split("@")[0]
        assert report["audit_chain"] == "intact"
        assert _psql("select rolcanlogin::text from pg_roles where rolname = 'atp_restore'") == "false"   # NOLOGIN again
    finally:
        if report.get("restored_into"):
            _psql(f'DROP DATABASE IF EXISTS "{report["restored_into"]}"')
        _psql(f'DROP DATABASE IF EXISTS "{src_db}"')


def test_a_database_url_without_a_user_is_refused_rather_than_read_as_the_superuser(tmp_path):
    """Z11 review LOW-5: the copy is only ever read as the restore role. A URL with no user part (libpq takes PGUSER)
    cannot be rewritten to log in as atp_restore, so the verify stops before restoring anything."""
    from urllib.parse import urlparse
    u = urlparse(DB_URL.replace("postgresql+asyncpg://", "postgresql://"))
    env = _env(tmp_path, DATABASE_URL=f"postgresql://{u.hostname}:{u.port or 5432}{u.path}", PGUSER=u.username or "", PGPASSWORD=u.password or "")
    assert subprocess.run(["sh", str(SCRIPTS / "backup.sh")], env=env, capture_output=True, text=True).returncode == 0
    run = subprocess.run(["sh", str(SCRIPTS / "verify_backup.sh"), "latest"], env=env, capture_output=True, text=True)
    assert run.returncode == 1 and "names no user" in run.stderr and "restoring" not in run.stderr


def test_restore_rehearsal_from_the_off_site_copy_alone_into_a_clean_database(tmp_path):
    """A1 (review R2): the server is lost. With only the bucket and the escrowed passphrase: fetch the newest dump,
    check its seal, decrypt and restore it into a NEW empty database, then the copy matches and its audit chain holds.
    The bucket is a local directory behind a stand-in rclone (the CI staging job runs the same with real rclone against
    an S3 endpoint: deploy/ci/offsite_rehearsal.sh)."""
    from tests.test_a1_offsite_backup import FAKE_RCLONE
    bindir = tmp_path / "bin"; bindir.mkdir()
    (bindir / "rclone").write_text(FAKE_RCLONE); (bindir / "rclone").chmod(0o755)
    env = _env(tmp_path / "backups", BACKUP_ENCRYPTION_PASSPHRASE="FAKEFAKE-a1-rehearsal", PATH=f"{bindir}:{os.environ['PATH']}",
               FAKE_BUCKET=str(tmp_path / "bucket"), FAKE_RCLONE_LOG=str(tmp_path / "rclone.log"), OFFSITE_REMOTE="offsite:atp-backups")
    assert subprocess.run(["sh", str(SCRIPTS / "backup.sh")], env=env, capture_output=True, text=True).returncode == 0
    sync = subprocess.run(["sh", str(SCRIPTS / "offsite_sync.sh"), "--once"], env=env, capture_output=True, text=True)
    assert sync.returncode == 0, sync.stderr
    shutil.rmtree(tmp_path / "backups")                                               # the server and its disk are gone
    fetched = subprocess.run(["sh", str(SCRIPTS / "offsite_fetch.sh"), str(tmp_path / "restore")], env={**env, "OFFSITE_REQUIRE_ENCRYPTED": "1"},
                             capture_output=True, text=True)
    assert fetched.returncode == 0, fetched.stderr
    dump = fetched.stdout.strip().splitlines()[-1]
    assert dump.endswith(".dump.enc") and Path(dump + ".hmac").exists() and b"PGDMP" not in Path(dump).read_bytes()
    clean = f"a1_clean_{os.getpid()}"
    clean_url = DB_URL.rsplit("/", 1)[0] + "/" + clean
    _psql(f'DROP DATABASE IF EXISTS "{clean}"'); _psql(f'CREATE DATABASE "{clean}"')
    try:
        wrong = subprocess.run(["sh", str(SCRIPTS / "restore.sh"), dump, clean_url.replace("postgresql+asyncpg://", "postgresql://")],
                               env={**env, "BACKUP_ENCRYPTION_PASSPHRASE": "not-the-escrowed-value", "RESTORE_CONFIRM": "yes"}, capture_output=True, text=True)
        assert wrong.returncode == 1 and "does not match its seal" in wrong.stderr
        assert _psql("select count(*) from pg_tables where schemaname = 'public'", clean) == "0"   # nothing written
        ok = subprocess.run(["sh", str(SCRIPTS / "restore.sh"), dump, clean_url.replace("postgresql+asyncpg://", "postgresql://")],
                            env={**env, "RESTORE_CONFIRM": "yes"}, capture_output=True, text=True)
        assert ok.returncode == 0 and "seal verified" in ok.stderr, ok.stderr
        for sql in ("select version_num from alembic_version", "select count(*) from tenants", "select count(*) from audit_logs"):
            assert _psql(sql, clean) == _psql(sql), sql
        chain = subprocess.run([sys.executable, "-m", "app.audit.verify_chain"], cwd=Path(__file__).resolve().parents[1],
                               env={**os.environ, "DATABASE_URL": clean_url}, capture_output=True, text=True)
        assert chain.returncode == 0, chain.stdout + chain.stderr
    finally:
        _psql(f'DROP DATABASE IF EXISTS "{clean}"')
