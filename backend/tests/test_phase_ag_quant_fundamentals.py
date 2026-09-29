"""Phase AG: the Factor Lab fills value/quality inputs from the Fundamentals module."""
from datetime import date

import numpy as np

from app.core.enums import PeriodType
from app.fundamentals.models import FinancialPeriod
from app.quant import fundamentals_bridge as bridge
from tests.test_auth_api import client
from tests.test_fundamentals_api import _sample_period, _sample_profile
from tests.test_phase_k_commercial import _owner
from tests.test_phase_z_quant import _candles, _series, _trend


def _period(label, end, ptype=PeriodType.ANNUAL, **kw):
    base = dict(revenue=1000.0, ebitda=250.0, pat=100.0, eps=20.0, shares_outstanding=50.0, total_debt=300.0, shareholders_equity=500.0)
    base.update(kw)
    return FinancialPeriod(period_type=ptype, period_label=label, period_end_date=end, **base)


def test_derive_uses_latest_annual_and_prior_of_same_type():
    fy23 = _period("FY23", date(2023, 3, 31), pat=80.0)
    fy24 = _period("FY24", date(2024, 3, 31))
    q1 = _period("Q1FY25", date(2024, 6, 30), ptype=PeriodType.QUARTER, pat=30.0)
    latest, prior = bridge._latest_two([q1, fy23, fy24])
    assert latest is fy24 and prior is fy23                              # annual preferred, quarter ignored
    fill = bridge.derive("ALPHA", latest, prior, close=300.0, shares_outstanding=None, market_cap=None)
    assert fill.period == "FY24" and fill.missing == []
    assert fill.values == {"pe": 15.0, "pb": 30.0, "roe_pct": 20.0, "debt_to_equity": 0.6, "earnings_growth_pct": 25.0}

    # Without a close the price falls back to market_cap / shares; without either, pe/pb are reported missing, not guessed.
    from_cap = bridge.derive("ALPHA", latest, prior, close=None, shares_outstanding=None, market_cap=15000.0)
    assert from_cap.values["pe"] == 15.0 and from_cap.values["pb"] == 30.0
    no_price = bridge.derive("ALPHA", latest, None, close=None, shares_outstanding=None, market_cap=None)
    assert "pe" not in no_price.values and "pb" not in no_price.values and no_price.values["roe_pct"] == 20.0
    assert any(m.startswith("pe:") for m in no_price.missing) and any(m.startswith("earnings_growth_pct:") for m in no_price.missing)

    # Only quarters stored: newest quarter is used and growth compares against the previous quarter.
    q0 = _period("Q4FY24", date(2024, 3, 31), ptype=PeriodType.QUARTER, pat=25.0)
    latest_q, prior_q = bridge._latest_two([q0, q1])
    assert latest_q is q1 and prior_q is q0
    assert bridge.derive("ALPHA", latest_q, prior_q, close=None, shares_outstanding=None, market_cap=None).values["earnings_growth_pct"] == 20.0

    # A negative EPS gives no PE; negative equity gives no PB/ROE/D-E.
    loss = _period("FY24", date(2024, 3, 31), pat=-10.0, eps=-2.0, shareholders_equity=-5.0)
    bad = bridge.derive("ALPHA", loss, None, close=100.0, shares_outstanding=None, market_cap=None)
    assert bad.values == {"roe_pct": 200.0, "debt_to_equity": -60.0} or "pe" not in bad.values
    assert "pe" not in bad.values and "pb" not in bad.values

    assert bridge.derive("ALPHA", None, None, close=1.0, shares_outstanding=None, market_cap=None).missing == ["no financial periods stored"]


def test_factors_endpoint_fills_value_and_quality_from_stored_financials():
    headers, _me = _owner("quant-fund@example.com")
    assert client.post("/api/fundamentals/companies", headers=headers, json=_sample_profile("QFUND")).status_code == 201
    fy23 = _sample_period("FY23", pat=80.0); fy23["period_end_date"] = "2023-03-31"
    assert client.post("/api/fundamentals/companies/QFUND/financials", headers=headers, json=fy23).status_code == 201
    assert client.post("/api/fundamentals/companies/QFUND/financials", headers=headers, json=_sample_period("FY24")).status_code == 201

    up, down, flat = _series(_trend(slope=0.25, seed=7)), _series(_trend(slope=-0.2, seed=8)), _series(100 + np.sin(np.arange(300) / 4) * 0.4)
    symbols = [{"symbol": "qfund", "candles": _candles(up)}, {"symbol": "NOPROFILE", "candles": _candles(down)},
               {"symbol": "GIVEN", "candles": _candles(flat), "fundamentals": {"pe": 10, "pb": 2, "roe_pct": 15, "debt_to_equity": 0.1, "earnings_growth_pct": 5}}]

    res = client.post("/api/quant/factors", json={"symbols": symbols})
    assert res.status_code == 200, res.text
    body = res.json()
    rows = {r["symbol"]: r for r in body["rows"]}
    assert rows["QFUND"]["raw"]["value"] is not None and rows["QFUND"]["raw"]["quality"] is not None
    assert rows["NOPROFILE"]["raw"]["value"] is None and rows["NOPROFILE"]["raw"]["quality"] is None
    assert rows["GIVEN"]["raw"]["value"] is not None                                            # caller-supplied kept as is
    fund = body["fundamentals"]
    assert fund["enabled"] is True and set(fund["filled"]) == {"QFUND"} and fund["filled"]["QFUND"]["period"] == "FY24"
    close = float(up["close"].iloc[-1])
    assert abs(fund["filled"]["QFUND"]["values"]["pe"] - close / 20.0) < 1e-3                 # close / eps
    assert fund["filled"]["QFUND"]["values"]["roe_pct"] == 20.0 and fund["filled"]["QFUND"]["values"]["debt_to_equity"] == 0.6
    assert fund["filled"]["QFUND"]["values"]["earnings_growth_pct"] == 25.0
    assert "1 of 2" in fund["note"] and "NOPROFILE" in fund["note"]
    assert not any("value" in w for w in body["warnings"])                                      # value/quality exist for 2 of 3 symbols

    # Opt out: the stored financials are ignored and value/quality only exist where supplied.
    off = client.post("/api/quant/factors", json={"symbols": symbols, "use_fundamentals": False}).json()
    assert off["fundamentals"]["enabled"] is False and {r["symbol"]: r for r in off["rows"]}["QFUND"]["raw"]["value"] is None

    # Exposure carries the same fill and the given-weights tilt now includes value.
    exp = client.post("/api/quant/exposure", headers=headers, json={"symbols": symbols, "weights": {"qfund": 0.6, "given": 0.4}}).json()
    assert set(exp["fundamentals"]["filled"]) == {"QFUND"} and exp["exposure"]["value"] is not None
