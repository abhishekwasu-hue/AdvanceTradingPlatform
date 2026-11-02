"""M76 MP-13 (D-3): the close summary inside the Copilot workspace - his own day, numbers only.

- trades entered / closed today (a structure counts once), net P&L and charges of the legs closed today;
- the check-card lines he accepted on today's trades, per type;
- plan adherence from HIS checklist (none saved = no checklist row) and plan vs fill in points (only where the plan had its
  own entry price);
- what stays open: positions, unrealised P&L, rupees at risk to his own stops, positions without his stop;
- PAPER and LIVE by the same code; behind ui_v2 + copilot_workspace; GET only, in the route table; no order path reads it.

Made-up data only."""
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.charts import workspace_summary as ws
from app.db.models import TradeRecord
from app.market_data.calendar import IST
from tests.tenancy_route_table import ROUTE_TABLE
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixture
from tests.test_m72_pr1c_planner import _flag, _run
from tests.test_ui2_4a1_prefs import _who

SUMMARY = "/api/charts/workspace/close-summary"
NOW = datetime(2031, 6, 11, 10, 0, tzinfo=UTC)
START = NOW.astimezone(IST).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
LABELS = {"duplicate_order": "Same contract and side already open or waiting", "expiry_day": "Contract expires today"}


def _t(i, **kw):
    base = dict(id=i, tenant_id=1, user_id=1, mode="PAPER", symbol="ZZSUM", strategy_id="manual", direction="LONG",
                entry_time=NOW - timedelta(hours=2), entry_price=100.0, quantity=10.0, stop_loss=95.0, initial_stop_loss=95.0,
                exit_time=None, exit_price=None, pnl=None, charges=0.0, mark_price=None, leg_group_id=None)
    return TradeRecord(**{**base, **kw})


def test_his_day_in_numbers():
    rows = [
        _t(1, exit_time=NOW - timedelta(hours=1), exit_price=104.0, pnl=38.0, charges=2.0),                     # a gain
        _t(2, direction="SHORT", stop_loss=105.0, initial_stop_loss=105.0, exit_time=NOW - timedelta(minutes=30), exit_price=103.0,
           pnl=-32.0, charges=2.0),                                                                             # a loss
        _t(3, mark_price=102.0),                                                                                # open, his stop 95
        _t(4, mark_price=98.0, stop_loss=0.0, initial_stop_loss=0.0, entry_time=NOW - timedelta(days=2)),       # open since before
        _t(5, leg_group_id="g1", exit_time=NOW - timedelta(minutes=20), pnl=10.0, charges=1.0),                 # one structure,
        _t(6, leg_group_id="g1", exit_time=NOW - timedelta(minutes=20), pnl=-4.0, charges=1.0),                 # two legs
    ]
    plans = {1: {"accepted_warnings": [{"id": "duplicate_order"}, {"id": "expiry_day"}], "entry": 99.5, "entry_source": "signal"},
             2: {"accepted_warnings": [{"id": "duplicate_order"}], "entry": 100.0, "entry_source": "fill"},
             3: {"entry": 101.0, "entry_source": "signal"},
             5: {"accepted_warnings": [{"id": "expiry_day"}]}, 6: {"accepted_warnings": [{"id": "expiry_day"}]}}
    answers = {1: [{"rule": "Trend first", "followed": True}, {"rule": "Area marked", "followed": False}],
               2: [{"rule": "Trend first", "followed": True}]}
    out = ws.mode_summary(rows, plans, answers, START, LABELS, ["Trend first", "Area marked"])
    assert out["trades"] == {"entered": 4, "closed": 3, "gains": 2, "losses": 1, "net_pnl": 12.0, "charges": 6.0}
    assert out["accepted"] == [{"id": "duplicate_order", "label": LABELS["duplicate_order"], "count": 2},
                               {"id": "expiry_day", "label": LABELS["expiry_day"], "count": 2}]      # the structure once
    assert out["checklist"] == {"rules": 2, "trades": 4, "followed": 2, "not_followed": 1, "not_answered": 5}   # a structure once
    assert out["fill_vs_plan"] == {"trades": 2, "average_points": 0.75, "largest_points": 1.0}     # a fill's own plan is not a plan
    assert out["open"] == {"positions": 2, "unrealised_pnl": 0.0, "at_risk_to_your_stops": 50.0, "without_your_stop": 1, "unmarked": 0}
    assert ws.mode_summary(rows, plans, answers, START, LABELS, [])["checklist"] is None           # no checklist saved: no row


def _add(who, **kw):
    async def go():
        async with _session_factory() as session:
            row = _t(None, tenant_id=who["tenant_id"], user_id=who["user_id"], entry_time=datetime.now(UTC) - timedelta(minutes=5), **kw)
            row.id = None
            session.add(row)
            await session.commit()
    _run(go())


def test_route_flags_modes_and_read_only():
    who = _who("mp13-route")
    _run(_flag("ui_v2", True))
    _run(_flag("copilot_workspace", False))
    assert client.get(SUMMARY, headers=who["headers"]).status_code == 404
    _run(_flag("copilot_workspace", True, [who["tenant_id"]]))
    try:
        empty = client.get(SUMMARY, headers=who["headers"]).json()
        assert empty["modes"] == {} and empty["blocks"] is False and isinstance(empty["market_open"], bool)
        _add(who, mode="PAPER", mark_price=101.0)
        _add(who, mode="LIVE", mark_price=99.0)
        body = client.get(SUMMARY, headers=who["headers"]).json()
        assert set(body["modes"]) == {"PAPER", "LIVE"} and body["checklist_saved"] is False
        assert body["modes"]["PAPER"]["open"]["unrealised_pnl"] == 10.0 and body["modes"]["LIVE"]["open"]["unrealised_pnl"] == -10.0
        assert body["modes"]["PAPER"]["trades"]["entered"] == 1
        assert client.get(SUMMARY).status_code == 401
        assert {m for r in ws.router.routes for m in getattr(r, "methods", set())} == {"GET"}
        assert ("GET", SUMMARY) in ROUTE_TABLE
    finally:
        _run(_flag("copilot_workspace", False))


def test_numbers_only_and_no_order_path():
    src = (Path(__file__).resolve().parents[1] / "app" / "charts" / "workspace_summary.py").read_text(encoding="utf-8")
    code = src.split('"""', 2)[2]
    for word in (r"\bbuy\b", r"\bsell\b", r"\btarget\b", r"\bstop[- ]loss\b", r"\bplace\(", r"fno_ticket", r"\.add\(", r"commit\("):
        assert not re.search(word, code, re.IGNORECASE), word
