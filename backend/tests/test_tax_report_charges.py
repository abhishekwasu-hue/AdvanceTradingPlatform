"""The tax report takes every statutory rate from `app.execution.india_costs` by the sell leg's date (one table for the
platform), and its F&O turnover method is a setting the report names.

Golden worked examples (hand-computed, then checked against `india_costs.round_trip`), on both sides of 1 Apr 2026:

  option written (SHORT): sell 75 @ 120, buy back @ 80   -> gross +3000, premium sold 9000
  option bought  (LONG):  buy 65 @ 50, sell @ 40         -> gross  -650, premium sold 2600
  future         (LONG):  buy 75 @ 25000, sell @ 25100   -> gross +7500, sell value 1,882,500

  STT          before 1 Apr 2026 (FY 2025-26)       from 1 Apr 2026 (FY 2026-27, Finance Act 2026: NSE/FATAX/73524)
  written      9000 x 0.1%   =   9.00               9000 x 0.15%   =  13.50
  bought       2600 x 0.1%   =   2.60               2600 x 0.15%   =   3.90
  future   1,882,500 x 0.02% = 376.50          1,882,500 x 0.05%   = 941.25

  F&O turnover  ABS_PNL              = 3000 + 650 + 7500               = 11,150
                ABS_PNL_PLUS_PREMIUM = 11,150 + 9000 + 2600            = 22,750
"""
from datetime import datetime, timezone

import pytest

from app.ai.wording import banned_terms
from app.core import config
from app.db.models import TradeRecord
from app.execution import india_costs
from app.market_data.calendar import IST
from app.tax import report as tax


def _at(y, m, d, hh=10):
    return datetime(y, m, d, hh, 0, tzinfo=IST).astimezone(timezone.utc)


def _trade(*, symbol, exchange, kind, direction, entry, exit_, qty, opened, closed, segment=None):
    """A closed trade whose recorded charges are the `india_costs` round trip (as PAPER fills record them)."""
    segment = segment or india_costs.segment_for(kind)
    sign = 1 if direction == "LONG" else -1
    gross = sign * (exit_ - entry) * qty
    cost = india_costs.round_trip(opened.astimezone(IST).date(), closed.astimezone(IST).date(), entry, exit_, qty, segment,
                                  sold_first=direction == "SHORT") if segment in india_costs.SEGMENTS else {"total": 0.0, "stt": 0.0}
    return TradeRecord(tenant_id=1, user_id=1, mode="LIVE", strategy_id="t", symbol=symbol, exchange=exchange, instrument_kind=kind,
                       direction=direction, entry_time=opened, entry_price=entry, quantity=qty, stop_loss=0.0, exit_time=closed,
                       exit_price=exit_, pnl=gross - cost["total"], charges=cost["total"]), cost


def _fno_trades(y, m, d):
    day_open, day_close = _at(y, m, d, 10), _at(y, m, d, 14)
    written = _trade(symbol="NIFTY26JUN25000CE", exchange="NFO", kind="OPTION", direction="SHORT", entry=120.0, exit_=80.0, qty=75,
                     opened=day_open, closed=day_close)
    bought = _trade(symbol="NIFTY26JUN25000PE", exchange="NFO", kind="OPTION", direction="LONG", entry=50.0, exit_=40.0, qty=65,
                    opened=day_open, closed=day_close)
    future = _trade(symbol="NIFTY26JUNFUT", exchange="NFO", kind="FUTURE", direction="LONG", entry=25000.0, exit_=25100.0, qty=75,
                    opened=day_open, closed=day_close)
    return [written, bought, future]


@pytest.mark.parametrize("day, fy, stt_written, stt_bought, stt_future", [
    ((2026, 3, 20), "2025-26", 9.00, 2.60, 376.50),
    ((2026, 6, 15), "2026-27", 13.50, 3.90, 941.25),
])
def test_fno_stt_equals_india_costs_on_both_sides_of_1_april_2026(day, fy, stt_written, stt_bought, stt_future):
    rows = _fno_trades(*day)
    for (_, cost), golden in zip(rows, (stt_written, stt_bought, stt_future)):
        assert cost["stt"] == pytest.approx(golden, abs=1e-9)                 # the golden figure is india_costs' figure
    body = tax.build_report([t for t, _ in rows], fy, mode="LIVE", method="ABS_PNL")
    fno = body["classes"]["FNO"]
    assert fno["trades"] == 3 and fno["gross_pnl"] == pytest.approx(3000 - 650 + 7500)
    assert fno["stt_estimate"] == pytest.approx(stt_written + stt_bought + stt_future, abs=0.005)
    assert fno["stt_estimate"] == pytest.approx(sum(cost["stt"] for _, cost in rows), abs=0.005)
    assert fno["charges"] == pytest.approx(sum(cost["total"] for _, cost in rows), abs=0.005)
    assert fno["sell_value"] == pytest.approx(9000 + 2600 + 1_882_500)


