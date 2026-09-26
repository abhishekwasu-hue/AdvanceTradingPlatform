"""Master prompt Section 48: "tamper-evident hash-chained audit logs". Covers the hash-chain
helper itself (app/audit/log.py) directly, and the SUPER_ADMIN-gated verification endpoint that
exposes it, including that tampering with a row is actually detected.
"""
import asyncio

from sqlalchemy import select

from app.audit.log import verify_audit_chain, write_audit_log
from app.auth.security import decode_access_token
from app.db.models import AuditLogRecord, User
from tests.test_auth_api import _register, _session_factory, client


def test_writes_form_a_valid_chain():
    async def _run():
        async with _session_factory() as session:
            await write_audit_log(session, 1, 1, "event_a", "first")
            await write_audit_log(session, 1, 1, "event_b", "second")
            await session.commit()
            intact, broken_id = await verify_audit_chain(session)
            assert intact is True
            assert broken_id is None

    asyncio.run(_run())


def test_a_row_chains_off_the_actual_previous_row_hash():
    """Doesn't assume this is the very first row in the whole (shared, cross-test) table - only
    that whatever chains off it correctly captures *some* real previous hash (`GENESIS` if this
    table happens to be empty so far, otherwise the true last row's hash), and that the new row's
    own `hash` isn't left empty.
    """
    async def _run():
        async with _session_factory() as session:
            previous_last = await session.scalar(
                select(AuditLogRecord).order_by(AuditLogRecord.id.desc()).limit(1)
            )
            expected_prev_hash = previous_last.hash if previous_last is not None else "GENESIS"

            record = await write_audit_log(session, 1, 1, "genesis_or_chain_check", "")
            await session.commit()

            assert record.prev_hash == expected_prev_hash
            assert record.hash != ""

    asyncio.run(_run())


def test_verify_endpoint_requires_super_admin():
    token = _register("nadia@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    response = client.get("/api/audit-logs/verify", headers=headers)
    assert response.status_code == 403


def test_verify_endpoint_reports_intact_chain_for_super_admin():
    token = _register("olga@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    async def _promote_to_super_admin():
        user_id = int(decode_access_token(token)["sub"])
        async with _session_factory() as session:
            user = await session.get(User, user_id)
            user.role = "SUPER_ADMIN"
            await session.commit()

    asyncio.run(_promote_to_super_admin())

    response = client.get("/api/audit-logs/verify", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["intact"] is True
    assert body["first_broken_row_id"] is None


def test_tampering_with_a_row_breaks_the_chain_from_that_point():
    """Runs last in this file (not just last-defined - pytest executes a module's tests in
    definition order with no randomization plugin installed here): it's the only test in the
    whole suite that deliberately corrupts a row, and does so in the same shared, cross-test
    audit_logs table every other test in this file (and test_audit_logs_api.py) reads from -
    running it earlier would make every intact-chain assertion after it fail for real, unrelated
    reasons.
    """
    async def _run():
        async with _session_factory() as session:
            await write_audit_log(session, 1, 1, "before_tamper", "")
            await write_audit_log(session, 1, 1, "will_be_tampered", "original detail")
            await write_audit_log(session, 1, 1, "after_tamper", "")
            await session.commit()

            intact, _ = await verify_audit_chain(session)
            assert intact is True

            tampered = await session.scalar(
                select(AuditLogRecord).where(AuditLogRecord.event == "will_be_tampered")
            )
            tampered.detail = "attacker-modified detail"
            await session.commit()

            intact, broken_id = await verify_audit_chain(session)
            assert intact is False
            assert broken_id == tampered.id

    asyncio.run(_run())
