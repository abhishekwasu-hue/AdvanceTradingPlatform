"""Z5 (Trade port): the short-strike breach model (Trade `strike_breach_model.py`) - golden parity with Trade's own
functions on a synthetic series (tests/fixtures/strike_breach/make_golden.py), no look-ahead, the inversion, the per-leg
report, the builder's evaluate answer and the proposal card.

The golden file is Trade output, read from the private fixtures store (tests/private_fixtures.py): the public CI skips
the parity tests with the reason. The other tests run on the same synthetic series built in code
(fixtures/strike_breach/synthetic_inputs.py; test_the_synthetic_series_is_the_golden_s_series ties the two)."""
import asyncio
import math
import uuid
from datetime import date, timedelta

import numpy as np
import pytest

from app.options_builder import strike_breach as sb
from tests import private_fixtures as PF
from tests.fixtures.strike_breach.synthetic_inputs import series as synthetic_series
from tests.test_auth_api import _register, client

_SERIES = synthetic_series()
BARS = {k: [float(x) for x in _SERIES[k]] for k in ("open", "high", "low", "close")}


@pytest.fixture(scope="module")
def golden():
    return PF.load("backend/tests/fixtures/strike_breach/golden.json.gz")     # skips when the private store is not mounted


def _bars(n=None):
    keys = ("high", "low", "close")
    rows = [dict(zip(keys, values)) for values in zip(*(BARS[k] for k in keys))]
    return rows if n is None else rows[:n]


# --- parity with the Trade functions -----------------------------------------------------------------------------------

def test_the_synthetic_series_is_the_golden_s_series(golden):
    assert BARS == golden["bars"]


def test_frozen_coefficients_are_trades(golden):
    for outcome in sb.OUTCOMES:
        assert list(golden["coef"][outcome]["names"]) == list(sb.NAMES)
        assert list(golden["coef"][outcome]["coef"]) == list(sb.COEF[outcome])


def test_features_match_trade_daily_features_lagged_one_day(golden):
    """Trade's row i holds the value through day i-1; ATP's series at i-1 is the same number."""
    bars = golden["bars"]
    series = sb.indicator_series(bars["high"], bars["low"], bars["close"])
    for key in ("sigma20", "atr14", "adx", "di_dir"):
        want = golden["features"][key]
        assert want[0] is None
        for i in range(1, len(want)):
            mine = series[key][i - 1]
            if want[i] is None:
                assert not math.isfinite(mine), (key, i)
            else:
                assert mine == pytest.approx(want[i], rel=1e-12, abs=1e-12), (key, i)


def test_wilder_and_regime_match_trade(golden):
    w = golden["wilder"]
    out = sb.wilder(np.asarray(w["x"], float), w["n"])
    for mine, theirs in zip(out, w["out"]):
        assert (theirs is None and math.isnan(mine)) or mine == pytest.approx(theirs, abs=1e-12)
    for case in golden["regime"]:
        assert sb.regime_of(case["adx"], case["align"]) == case["regime"]


def test_breach_probability_matches_trade_predict_frozen(golden):
    assert len(golden["predict"]) >= 500
    for case in golden["predict"]:
        regime = sb.regime_of(case["adx"], case["align_adx"])
        for outcome in sb.OUTCOMES:
            p = sb.breach_probability(case["z_rv"], case["h"], case["weekday"], regime, struct=case["align_struct"], call=case["side"] < 0, outcome=outcome)
            assert p == pytest.approx(case[f"p_{outcome}"], abs=1e-12)


def test_required_z_matches_trade_and_inverts_the_model(golden):
    for case in golden["required_z"]:
        z = sb.required_z(case["target"], case["h"], case["weekday"], case["regime"], struct=case["struct"], call=bool(case["call"]), outcome=case["outcome"])
        assert (z is None) == (case["z"] is None)
        if z is not None:
            assert z == pytest.approx(case["z"], abs=1e-12)
            p = sb.breach_probability(z, case["h"], case["weekday"], case["regime"], struct=case["struct"], call=bool(case["call"]), outcome=case["outcome"])
            assert p == pytest.approx(case["target"], abs=1e-9)
    assert sb.required_z(0.999, 3, 1, "mid") is None            # beyond what z >= 0 reaches: no extrapolation


