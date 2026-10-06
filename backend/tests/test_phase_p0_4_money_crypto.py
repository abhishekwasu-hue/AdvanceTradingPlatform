"""P0.4: money columns stored as NUMERIC and read back as floats (S10); AES-256-GCM secrets bound to tenant and
column behind SECRETS_WRITE_FORMAT, every older format still readable, scrypt for passphrase masters (S11)."""
import asyncio
import base64
import hashlib
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import Numeric, select
from cryptography.fernet import Fernet

from app.core import config
from app.db.models import BrokerCredentialRecord, Money, Price, TradeRecord
from app.secrets_store import encryption, envelope
from tests.test_auth_api import _register, _session_factory, client


def _run(coro):
    return asyncio.run(coro)


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


# --- S10 -----------------------------------------------------------------------------------------------------------------
def test_money_and_price_columns_are_numeric_and_still_read_as_floats():
    assert isinstance(Money, Numeric) and (Money.precision, Money.scale, Money.asdecimal) == (18, 2, False)
    assert isinstance(Price, Numeric) and (Price.precision, Price.scale, Price.asdecimal) == (18, 4, False)
    cols = TradeRecord.__table__.c
    assert isinstance(cols.entry_price.type, Numeric) and cols.entry_price.type.scale == 4
    assert isinstance(cols.pnl.type, Numeric) and cols.pnl.type.scale == 2
    assert cols.quantity.type.__class__.__name__ == "Float"                      # fractional crypto units stay Float
    headers = _headers(_register("p04-money@example.com"))
    me = client.get("/api/auth/me", headers=headers).json()

    async def go():
        async with _session_factory() as session:
            trade = TradeRecord(tenant_id=me["tenant_id"], user_id=me["id"], mode="PAPER", symbol="NIFTY", strategy_id="s", direction="LONG",
                                entry_time=datetime.now(timezone.utc), entry_price=22512.1234, quantity=50, stop_loss=22400.0,
                                charges=13.37, pnl=1250.5)
            session.add(trade)
            await session.commit()
            await session.refresh(trade)
            assert isinstance(trade.entry_price, float) and isinstance(trade.pnl, float)
            assert trade.entry_price == 22512.1234 and trade.pnl + trade.charges == 1263.87
            total = await session.scalar(select(TradeRecord.pnl).where(TradeRecord.id == trade.id))
            assert isinstance(total, float)
    _run(go())


