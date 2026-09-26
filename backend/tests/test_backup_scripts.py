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