def test_a_written_option_pays_stt_at_the_rate_of_its_sale_date_not_its_close_date():
    """Sold on 27 Mar 2026 (0.1%), bought back on 7 Apr 2026: the report counts it in FY 2026-27 (closed then) but the STT
    sits on the sale, at the old rate - exactly what india_costs charges that leg."""
    trade, cost = _trade(symbol="NIFTY26APR25000CE", exchange="NFO", kind="OPTION", direction="SHORT", entry=120.0, exit_=80.0,
                         qty=75, opened=_at(2026, 3, 27), closed=_at(2026, 4, 7))
    body = tax.build_report([trade], "2026-27", mode="LIVE")
    assert body["classes"]["FNO"]["stt_estimate"] == pytest.approx(9.00) == pytest.approx(cost["stt"])


@pytest.mark.parametrize("day, fy", [((2026, 3, 20), "2025-26"), ((2026, 6, 15), "2026-27")])
def test_fno_turnover_under_both_methods(day, fy):
    trades = [t for t, _ in _fno_trades(*day)]
    abs_pnl = tax.build_report(trades, fy, mode="LIVE", method="ABS_PNL")
    plus_premium = tax.build_report(trades, fy, mode="LIVE", method="ABS_PNL_PLUS_PREMIUM")
    assert abs_pnl["classes"]["FNO"]["turnover"] == pytest.approx(11_150)
    assert plus_premium["classes"]["FNO"]["turnover"] == pytest.approx(22_750)
    # The method changes turnover only: STT, charges and P&L are the same either way.
    for key in ("stt_estimate", "charges", "net_pnl", "gross_pnl"):
        assert abs_pnl["classes"]["FNO"][key] == plus_premium["classes"]["FNO"][key]
    for body, method in ((abs_pnl, "ABS_PNL"), (plus_premium, "ABS_PNL_PLUS_PREMIUM")):
        assert body["turnover_method"] == method and body["turnover_method_label"] == tax.TURNOVER_METHODS[method]
        assert any(n.startswith(f"Turnover method: {method}.") for n in body["notes"])
        assert f"turnover_method,{method}" in tax.to_csv(body).splitlines()[0]


def test_the_default_method_is_the_setting(monkeypatch):
    trades = [t for t, _ in _fno_trades(2026, 6, 15)]
    assert config.TAX_FNO_TURNOVER_METHODS == tuple(tax.TURNOVER_METHODS)
    monkeypatch.setattr(config, "TAX_FNO_TURNOVER_METHOD", "ABS_PNL")
    assert tax.build_report(trades, "2026-27", mode=None)["turnover_method"] == "ABS_PNL"
    monkeypatch.setattr(config, "TAX_FNO_TURNOVER_METHOD", "ABS_PNL_PLUS_PREMIUM")
    assert tax.build_report(trades, "2026-27", mode=None)["classes"]["FNO"]["turnover"] == pytest.approx(22_750)
    assert tax.turnover_method(" abs_pnl ") == "ABS_PNL"
    with pytest.raises(ValueError):
        tax.turnover_method("PREMIUM_ONLY")


def test_the_setting_defaults_to_abs_pnl_and_ignores_an_unknown_value(monkeypatch):
    monkeypatch.delenv("TAX_FNO_TURNOVER_METHOD", raising=False)
    assert config._choice("TAX_FNO_TURNOVER_METHOD", "ABS_PNL", config.TAX_FNO_TURNOVER_METHODS) == "ABS_PNL"
    monkeypatch.setenv("TAX_FNO_TURNOVER_METHOD", "abs_pnl_plus_premium")
    assert config._choice("TAX_FNO_TURNOVER_METHOD", "ABS_PNL", config.TAX_FNO_TURNOVER_METHODS) == "ABS_PNL_PLUS_PREMIUM"
    monkeypatch.setenv("TAX_FNO_TURNOVER_METHOD", "GUESS")
    assert config._choice("TAX_FNO_TURNOVER_METHOD", "ABS_PNL", config.TAX_FNO_TURNOVER_METHODS) == "ABS_PNL"