def test_features_have_no_lookahead():
    """Changing the bars from day i on never changes the values up to day i-1 (an entry on day i reads only those)."""
    base = sb.indicator_series(BARS["high"], BARS["low"], BARS["close"])
    i = 60
    high, close = list(BARS["high"]), list(BARS["close"])
    for j in range(i, len(close)):
        high[j] *= 1.2
        close[j] *= 1.2
    moved = sb.indicator_series(high, BARS["low"], close)
    for key in base:
        np.testing.assert_array_equal(base[key][:i], moved[key][:i])
    assert sb.features_from_bars(_bars(i)) == sb.features_from_bars([{**b} for b in _bars(i)])


# --- the per-leg report ------------------------------------------------------------------------------------------------

def test_sessions_to_expiry_counts_both_ends_and_skips_weekends_and_holidays():
    monday = date(2026, 3, 2)
    assert sb.sessions_to_expiry(monday, monday) == 1
    assert sb.sessions_to_expiry(monday, monday + timedelta(days=3)) == 4
    assert sb.sessions_to_expiry(monday, monday + timedelta(days=7)) == 6
    assert sb.sessions_to_expiry(monday, monday + timedelta(days=3), holidays=[monday + timedelta(days=1)]) == 3
    assert sb.sessions_to_expiry(monday, monday - timedelta(days=1)) == 0


def test_short_strike_report_per_leg():
    features = sb.features_from_bars(_bars())
    assert features is not None and features["sigma20"] > 0
    spot, monday = 20000.0, date(2026, 3, 2)
    thursday = monday + timedelta(days=3)
    legs = [{"direction": "SELL", "option_type": "PE", "strike": 19700, "expiry": thursday.isoformat()},
            {"direction": "SELL", "option_type": "PE", "strike": 19400, "expiry": thursday.isoformat()},
            {"direction": "BUY", "option_type": "PE", "strike": 19000, "expiry": thursday.isoformat()},
            {"direction": "SELL", "option_type": "CE", "strike": 19900, "expiry": thursday.isoformat()},             # already beyond
            {"direction": "SELL", "option_type": "CE", "strike": 20500, "expiry": (monday + timedelta(days=30)).isoformat()},
            {"direction": "SELL", "option_type": "CE", "strike": 20500, "expiry": (monday - timedelta(days=1)).isoformat()}]
    rows = sb.short_strike_breach(legs, spot=spot, as_of=monday, features=features)
    assert [r["leg"] for r in rows] == [0, 1, 3, 4, 5]                       # the long leg is not a short strike
    near, far, itm, monthly, expired = rows
    assert near["status"] == "ok" and near["side"] == "put" and near["sessions"] == 4 and near["dte"] == 3
    assert 0 < far["prob_close"] < near["prob_close"] < 1                  # farther strike, smaller chance
    assert far["z"] > near["z"]
    assert itm["status"] == "in_the_money" and itm["prob_close"] is None
    assert monthly["extrapolated"] is True                                 # DTE beyond the fitted weekly range
    assert expired["status"] == "expired"
    z = sb.z_of(19700, spot, features["sigma20"], 4)
    assert near["z"] == pytest.approx(z, abs=1e-4)
    regime = sb.regime_of(features["adx"], features["di_dir"] * 1)
    assert near["regime"] == regime
    assert near["prob_close"] == pytest.approx(sb.breach_probability(z, 4, 0, regime), abs=1e-4)
    ten = near["required_distance"]["10"]
    if ten is not None:                                                    # the strike for a 10% risk sits where the model says 10%
        p = sb.breach_probability(sb.z_of(ten["strike"], spot, features["sigma20"], 4), 4, 0, regime)
        assert p == pytest.approx(0.10, abs=2e-3)
    up = sb.short_strike_breach(legs[:1], spot=spot, as_of=monday, features=features, structure=1)[0]
    down = sb.short_strike_breach(legs[:1], spot=spot, as_of=monday, features=features, structure=-1)[0]
    assert up["struct"] == 1 and down["struct"] == -1                     # an uptrend is "with" a short put


