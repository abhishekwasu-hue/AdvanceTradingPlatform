"""H-C1 e: llm_calls hygiene - personal data masked in the stored copy, old text scrubbed when the operator sets a
retention, the user's text cleared on erasure or "forget me"; rows, hashes and costs are never deleted."""
import asyncio
import dataclasses
import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.ai import metering, pii
from app.db.models import LlmCallRecord, User
from app.retention import service as retention
from app.retention.policy import load_policy
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner


def _run(coro):
    return asyncio.run(coro)


def test_personal_data_is_masked_and_market_figures_in_the_system_prompt_are_not():
    assert pii.redact("mail abhi.w@example.com or +91 98765 43210") == "mail [email] or [phone]"
    assert pii.redact("PAN ABCDE1234F, aadhaar 1234 5678 9012, a/c no: 123456789012") == "PAN [pan], aadhaar [aadhaar], a/c no: [account]"
    assert pii.redact("client id AB1234 asked") == "client id [account] asked"
    assert pii.redact("NIFTY 25200.50, 2 lots of 25000 CE") == "NIFTY 25200.50, 2 lots of 25000 CE"
    facts = "NIFTY volume 9876543210 turnover 123456789012"
    assert pii.redact(facts, strict=False) == facts                       # the system prompt keeps its figures
    assert pii.redact(None) is None


async def _log(tenant_id, user_id, user_text, when=None):
    async with _session_factory() as session:
        await metering.log_call(session, tenant_id=tenant_id, user_id=user_id, feature="copilot", provider="anthropic", model="m", prompt_version="v1",
                                system="FACTS volume 9876543210", user=user_text, response="Reply to x@y.com", status="ok", result=None)
        await session.commit()
        row = await session.scalar(select(LlmCallRecord).where(LlmCallRecord.user_id == user_id).order_by(LlmCallRecord.id.desc()).limit(1))
        if when is not None:
            row.created_at = when
            await session.commit()
        return row.id


async def _row(row_id):
    async with _session_factory() as session:
        return await session.get(LlmCallRecord, row_id)


def test_the_stored_copy_is_masked_and_the_hash_is_of_the_original():
    _, me = _owner("hc1e-log@example.com")
    question = "my number is 9876543210, email me at trader@example.com"
    row = _run(_row(_run(_log(me["tenant_id"], me["id"], question))))
    assert row.user_text == "my number is [phone], email me at [email]" and row.response_text == "Reply to [email]"
    assert row.system_text == "FACTS volume 9876543210"
    assert row.user_sha256 == hashlib.sha256(question.encode()).hexdigest()


def test_retention_scrubs_old_text_only_when_the_operator_sets_it():
    _, me = _owner("hc1e-retention@example.com")
    now = datetime.now(timezone.utc)
    old = _run(_log(me["tenant_id"], me["id"], "old question", when=now - timedelta(days=400)))
    new = _run(_log(me["tenant_id"], me["id"], "new question"))
    policy = load_policy()
    assert policy.llm_text_days == 0                                      # default: text is kept

    async def run(p):
        async with _session_factory() as session:
            return await retention.run_retention(session, now=now, policy=p)
    report = _run(run(policy))
    assert "llm_calls_text_scrubbed" not in report.deleted and _run(_row(old)).user_text == "old question"
    report = _run(run(dataclasses.replace(policy, llm_text_days=365)))
    assert report.deleted["llm_calls_text_scrubbed"] >= 1
    scrubbed, kept = _run(_row(old)), _run(_row(new))
    assert scrubbed is not None and scrubbed.user_text == scrubbed.system_text == scrubbed.response_text == "[expired]"
    assert scrubbed.user_sha256 == hashlib.sha256(b"old question").hexdigest() and scrubbed.cost_usd == 0.0
    assert kept.user_text == "new question"


def test_erasure_and_forget_me_clear_only_that_users_text():
    _, me = _owner("hc1e-erase@example.com")
    other_headers, other = _owner("hc1e-other@example.com")
    mine = _run(_log(me["tenant_id"], me["id"], "my question"))
    theirs = _run(_log(other["tenant_id"], other["id"], "their question"))

    async def erase():
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            await retention.erase_user(session, user, actor_id=None, reason="test")
            await session.commit()
    _run(erase())
    assert _run(_row(mine)).user_text == "[erased]" and _run(_row(theirs)).user_text == "their question"
    # "Forget everything the Copilot learnt" from the trader's own session does the same for their rows.
    assert client.delete("/api/ai/profile", headers=other_headers).status_code == 204
    gone = _run(_row(theirs))
    assert gone is not None and gone.user_text == "[erased]" and gone.response_text == "[erased]"


def test_an_age_scrub_keeps_the_erasure_marker():
    _, me = _owner("hc1e-markers@example.com")
    now = datetime.now(timezone.utc)
    old = _run(_log(me["tenant_id"], me["id"], "old question", when=now - timedelta(days=400)))

    async def go():
        async with _session_factory() as session:
            await retention.scrub_llm_text(session, LlmCallRecord.user_id == me["id"], "[erased]")
            await session.commit()
            again = await retention.scrub_llm_text(session, LlmCallRecord.id == old, "[expired]")
            await session.commit()
            return again
    assert _run(go()) == 0 and _run(_row(old)).user_text == "[erased]"