def test_numeric_migration_is_the_head_and_covers_every_numeric_column():
    import importlib.util
    from pathlib import Path
    from app.db.models import Base
    path = next(Path("alembic/versions").glob("c4d6e8f0a2b4_*.py"))
    source = path.read_text()
    assert "down_revision: Union[str, Sequence[str], None] = 'b3c5d7e9f1a3'" in source
    # Every Numeric column is created or converted by some migration (P0.4's conversion, or a later migration that
    # adds a Numeric column, e.g. P0.5's trades.mark_price).
    sources = [p.read_text() for p in Path("alembic/versions").glob("*.py")]
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, Numeric) and column.type.__class__.__name__ != "Float":
                assert any(f"'{column.name}'" in src and f"batch_alter_table('{table.name}')" in src for src in sources), (table.name, column.name)
    spec = importlib.util.spec_from_file_location("mig_p04", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "c4d6e8f0a2b4" and callable(module.upgrade) and callable(module.downgrade)


# --- S11 -----------------------------------------------------------------------------------------------------------------
def test_default_write_format_is_fernet_so_a_rollback_never_strands_a_secret(monkeypatch):
    monkeypatch.setattr(config, "SECRETS_WRITE_FORMAT", "fernet")
    headers = _headers(_register("p04-fmt@example.com"))
    tenant_id = client.get("/api/auth/me", headers=headers).json()["tenant_id"]
    token = encryption.encrypt_text("s", tenant_id, envelope.PURPOSE_BROKER_CREDENTIAL)
    assert token.startswith("t1:") and encryption.format_of(token) == "fernet"
    assert encryption.decrypt_text(token, envelope.PURPOSE_BROKER_CREDENTIAL) == "s"
    assert encryption.decrypt_text(token) == "s"                                   # purpose is only enforced for t2
    assert encryption.needs_rewrite(token) is False


def test_aesgcm_binds_tenant_and_purpose_and_every_older_format_still_reads(monkeypatch):
    monkeypatch.setattr(config, "SECRETS_WRITE_FORMAT", "aesgcm")
    a = client.get("/api/auth/me", headers=_headers(_register("p04-a@example.com"))).json()["tenant_id"]
    b = client.get("/api/auth/me", headers=_headers(_register("p04-b@example.com"))).json()["tenant_id"]
    token = encryption.encrypt_text('{"api_key":"k"}', a, envelope.PURPOSE_BROKER_CREDENTIAL)
    assert token.startswith(f"t2:{a}:broker_credential:") and '{"api_key"' not in token
    assert encryption.decrypt_text(token, envelope.PURPOSE_BROKER_CREDENTIAL) == '{"api_key":"k"}'
    # Same bytes under another column label: refused before any crypto.
    with pytest.raises(ValueError, match="belongs to"):
        encryption.decrypt_text(token, envelope.PURPOSE_ALERT_CHANNEL)
    with pytest.raises(ValueError):
        encryption.decrypt_text(token)                                             # a t2 token needs its purpose
    # Relabelled token (attacker rewrites the header): the associated data no longer matches.
    relabelled = token.replace(":broker_credential:", ":alert_channel:", 1)
    with pytest.raises(ValueError):
        encryption.decrypt_text(relabelled, envelope.PURPOSE_ALERT_CHANNEL)
    # Moved to another tenant's row (header rewritten): different key and different AAD.
    moved = token.replace(f"t2:{a}:", f"t2:{b}:", 1)
    with pytest.raises(ValueError):
        encryption.decrypt_text(moved, envelope.PURPOSE_BROKER_CREDENTIAL)
    # Copied *verbatim* into another organisation's row: the owner check refuses it (t2 and t1 alike).
    with pytest.raises(ValueError, match="belongs to organisation"):
        encryption.decrypt_text(token, envelope.PURPOSE_BROKER_CREDENTIAL, tenant_id=b)
    with pytest.raises(ValueError, match="belongs to organisation"):
        encryption.decrypt_text(f"t1:{a}:{envelope.key_ring[a].encrypt(b'x').decode()}", envelope.PURPOSE_MFA_SECRET, tenant_id=b)
    assert encryption.decrypt_text(token, envelope.PURPOSE_BROKER_CREDENTIAL, tenant_id=a) == '{"api_key":"k"}'
    # Two encryptions of the same plaintext never share a nonce/ciphertext.
    assert encryption.encrypt_text("x", a, "p") != encryption.encrypt_text("x", a, "p")
    # Older formats: Phase N tenant Fernet and pre-Phase-N master tokens.
    legacy_master = encryption.master_fernet().encrypt(b"old").decode()
    t1 = f"t1:{a}:{envelope.key_ring[a].encrypt(b'phase-n').decode()}"
    assert encryption.decrypt_text(legacy_master, envelope.PURPOSE_MFA_SECRET) == "old"
    assert encryption.decrypt_text(t1, envelope.PURPOSE_MFA_SECRET) == "phase-n"
    assert encryption.needs_rewrite(t1) is True and encryption.needs_rewrite(token) is False
    # Without a purpose the writer falls back to the Fernet format rather than producing an unbound AEAD token.
    assert encryption.encrypt_text("x", a).startswith("t1:")
    # Key not loaded -> clear error, never a silent master fallback on read.
    envelope.forget(b)
    try:
        with pytest.raises(ValueError, match="not loaded"):
            encryption.decrypt_text(moved, envelope.PURPOSE_BROKER_CREDENTIAL)
    finally:
        _run(_reload(b))


async def _reload(tenant_id):
    async with _session_factory() as session:
        await envelope.ensure_tenant_key(session, tenant_id)


def test_reencrypt_moves_rows_to_the_configured_format_and_status_reports_it(monkeypatch):
    headers = _headers(_register("p04-reenc@example.com"))
    me = client.get("/api/auth/me", headers=headers).json()
    tenant_id = me["tenant_id"]
    monkeypatch.setattr(config, "SECRETS_WRITE_FORMAT", "fernet")
    legacy = encryption.master_fernet().encrypt(json.dumps({"api_key": "legacy"}).encode()).decode()
    t1 = encryption.encrypt_text(json.dumps({"api_key": "phase-n"}), tenant_id, envelope.PURPOSE_BROKER_CREDENTIAL)

    async def go():
        async with _session_factory() as session:
            session.add(BrokerCredentialRecord(tenant_id=tenant_id, user_id=me["id"], broker_name="upstox", encrypted_payload=legacy))
            session.add(BrokerCredentialRecord(tenant_id=tenant_id, user_id=me["id"], broker_name="zerodha", encrypted_payload=t1))
            await session.commit()
            # fernet mode: only the legacy row moves.
            counts = await envelope.reencrypt_tenant(session, tenant_id)
            assert counts["broker_credentials"] == 1
            rows = {r.broker_name: r.encrypted_payload for r in await session.scalars(
                select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == tenant_id))}
            assert rows["upstox"].startswith("t1:") and rows["zerodha"] == t1
            # aesgcm mode: both move, and both still decrypt to their plaintext.
            monkeypatch.setattr(config, "SECRETS_WRITE_FORMAT", "aesgcm")
            counts = await envelope.reencrypt_tenant(session, tenant_id)
            assert counts["broker_credentials"] == 2
            rows = {r.broker_name: r.encrypted_payload for r in await session.scalars(
                select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == tenant_id))}
            assert all(v.startswith(f"t2:{tenant_id}:broker_credential:") for v in rows.values())
            assert json.loads(encryption.decrypt_text(rows["upstox"], envelope.PURPOSE_BROKER_CREDENTIAL))["api_key"] == "legacy"
            assert json.loads(encryption.decrypt_text(rows["zerodha"], envelope.PURPOSE_BROKER_CREDENTIAL))["api_key"] == "phase-n"
            assert (await envelope.reencrypt_tenant(session, tenant_id))["broker_credentials"] == 0      # idempotent
            report = await envelope.status(session)
            assert report["write_format"] == "aesgcm" and report["formats"]["aesgcm"] >= 2 and "pending_rewrite" in report
    _run(go())
    # The API path round-trips through the same helpers: credentials saved in aesgcm mode come back for the broker.
    monkeypatch.setattr(config, "SECRETS_WRITE_FORMAT", "aesgcm")
    saved = client.post("/api/broker/fyers/credentials", headers=headers, json={"api_key": "appid", "api_secret": "sec"})
    assert saved.status_code in (200, 201, 204), saved.text

    async def stored():
        async with _session_factory() as session:
            rec = await session.scalar(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == tenant_id,
                                                                            BrokerCredentialRecord.broker_name == "fyers"))
            return rec.encrypted_payload
    payload = _run(stored())
    assert payload.startswith("t2:") and json.loads(encryption.decrypt_text(payload, envelope.PURPOSE_BROKER_CREDENTIAL))["api_key"] == "appid"


