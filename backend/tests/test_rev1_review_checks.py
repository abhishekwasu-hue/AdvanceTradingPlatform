"""REV-1 `atp.sh review-checks` (docs/design/REV1_PLAN.md "Needs live check"): the ten read-only live checks print one
`ok|WARN|FAIL <name>: <reason>` line each, warn on the review's findings, write nothing, print no personal data, and a
check that breaks is one FAIL line while the others still run.
"""
import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, func, select

from app.db.models import LlmCallRecord, RiskSettingsRecord, TradeRecord, User
from app.platform import review_checks as rc
from tests.test_auth_api import _session_factory, client  # noqa: F401 - the app + database fixture module
from tests.test_trading_api import _register_user_and_get_id

BACKEND = Path(__file__).resolve().parents[1]
NOW = datetime.now(UTC)
LINE = re.compile(r"^(ok|WARN|FAIL) [a-z-]+: \S.*$")
NAMES = ["byob-flag", "shared-symbol-open", "trades-rows", "ai-read-words", "risk-zero", "members-per-org", "strategy-ids",
         "ai-one-key", "news-rows", "flags"]


def _run(coro):
    return asyncio.run(coro)


async def _lines():
    async with _session_factory() as session:
        return await rc.run_checks(session)


async def _count(model) -> int:
    async with _session_factory() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


def test_ten_checks_one_line_each_in_order():
    lines = _run(_lines())
    assert [name for _, name, _ in lines] == NAMES
    for text in rc.format_lines(lines):
        assert LINE.match(text), text
    assert all(status != "FAIL" for status, _, _ in lines)


def test_the_review_findings_warn_and_nothing_is_written_or_printed_personal():
    _, a_id, tenant = _run(_register_user_and_get_id("rev1-checks-a@example.com"))
    ids = {}

    async def seed():
        async with _session_factory() as session:
            second = User(tenant_id=tenant, email="rev1-checks-b@example.com", hashed_password="x", role="USER", is_active=True)
            session.add(second)
            await session.flush()
            base = dict(mode="PAPER", symbol="REV1SHARED", direction="LONG", entry_time=NOW - timedelta(hours=1), entry_price=100.0,
                        quantity=1.0, stop_loss=99.0, target1=102.0)
            trades = [TradeRecord(tenant_id=tenant, user_id=a_id, strategy_id="custom:7", **base),
                      TradeRecord(tenant_id=tenant, user_id=second.id, strategy_id="plan_rev1", **base)]
            session.add_all(trades)
            existing = await session.scalar(select(RiskSettingsRecord).where(RiskSettingsRecord.tenant_id == tenant))
            risk = existing or RiskSettingsRecord(tenant_id=tenant)
            risk.max_trades_per_day = 0
            session.add(risk)
            answer = json.dumps({"summary": "Two matches.", "ranked": [{"symbol": "X", "thesis": "Trend up.", "risks": "Gap risk.",
                                                                        "next_step": "Buy above the range high"}]})
            call = LlmCallRecord(tenant_id=tenant, user_id=a_id, feature=rc.SCAN_READ_FEATURE, provider="test", model="m",
                                 system_sha256="s", user_sha256="u", system_text="s", user_text="u", response_text=answer)
            session.add(call)
            await session.commit()
            ids.update(second=second.id, trades=[t.id for t in trades], call=call.id)

    async def cleanup():
        async with _session_factory() as session:
            await session.execute(delete(TradeRecord).where(TradeRecord.id.in_(ids.get("trades", []))))
            await session.execute(delete(LlmCallRecord).where(LlmCallRecord.id == ids.get("call", -1)))
            await session.execute(delete(RiskSettingsRecord).where(RiskSettingsRecord.tenant_id == tenant))
            await session.execute(delete(User).where(User.id == ids.get("second", -1)))
            await session.commit()

    _run(seed())
    try:
        before = (_run(_count(TradeRecord)), _run(_count(LlmCallRecord)), _run(_count(User)))
        lines = {name: (status, reason) for status, name, reason in _run(_lines())}
        assert (_run(_count(TradeRecord)), _run(_count(LlmCallRecord)), _run(_count(User))) == before      # read-only
        assert lines["shared-symbol-open"][0] == "WARN" and str(tenant) in lines["shared-symbol-open"][1]
        assert lines["members-per-org"][0] == "WARN" and str(tenant) in lines["members-per-org"][1]
        assert lines["risk-zero"][0] == "WARN" and "max_trades_per_day" in lines["risk-zero"][1]
        assert lines["strategy-ids"][0] == "WARN" and "custom:7" in lines["strategy-ids"][1] and "plan_rev1" in lines["strategy-ids"][1]
        assert lines["ai-read-words"][0] == "WARN" and "Buy above" not in lines["ai-read-words"][1]
        printed = "\n".join(rc.format_lines([(s, n, r) for n, (s, r) in lines.items()]))
        assert "@" not in printed and "REV1SHARED" not in printed                                      # no e-mail, no symbol
    finally:
        _run(cleanup())


def test_order_words_are_read_only_in_the_fields_the_page_shows():
    shown = json.dumps({"summary": "Calm.", "ranked": [{"thesis": "Range.", "risks": "Stop runs below.", "next_step": "Watch."}]})
    assert rc.ORDER_WORDS.search(rc._read_fields(shown))
    hidden = json.dumps({"summary": "Calm.", "ranked": [{"symbol": "BUY", "thesis": "Stopped trend.", "risks": "", "next_step": ""}]})
    assert not rc.ORDER_WORDS.search(rc._read_fields(hidden))


def test_a_broken_check_is_one_fail_line_and_the_rest_still_run(monkeypatch):
    async def boom(session):
        raise RuntimeError("secret detail never printed")
    checks = list(rc.CHECKS)
    checks[2] = ("trades-rows", boom)
    monkeypatch.setattr(rc, "CHECKS", tuple(checks))
    lines = _run(_lines())
    assert [name for _, name, _ in lines] == NAMES
    assert lines[2] == ("FAIL", "trades-rows", "the check could not run (RuntimeError)")
    assert all(status != "FAIL" for i, (status, _, _) in enumerate(lines) if i != 2)


def test_main_refuses_arguments():
    assert rc.main(["--write"]) == 2


def test_atp_sh_has_the_read_only_review_checks_subcommand():
    sh = (BACKEND.parent / "deploy" / "atp.sh").read_text()
    assert re.search(r"review-checks\)\s+cmd_review_checks \"\$@\"", sh)
    assert "dc exec -T backend python -m app.platform.review_checks" in sh
    assert "#   /opt/atp/deploy/atp.sh review-checks" in sh
