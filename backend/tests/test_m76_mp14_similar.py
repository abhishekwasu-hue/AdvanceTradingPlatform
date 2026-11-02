"""M76 MP-14 L-1: "Similar past setups" by area context - captured at entry, closed-before-now only.

- the server reads the area context itself, as of the trade's entry (the workspace's own reading; the page's words are
  never stored), and puts it on HIS own trades' journal entries only; the first record stays;
- the recall matches his closed trades on source, the area's timeframe, side and state, grouped by the side he took, with
  the setup recall's own summary (its minimum sample); a trade without a recorded context never matches;
- behind ui_v2 + copilot_workspace; another user's trade answers 404; nothing on an order path calls it.

Made-up data only."""
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select, update

from app.charts import read_chart as rc
from app.charts import workspace_recall as wr
from app.db.models import JournalEntryRecord, TradeRecord
from app.setup_recall import service
from tests.tenancy_route_table import ROUTE_TABLE
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixture
from tests.test_m72_pr1c_planner import _flag, _run
from tests.test_ui2_4a1_prefs import _who

CONTEXT = "/api/charts/workspace/entry-context"
SIMILAR = "/api/charts/workspace/similar"
INSIDE = {"role": "demand", "side": "buyer", "low": 100.0, "high": 110.0, "timeframe": "1h", "state": "tested", "words": "x"}
READING = {"source": "atp", "next_areas": {"inside": INSIDE, "above": None, "below": None}}


def test_area_context_and_match_are_pure():
    assert wr.area_context(READING, "15m") == {"source": "atp", "chart_tf": "15m", "tf": "1h", "side": "buyer", "state": "tested"}
    assert wr.area_context({"source": "atp", "next_areas": {"inside": None}}, "15m") is None
    assert wr.area_context(None, "15m") is None
    subject = {"source": "atp", "tf": "1h", "side": "buyer", "state": "tested"}
    assert wr.same_area({**subject, "chart_tf": "5m"}, subject)                   # the chart's own timeframe is not a key
    assert not wr.same_area({**subject, "state": "fresh"}, subject)
    assert not wr.same_area({**subject, "source": "engine"}, subject)             # two sources never mixed
    assert not wr.same_area(None, subject)


def _trade(i, side, pnl, closed):
    return TradeRecord(id=i, tenant_id=1, user_id=1, mode="PAPER", symbol="ZZSIM", strategy_id="manual", direction=side,
                       entry_time=closed - timedelta(hours=1), entry_price=100.0, quantity=1.0, stop_loss=95.0 if side == "LONG" else 105.0,
                       initial_stop_loss=None, exit_time=closed, exit_price=100.0, pnl=pnl, charges=0.0, leg_group_id=None, holding="SWING")


def test_grouped_by_his_side_with_the_recalls_own_summary():
    now = datetime(2031, 6, 11, 10, 0, tzinfo=UTC)
    rows = [_trade(1, "LONG", 10.0, now - timedelta(days=3)), _trade(2, "LONG", -5.0, now - timedelta(days=2)),
            _trade(3, "SHORT", 4.0, now - timedelta(days=1)), _trade(4, "LONG", 9.0, now + timedelta(minutes=1))]
    out = wr.grouped(service.build_units(rows, {}), {r.id: r.symbol for r in rows}, now, 2, 10)
    assert [(g["side"], g["count"], g["enough"]) for g in out] == [("LONG", 2, True), ("SHORT", 1, False)]   # trade 4: after now
    assert out[0]["wins"] == 1 and out[0]["losses"] == 1 and [m["trade_id"] for m in out[0]["matches"]] == [2, 1]
    assert out[0]["matches"][0]["symbol"] == "ZZSIM" and "Not enough history yet: 1 of 2" in out[1]["line"]


def _open(who):
    async def go():
        async with _session_factory() as session:
            row = TradeRecord(tenant_id=who["tenant_id"], user_id=who["user_id"], mode="PAPER", symbol="ZZSIM", strategy_id="manual",
                              direction="LONG", entry_time=datetime.now(UTC) - timedelta(minutes=2), entry_price=100.0, quantity=1.0,
                              stop_loss=95.0)
            session.add(row)
            await session.commit()
            return row.id
    return _run(go())


def _plan(trade_id):
    async def go():
        async with _session_factory() as session:
            raw = await session.scalar(select(JournalEntryRecord.plan_json).where(JournalEntryRecord.trade_id == trade_id))
            return json.loads(raw) if raw else None
    return _run(go())


