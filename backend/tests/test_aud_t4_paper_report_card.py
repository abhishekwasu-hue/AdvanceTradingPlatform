"""AUD-T4 (owner audit 28 Oct): the paper report card.

- average R = net P&L / initial risk; a decision without a stop is counted but left out of the average (and counted);
- credit kept % = realised net P&L / premium received at entry, written options only;
- max drawdown of the cumulative net P&L (running peak from 0);
- Brier calibration only from PAPER_REPORT_CARD_MIN_TRADES_BRIER pairs, and only when a confidence is stored (none is);
- GET /api/paper/report-card: 404 while `paper_report_card` (or `ui_v2`) is off; the caller's own closed PAPER trades
  only (not a teammate's, not another organisation's, not LIVE, not open); plan followed from the journal's tags.
"""
from datetime import datetime, timedelta, timezone

from app.core import config
from app.db.models import JournalEntryRecord, JournalTagRecord, TradeRecord, User
from app.paper import report_card
from tests.test_auth_api import _session_factory, client
from tests.test_settings_a_connect_ai import _flags
from tests.test_trading_worker import _deploy, _run, _tenant

T0 = datetime(2026, 10, 1, 4, 0, tzinfo=timezone.utc)


def _trade(i, *, pnl, stop=95.0, direction="LONG", kind="UNDERLYING", entry=100.0, exit_price=None, qty=10.0, closed=True, **kw):
    return TradeRecord(id=i, tenant_id=1, user_id=1, mode=kw.pop("mode", "PAPER"), symbol=kw.pop("symbol", "RELIANCE"),
                       strategy_id=kw.pop("strategy_id", "s1"), direction=direction, entry_time=T0 + timedelta(hours=i),
                       entry_price=entry, quantity=qty, stop_loss=stop, instrument_kind=kind,
                       exit_time=T0 + timedelta(hours=i, minutes=30) if closed else None,
                       exit_price=exit_price if exit_price is not None else (entry if closed else None),
                       pnl=pnl if closed else None, charges=0.0, **kw)


def test_r_uses_the_initial_risk_and_leaves_stopless_trades_out_of_the_average():
    units = report_card.decisions([
        _trade(1, pnl=100.0, stop=95.0),                 # risk 5 x 10 = 50 -> 2R
        _trade(2, pnl=-25.0, stop=95.0),                 # -0.5R
        _trade(3, pnl=-30.0, stop=0.0),                  # no stop: counted, no R
        _trade(4, pnl=40.0, stop=90.0, initial_stop_loss=98.0),   # the INITIAL stop: risk 2 x 10 = 20 -> 2R
    ])
    c = report_card.card(units)
    assert c["closed"] == 4 and c["wins"] == 2 and c["win_pct"] == 50.0
    assert c["r_trades"] == 3 and c["no_stop_trades"] == 1
    assert c["avg_r"] == round((2.0 - 0.5 + 2.0) / 3, 2)
    assert c["net_pnl"] == 85.0
    assert c["plan_pct"] is None and c["capture_pct"] is None


def test_drawdown_is_the_largest_fall_from_the_running_peak():
    assert report_card.max_drawdown([]) == 0.0
    assert report_card.max_drawdown([100, -50, -80, 40, -30]) == 130.0
    assert report_card.max_drawdown([-20, -10, 50]) == 30.0          # the peak starts at 0
    units = report_card.decisions([_trade(2, pnl=-80.0), _trade(1, pnl=100.0), _trade(3, pnl=-50.0)])
    assert [u.pnl for u in units] == [100.0, -80.0, -50.0]           # in the order they closed
    assert report_card.card(units)["max_drawdown"] == 130.0


def test_capture_is_realised_over_premium_received_for_written_options_only():
    units = report_card.decisions([
        _trade(1, pnl=2950.0, direction="SHORT", kind="OPTION", entry=100.0, exit_price=40.0, qty=50.0, stop=150.0),
        _trade(2, pnl=-1000.0, direction="SHORT", kind="OPTION", entry=100.0, exit_price=120.0, qty=50.0, stop=150.0),
        _trade(3, pnl=500.0, direction="LONG", kind="OPTION", entry=50.0, exit_price=60.0, qty=50.0, stop=40.0),   # bought: no credit
    ])
    c = report_card.card(units)
    assert c["credit_trades"] == 2
    assert c["capture_pct"] == round(100.0 * (2950.0 - 1000.0) / 10000.0, 1)


