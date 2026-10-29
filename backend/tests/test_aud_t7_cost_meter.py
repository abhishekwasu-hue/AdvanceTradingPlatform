"""AUD-T7 (owner audit 28 Oct): the cost-of-trading meter.

- the financial year runs 1 April - 31 March in IST (31 March 23:59 IST is last year, 1 April 00:00 IST is this year);
- PAPER and LIVE are separate books, never added together;
- contract-note charges are counted as such (the import writes them into the trade), the rest labelled estimated;
- a zero or losing gross gives no share (no division by zero);
- GET /api/trades/cost-meter: 404 while `cost_meter` (or `ui_v2`) is off; the caller's own trades only.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.db.models import TradeRecord
from app.market_data.calendar import IST
from app.trading import cost_meter
from tests.test_auth_api import _register, _session_factory, client
from tests.test_settings_a_connect_ai import _flags, _run

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=IST)


def _t(exit_time, pnl, charges, mode="PAPER", source="ESTIMATED", price_source=None):
    return SimpleNamespace(exit_time=exit_time, pnl=pnl, charges=charges, mode=mode, charges_source=source, price_source=price_source)


def _mode(result, mode):
    return next(m for m in result["modes"] if m["mode"] == mode)


def test_financial_year_boundary_is_1_april_ist():
    assert cost_meter.year_start(datetime(2027, 3, 31, 23, 59, tzinfo=IST)).date().isoformat() == "2026-04-01"
    assert cost_meter.year_start(datetime(2026, 4, 1, 0, 0, tzinfo=IST)).date().isoformat() == "2026-04-01"
    # 31 March 19:00 UTC is already 1 April 00:30 IST.
    assert cost_meter.year_start(datetime(2026, 3, 31, 19, 0, tzinfo=timezone.utc)).date().isoformat() == "2026-04-01"
    assert cost_meter.year_start(datetime(2026, 3, 31, 18, 0, tzinfo=timezone.utc)).date().isoformat() == "2025-04-01"

    now = datetime(2026, 4, 2, 10, 0, tzinfo=IST)
    trades = [_t(datetime(2026, 3, 31, 23, 59, tzinfo=IST), 100.0, 20.0),     # last financial year: left out
              _t(datetime(2026, 4, 1, 0, 0, tzinfo=IST), 50.0, 10.0)]          # this year and this month
    paper = _mode(cost_meter.meter(trades, now), "PAPER")
    assert paper["year"]["trades"] == 1 and paper["year"]["gross"] == 60.0 and paper["month"]["trades"] == 1


def test_paper_and_live_are_separate_and_the_figures_add_up():
    trades = [_t(NOW - timedelta(days=1), 80.0, 20.0, "PAPER"), _t(NOW - timedelta(days=40), -30.0, 10.0, "PAPER"),
              _t(NOW - timedelta(days=2), 900.0, 100.0, "LIVE")]
    result = cost_meter.meter(trades, NOW)
    assert [m["mode"] for m in result["modes"]] == ["PAPER", "LIVE"]
    paper, live = _mode(result, "PAPER"), _mode(result, "LIVE")
    # pnl is net of charges, so gross = pnl + charges: (100) + (-20) = 80; charges 30; net 50.
    assert (paper["year"]["gross"], paper["year"]["charges"], paper["year"]["net"]) == (80.0, 30.0, 50.0)
    assert paper["year"]["charges_share_of_gross"] == round(30 / 80, 4)
    assert paper["year"]["charges_per_trade"] == 15.0
    assert paper["year"]["median_trade_share"] == 0.2 and paper["year"]["trades_with_gross_profit"] == 1
    assert paper["month"]["trades"] == 1 and paper["month"]["gross"] == 100.0
    assert (live["year"]["gross"], live["year"]["charges"], live["year"]["trades"]) == (1000.0, 100.0, 1)


def test_contract_note_charges_are_counted_and_labelled():
    trades = [_t(NOW - timedelta(days=1), 70.0, 30.0, source="CONTRACT_NOTE"), _t(NOW - timedelta(days=1), 90.0, 10.0)]
    year = _mode(cost_meter.meter(trades, NOW), "PAPER")["year"]
    assert (year["from_contract_notes"], year["estimated"], year["charges"]) == (1, 1, 40.0)


def test_zero_or_losing_gross_gives_no_share():
    flat = _mode(cost_meter.meter([_t(NOW, -20.0, 20.0)], NOW), "PAPER")["year"]       # gross exactly 0
    assert flat["gross"] == 0.0 and flat["charges_share_of_gross"] is None and flat["median_trade_share"] is None
    loss = _mode(cost_meter.meter([_t(NOW, -120.0, 20.0)], NOW), "PAPER")["year"]
    assert loss["charges_share_of_gross"] is None
    whole = _mode(cost_meter.meter([_t(NOW, -5.0, 20.0)], NOW), "PAPER")["year"]       # gross 15, charges 20
    assert whole["charges_took_whole_profit"] == 1
    empty = cost_meter.meter([], NOW)
    assert empty["modes"] == [] and empty["year_start"] == "2026-04-01" and empty["month_start"] == "2026-10-01"


def test_open_and_client_priced_trades_are_left_out():
    trades = [_t(None, None, 0.0), _t(NOW, 10.0, 1.0, price_source="client")]
    assert cost_meter.meter(trades, NOW)["modes"] == []


def _me(email):
    headers = {"Authorization": f"Bearer {_register(email)}"}
    me = client.get("/api/auth/me", headers=headers).json()
    return {"headers": headers, "tenant_id": me["tenant_id"], "user_id": me["id"]}


def _seed(tenant_id, user_id, pnl, charges, mode="PAPER"):
    async def go():
        async with _session_factory() as session:
            when = datetime.now(timezone.utc) - timedelta(minutes=5)
            session.add(TradeRecord(tenant_id=tenant_id, user_id=user_id, mode=mode, symbol="RELIANCE", strategy_id="s",
                                    direction="LONG", entry_time=when - timedelta(hours=1), entry_price=100.0, quantity=1,
                                    stop_loss=90.0, exit_time=when, exit_price=110.0, pnl=pnl, charges=charges))
            await session.commit()
    _run(go())


def test_the_route_is_flagged_and_self_scoped():
    a, b = _me("aud-t7-a@example.com"), _me("aud-t7-b@example.com")
    _flags(ui_v2=True, cost_meter=False)
    try:
        assert client.get("/api/trades/cost-meter", headers=a["headers"]).status_code == 404
        _flags(ui_v2=False, cost_meter=True)
        assert client.get("/api/trades/cost-meter", headers=a["headers"]).status_code == 404
        _flags(ui_v2=True, cost_meter=True)
        empty = client.get("/api/trades/cost-meter", headers=a["headers"])
        assert empty.status_code == 200 and empty.json()["modes"] == []
        _seed(a["tenant_id"], a["user_id"], 8.0, 2.0)
        _seed(b["tenant_id"], b["user_id"], 500.0, 50.0)                 # another organisation's user
        _seed(a["tenant_id"], b["user_id"], 700.0, 70.0)                 # another user's row: never counted for a
        _seed(a["tenant_id"], a["user_id"], 18.0, 2.0, mode="LIVE")
        body = client.get("/api/trades/cost-meter", headers=a["headers"]).json()
        paper, live = _mode(body, "PAPER"), _mode(body, "LIVE")
        assert (paper["year"]["trades"], paper["year"]["gross"], paper["year"]["charges"]) == (1, 10.0, 2.0)
        assert (live["year"]["trades"], live["year"]["gross"]) == (1, 20.0)
        assert client.get("/api/trades/cost-meter").status_code == 401
    finally:
        _flags(ui_v2=False, cost_meter=False)
