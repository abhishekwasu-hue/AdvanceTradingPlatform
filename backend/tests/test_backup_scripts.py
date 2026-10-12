"""Phase E3: the backup/restore/verify scripts against a real Postgres. Skipped when pg_dump or
a migrated Postgres at DATABASE_URL is not available (the default unit-test run uses SQLite)."""
import json
import os
import shutil
import subprocess
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
        assert _psql("select rolsuper from pg_roles where rolname = 'atp_restore'") == "f"
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