def test_brier_appears_only_from_the_minimum_and_only_with_a_stored_confidence():
    pairs = [(0.7, 1)] * 19
    assert report_card.brier(pairs, 20) is None
    assert report_card.brier(pairs + [(0.7, 1)], 20) == 0.09
    shown = report_card.calibration(pairs + [(0.2, 0)], 20)
    assert shown["shown"] and shown["brier"] == round((19 * 0.09 + 0.04) / 20, 4)
    early = report_card.calibration(pairs, 20)
    assert not early["shown"] and "20" in early["note"]
    none = report_card.calibration([], 20)
    assert not none["shown"] and none["note"] == report_card.NO_CONFIDENCE


def _seed(rows):
    async def go():
        async with _session_factory() as session:
            for r in rows:
                session.add(r)
            await session.commit()
    _run(go())


def _db_trade(t, *, i, pnl, deployment_id=None, user_id=None, mode="PAPER", closed=True, strategy_id="s1"):
    row = _trade(i, pnl=pnl, mode=mode, closed=closed, strategy_id=strategy_id, deployment_id=deployment_id)
    row.id = None
    row.tenant_id, row.user_id = t["tenant_id"], user_id or t["user_id"]
    return row


def test_the_route_is_flagged_and_self_scoped():
    t = _tenant("aud-t4-route@example.com")
    other = _tenant("aud-t4-other@example.com")
    dep = _deploy(t, symbol="INFY")

    async def teammate():
        async with _session_factory() as session:
            u = User(tenant_id=t["tenant_id"], email="aud-t4-mate@example.com", hashed_password="x")
            session.add(u)
            await session.commit()
            return u.id
    mate = _run(teammate())
    _seed([
        _db_trade(t, i=1, pnl=100.0, deployment_id=dep),
        _db_trade(t, i=2, pnl=-50.0, deployment_id=dep),
        _db_trade(t, i=3, pnl=30.0, strategy_id="s2"),                     # by hand
        _db_trade(t, i=4, pnl=999.0, deployment_id=dep, mode="LIVE"),      # not PAPER
        _db_trade(t, i=5, pnl=999.0, deployment_id=dep, closed=False),     # still open
        _db_trade(t, i=6, pnl=999.0, deployment_id=dep, user_id=mate),     # a teammate's
        _db_trade(other, i=7, pnl=999.0),                                  # another organisation's
    ])
    _flags(ui_v2=True, paper_report_card=False, discipline_engine=False)
    try:
        assert client.get("/api/paper/report-card", headers=t["headers"]).status_code == 404
        _flags(ui_v2=False, paper_report_card=True)
        assert client.get("/api/paper/report-card", headers=t["headers"]).status_code == 404
        _flags(ui_v2=True, paper_report_card=True)
        body = client.get("/api/paper/report-card", headers=t["headers"]).json()
        assert body["total"]["closed"] == 3 and body["total"]["net_pnl"] == 80.0
        bots = {b["key"]: b for b in body["bots"]}
        assert bots[str(dep)]["closed"] == 2 and bots[str(dep)]["symbol"] == "INFY"
        assert bots["manual"]["closed"] == 1
        assert {s["key"]: s["closed"] for s in body["strategies"]} == {"s1": 2, "s2": 1}
        assert body["plan_note"] == report_card.JOURNAL_OFF and body["total"]["plan_pct"] is None
        assert not body["calibration"]["shown"] and body["calibration"]["min_trades"] == config.PAPER_REPORT_CARD_MIN_TRADES_BRIER
        assert all("tenant_id" not in b and "user_id" not in b for b in body["bots"] + body["strategies"])
        assert client.get("/api/paper/report-card", headers=other["headers"]).json()["total"]["closed"] == 1
    finally:
        _flags(ui_v2=False, paper_report_card=False)


def test_plan_followed_comes_from_counted_journal_tags():
    t = _tenant("aud-t4-plan@example.com")
    rows = [_db_trade(t, i=i, pnl=10.0) for i in (1, 2, 3, 4)]
    _seed(rows)
    ids = [r.id for r in rows]
    _seed([JournalEntryRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], trade_id=i) for i in ids[:3]]   # trade 4: no journal
          + [JournalTagRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], trade_id=ids[0], tag="fomo_entry", status="CONFIRMED"),
             JournalTagRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], trade_id=ids[1], tag="early_exit", status="DISMISSED"),
             JournalTagRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], trade_id=ids[2], tag="revenge_trade", status="CONFIRMED")])
    _flags(ui_v2=True, paper_report_card=True, discipline_engine=True)
    try:
        total = client.get("/api/paper/report-card", headers=t["headers"]).json()["total"]
        # 3 journaled; only the confirmed FOMO entry breaks the plan (dismissed never counts; revenge is another component)
        assert total["plan_trades"] == 3 and total["plan_flagged"] == 1 and total["plan_pct"] == round(200 / 3, 1)
    finally:
        _flags(ui_v2=False, paper_report_card=False, discipline_engine=False)