def test_passphrase_master_scrypt_follows_the_rollout_and_sha256_wraps_always_open(monkeypatch):
    passphrase = "correct horse battery staple"
    sha_fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(passphrase.encode()).digest()))   # Phase N derivation
    scrypt_fernet = Fernet(base64.urlsafe_b64encode(hashlib.scrypt(passphrase.encode(), salt=b"atp-secrets-master-v2", n=2 ** 14, r=8, p=1, dklen=32)))
    old_token = sha_fernet.encrypt(b"wrapped-before-p0.4")
    # Default rollout (fernet): new wraps stay under SHA-256, so the previous image still opens them (rollback-safe).
    monkeypatch.setattr(config, "SECRETS_WRITE_FORMAT", "fernet")
    master = encryption.fernet_for_key(passphrase)
    assert master.decrypt(old_token) == b"wrapped-before-p0.4"
    assert sha_fernet.decrypt(master.encrypt(b"new")) == b"new"
    assert master.decrypt(scrypt_fernet.encrypt(b"from-later")) == b"from-later"      # and scrypt wraps already open
    # After the operator moves to aesgcm the scrypt key leads; SHA-256 wraps still open.
    monkeypatch.setattr(config, "SECRETS_WRITE_FORMAT", "aesgcm")
    master = encryption.fernet_for_key(passphrase)
    fresh = master.encrypt(b"new")
    assert scrypt_fernet.decrypt(fresh) == b"new" and master.decrypt(old_token) == b"wrapped-before-p0.4"
    with pytest.raises(Exception):
        sha_fernet.decrypt(fresh)
    # A real Fernet key is used as-is (the operator's 44-char key never changes meaning).
    real = Fernet.generate_key().decode()
    assert encryption._derive_fernet_keys(real) == (real.encode(),)
    assert encryption.fernet_for_key(real).decrypt(Fernet(real.encode()).encrypt(b"k")) == b"k"


def test_rotate_master_from_a_passphrase_to_a_fernet_key_rewraps_every_tenant_key(monkeypatch):
    from app.db.models import TenantKeyRecord
    headers = _headers(_register("p04-rotate@example.com"))
    tenant_id = client.get("/api/auth/me", headers=headers).json()["tenant_id"]
    passphrase = "old operator passphrase"
    sha_fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(passphrase.encode()).digest()))
    raw = envelope.raw_ring[tenant_id]

    async def go():
        async with _session_factory() as session:
            record = await session.get(TenantKeyRecord, tenant_id)
            record.wrapped_key = sha_fernet.encrypt(raw).decode()                   # as a Phase N passphrase deployment left it
            await session.commit()
            envelope.forget(tenant_id)
            with pytest.raises(ValueError, match="SECRETS_ENCRYPTION_KEY changed"):
                await envelope.ensure_tenant_key(session, tenant_id)                 # the current (test) master cannot open it
            assert await envelope.rotate_master(session, passphrase) >= 1          # old = passphrase (MultiFernet, two keys)
            await session.refresh(record)
            assert encryption.master_fernet().decrypt(record.wrapped_key.encode()) == raw
            assert envelope.raw_ring[tenant_id] == raw
            assert await envelope.rotate_master(session, passphrase) == 0           # re-run: nothing left under the old master
    _run(go())
    secret = encryption.encrypt_text("after-rotation", tenant_id, envelope.PURPOSE_MFA_SECRET)
    assert encryption.decrypt_text(secret, envelope.PURPOSE_MFA_SECRET, tenant_id) == "after-rotation"