@pytest.mark.parametrize("day", [(2026, 3, 20), (2026, 6, 15)])
def test_equity_mcx_and_crypto_rates_come_from_india_costs(day):
    opened, closed = _at(*day, 10), _at(*day, 14)
    equity, eq_cost = _trade(symbol="RELIANCE", exchange="NSE", kind="UNDERLYING", direction="LONG", entry=100.0, exit_=104.0, qty=10,
                             opened=opened, closed=closed)
    mcx, _ = _trade(symbol="CRUDEOIL26JUNFUT", exchange="MCX", kind="FUTURE", direction="SHORT", entry=6000.0, exit_=5900.0, qty=100,
                    opened=opened, closed=closed, segment="MCX")
    btc_win, _ = _trade(symbol="BTCINR", exchange="CRYPTO", kind="UNDERLYING", direction="LONG", entry=100.0, exit_=110.0, qty=1,
                        opened=opened, closed=closed, segment="VDA")
    btc_loss, _ = _trade(symbol="BTCINR", exchange="CRYPTO", kind="UNDERLYING", direction="LONG", entry=100.0, exit_=95.0, qty=1,
                         opened=opened, closed=closed, segment="VDA")
    fy = tax.financial_year(opened.astimezone(IST).date())
    body = tax.build_report([equity, mcx, btc_win, btc_loss], fy, mode="LIVE")
    d = opened.astimezone(IST).date()
    assert body["classes"]["EQUITY_INTRADAY"]["stt_estimate"] == pytest.approx(eq_cost["stt"], abs=0.005) == pytest.approx(0.26, abs=0.005)
    assert body["classes"]["FNO"]["ctt_estimate"] == pytest.approx(6000 * 100 * india_costs.ctt_sell(d), abs=0.005) == pytest.approx(60.0)
    crypto = body["classes"]["CRYPTO"]
    vda = india_costs.vda_rates(d)
    assert crypto["tds_estimate"] == pytest.approx((110 + 95) * vda["tds"], abs=0.005) == pytest.approx(2.05)
    assert crypto["tax_estimate"] == pytest.approx(10 * vda["tax"]) == pytest.approx(3.0) and crypto["disallowed_losses"] == 5.0


def test_the_report_has_no_rate_table_of_its_own_and_shows_the_dated_one():
    assert not hasattr(tax, "RATES")
    before = tax.build_report([], "2025-26", mode="LIVE")
    after = tax.build_report([], "2026-27", mode="LIVE")
    assert before["rates_as_of"] == "2025-04-01" and after["rates_as_of"] == "2026-04-01"
    assert before["rates"]["FNO_OPTION"]["stt_sell_pct"] == pytest.approx(india_costs.rates("2025-04-01", "OPTION")["stt_sell"] * 100) == 0.1
    assert after["rates"]["FNO_OPTION"]["stt_sell_pct"] == pytest.approx(india_costs.rates("2026-04-01", "OPTION")["stt_sell"] * 100) == 0.15
    assert after["rates"]["FNO_FUTURE"]["stt_sell_pct"] == 0.05 and after["rates"]["CRYPTO"] == {"tds_pct": 1.0, "tax_pct": 30.0}
    assert before["rate_changes"] == [] and after["rate_changes"] == []        # the 2026 change falls on the FY's first day
    mid_year = tax.build_report([], "2024-25", mode="LIVE")                    # 1 Oct 2024 changed option and future STT
    changed = {(c["class"], c["from"]): (c["before_pct"], c["pct"]) for c in mid_year["rate_changes"]}
    assert changed == {("FNO_OPTION", "2024-10-01"): (0.0625, 0.1), ("FNO_FUTURE", "2024-10-01"): (0.0125, 0.02)}
    assert any(n.startswith("Rates changed during this year") for n in mid_year["notes"])


def test_the_report_names_the_source_of_every_rate_it_shows():
    """TX-1: each rate in `rates` has its `india_costs` source in `rate_sources` (same keys), as of the FY's first day."""
    before = tax.build_report([], "2025-26", mode="LIVE")
    after = tax.build_report([], "2026-27", mode="LIVE")
    for body in (before, after):
        assert {k: set(v) for k, v in body["rate_sources"].items()} == {k: set(v) for k, v in body["rates"].items()}
    option, future = after["rate_sources"]["FNO_OPTION"]["stt_sell_pct"], after["rate_sources"]["FNO_FUTURE"]["stt_sell_pct"]
    for src in (option, future):
        assert src["ref"].startswith("NSE/FATAX/73524") and src["date"] == "2026-03-31" and src["verify"] == ""
        assert src["url"] == "https://nsearchives.nseindia.com/content/circulars/FATAX73524.pdf"
    assert before["rate_sources"]["FNO_OPTION"]["stt_sell_pct"]["ref"].startswith("NSE/FATAX/63809")
    assert after["rate_sources"]["MCX_FUTURE"]["ctt_sell_pct"]["verify"] == ""
    assert after["rate_sources"]["CRYPTO"]["tds_pct"]["verify"] == ""
    assert "Income-tax Act 2025" in after["rate_sources"]["CRYPTO"]["tax_pct"]["verify"]        # still to settle (TX-1)


def test_notes_say_not_tax_advice_and_carry_no_advice_wording():
    body = tax.build_report([t for t, _ in _fno_trades(2026, 6, 15)], "2026-27", mode="LIVE")
    assert body["notes"][0].startswith("This report is not tax advice.")
    for note in body["notes"]:
        assert banned_terms(note) == [], note