def _close(trade_id, pnl):
    async def go():
        async with _session_factory() as session:
            await session.execute(update(TradeRecord).where(TradeRecord.id == trade_id).values(
                exit_time=datetime.now(UTC) - timedelta(seconds=5), exit_price=100.0 + pnl, pnl=pnl, exit_reason="manual exit"))
            await session.commit()
    _run(go())


def test_capture_at_entry_then_recall(monkeypatch):
    from app.journal import capture
    owner, other = _who("mp14-own"), _who("mp14-other")
    seen = {}

    async def reading(session, user, key, tf, now):
        seen.update(key=key, tf=tf, now=now)
        return READING
    monkeypatch.setattr(rc, "workspace_annotations", reading)
    _run(_flag("ui_v2", True))
    _run(_flag("copilot_workspace", False))
    body = {"trade_ids": [1], "instrument": "NSE:ZZSIM", "tf": "15m"}
    assert client.post(CONTEXT, headers=owner["headers"], json=body).status_code == 404
    _run(_flag("copilot_workspace", True, [owner["tenant_id"], other["tenant_id"]]))
    _run(_flag("discipline_engine", True, [owner["tenant_id"], other["tenant_id"]]))
    capture.reset_flag_cache()
    try:
        mine = _open(owner)
        theirs = _open(other)
        assert client.post(CONTEXT, headers=owner["headers"], json={**body, "trade_ids": [theirs]}).status_code == 404   # not his
        assert client.post(CONTEXT, headers=owner["headers"], json={**body, "instrument": "bad;key"}).status_code == 422
        got = client.post(CONTEXT, headers=owner["headers"], json={**body, "trade_ids": [mine]}).json()
        assert got["stored"] == 1 and got["area"]["side"] == "buyer"
        assert seen["key"] == "NSE:ZZSIM" and seen["tf"] == "15m" and seen["now"] <= datetime.now(UTC) - timedelta(minutes=1)  # as of entry
        assert _plan(mine)["area_context"] == {"source": "atp", "chart_tf": "15m", "tf": "1h", "side": "buyer", "state": "tested"}
        monkeypatch.setattr(rc, "workspace_annotations", lambda *a, **k: _later())
        client.post(CONTEXT, headers=owner["headers"], json={**body, "trade_ids": [mine]})
        assert _plan(mine)["area_context"]["state"] == "tested"                   # the first record stays
        q = {"source": "atp", "tf": "1h", "side": "buyer", "state": "tested"}
        assert client.get(SIMILAR, headers=owner["headers"], params=q).json()["count"] == 0         # still open: not a match
        _close(mine, 6.0)
        out = client.get(SIMILAR, headers=owner["headers"], params=q).json()
        assert out["count"] == 1 and out["sides"][0]["side"] == "LONG" and out["sides"][0]["matches"][0]["trade_id"] == mine
        assert out["blocks"] is False
        assert client.get(SIMILAR, headers=owner["headers"], params={**q, "state": "fresh"}).json()["count"] == 0
        assert client.get(SIMILAR, headers=other["headers"], params=q).json()["count"] == 0          # never another user's
        assert client.get(SIMILAR, headers=owner["headers"], params={**q, "side": "middle"}).status_code == 422
        assert ("POST", CONTEXT) in ROUTE_TABLE and ("GET", SIMILAR) in ROUTE_TABLE
    finally:
        _run(_flag("copilot_workspace", False))
        _run(_flag("discipline_engine", False))
        capture.reset_flag_cache()


async def _later():
    return {"source": "atp", "next_areas": {"inside": {**INSIDE, "state": "flipped"}}}


def test_no_order_path_calls_it():
    app = Path(__file__).resolve().parents[1] / "app"
    callers = sorted(p.relative_to(app).as_posix() for p in app.rglob("*.py") if "on_area_context" in p.read_text(encoding="utf-8"))
    assert callers == ["charts/workspace_recall.py", "journal/capture.py"]
    code = (app / "charts" / "workspace_recall.py").read_text(encoding="utf-8").split('"""', 2)[2]
    for word in (r"\bplace\(", r"fno_ticket", r"\bbuy\b", r"\bsell\b", r"\btarget\b"):
        assert not re.search(word, code, re.IGNORECASE), word