def test_report_needs_enough_history():
    out = sb.report([], spot=100.0, as_of=date(2026, 3, 2), bars=_bars(10))
    assert out["available"] is False and "daily bars" in out["reason"]
    out = sb.report([], spot=100.0, as_of=date(2026, 3, 2), bars=_bars())
    assert out["available"] is True and out["legs"] == [] and out["reason"] == "No short option leg"
    assert "not a forecast or a recommendation" in out["note"]


# --- the builder and the proposal card ---------------------------------------------------------------------------------

def test_builder_evaluate_carries_the_breach_block_only_with_daily_bars():
    h = {"Authorization": f"Bearer {_register(f'z5-sb-{uuid.uuid4().hex[:6]}@example.com')}"}
    as_of = date(2026, 3, 2)
    legs = [{"direction": "SELL", "option_type": "PE", "strike": 17800, "premium": 40, "lots": 1, "lot_size": 50,
             "expiry": (as_of + timedelta(days=3)).isoformat(), "iv": 0.14},
            {"direction": "BUY", "option_type": "PE", "strike": 17600, "premium": 20, "lots": 1, "lot_size": 50,
             "expiry": (as_of + timedelta(days=3)).isoformat(), "iv": 0.15}]
    spot = BARS["close"][-1]
    base = {"legs": legs, "spot": spot, "as_of": as_of.isoformat()}
    plain = client.post("/api/options-builder/evaluate", json=base, headers=h)
    assert plain.status_code == 200 and plain.json()["strike_breach"] is None
    out = client.post("/api/options-builder/evaluate", json={**base, "daily_bars": _bars(), "structure": 1}, headers=h).json()["strike_breach"]
    assert out["available"] is True and len(out["legs"]) == 1 and out["legs"][0]["strike"] == 17800
    assert out["legs"][0] == sb.report(legs, spot=spot, as_of=as_of, bars=_bars(), structure=1)["legs"][0]
    bad = client.post("/api/options-builder/evaluate", json={**base, "daily_bars": [{"high": 1, "low": 1, "close": -1}]}, headers=h)
    assert bad.status_code == 422


def test_proposal_card_shows_the_breach_block_with_the_flag(monkeypatch):
    from app.execution import approval
    from tests.test_z3_approval_entries import _sig
    from tests.test_z5_p1d_risk_by_default import _flag, _org, _tenant_t

    t = _tenant_t("card-sb")
    _org(t)
    block = sb.report([{"direction": "SELL", "option_type": "PE", "strike": 17800, "expiry": "2026-03-05"}], spot=BARS["close"][-1],
                      as_of=date(2026, 3, 2), bars=_bars())

    async def propose():
        from tests.test_auth_api import _session_factory
        async with _session_factory() as session:
            row = await approval.propose_entry(session, t["tenant_id"], signal=_sig(), strategy_id="x", source="tradingview",
                                               idempotency_key=f"sb:{uuid.uuid4().hex}", user_id=t["user_id"], strike_breach=block)
            return row.id
    action_id = asyncio.run(propose())

    def card():
        rows = client.get("/api/ai/actions", headers=t["headers"]).json()
        return next(r for r in rows if r["id"] == action_id)
    assert card()["strike_breach"] is None and card()["risk_check"] is None          # flag off: nothing new on the card
    _flag("risk_by_default", t["tenant_id"])
    shown = card()
    assert shown["strike_breach"]["legs"][0]["strike"] == 17800
    assert shown["risk_check"]["sizing"]["suggested_quantity"] > 0 and shown["risk_check"]["costs"]["total"] > 0
