"""UI-H bug lane B3 + B4 (docs/ui/UI_AUDIT.md).

B3: days to expiry are TRADING days from app/market_data/calendar.py - on the chain page's summary and in the expected
move text (the analyses and "What the chain shows"). Saturday -> Tuesday = 2.
B4: the PCR zone edges of "What the chain shows" are the user's own settings (prefs namespace `option_chain`), with
defaults equal to the values used before (oi_regime.PcrBands: 0.70 / 0.90 / 1.00 / 1.30).
"""
from datetime import date, datetime

import uuid

import pytest

from app.brokers.models import OptionChain, OptionChainRow
from app.market_data.calendar import trading_days_until
from app.me.prefs import NAMESPACES, OptionChainPrefs, defaults, stored_value
from app.option_chain import analyses, chain_view, dish
from app.option_chain.oi_regime import PcrBands
from app.option_chain.snapshots import IST

SAT, SUN, MON, TUE, WED = (date(2026, 10, d) for d in (24, 25, 26, 27, 28))


def test_trading_days_leave_out_weekends_and_holidays():
    assert trading_days_until(SAT, TUE) == 2                       # the owner's example: Mon, Tue
    assert trading_days_until(SUN, TUE) == 2
    assert trading_days_until(MON, TUE) == 1
    assert trading_days_until(TUE, TUE) == 0                       # expiry day
    assert trading_days_until(SAT, TUE, holidays={MON}) == 1       # an exchange holiday does not count
    assert trading_days_until(WED, TUE) == -1                      # passed: still negative, as before
    assert trading_days_until(date(2026, 10, 21), TUE) == 4        # Wed -> Tue: Thu, Fri, Mon, Tue


def _chain(expiry: date) -> OptionChain:
    rows = [OptionChainRow(strike=s, call_ltp=c, put_ltp=p, call_oi=100.0, put_oi=120.0)
            for s, c, p in ((100.0, 20.0, 2.0), (110.0, 10.0, 6.0), (120.0, 4.0, 14.0))]
    return OptionChain(underlying="NIFTY", expiry=expiry.isoformat(), underlying_ltp=112.0, rows=rows)


def test_chain_summary_and_expected_move_count_trading_days_on_a_saturday():
    sat = datetime(2026, 10, 24, 11, 0, tzinfo=IST)
    chain = _chain(TUE)
    oi = chain_view.OiChange(status="ready", source="broker")
    view = chain_view.build_view(chain, spot=112.0, future=None, expiry=TUE, now=sat, oi=oi, holidays=())
    assert view["summary"]["days_to_expiry"] == 2
    assert chain_view.build_view(chain, spot=112.0, future=None, expiry=TUE, now=sat, oi=oi, holidays={MON})["summary"]["days_to_expiry"] == 1
    move = analyses.expected_move(chain, [], sat, holidays=())
    assert move["values"]["dte"] == 2 and move["sentences"][1].endswith("with 2 trading days to expiry.")
    results = analyses.compute_all(chain, [], sat, holidays={MON})
    em = next(r for r in results if r["key"] == "expected_move")
    assert em["values"]["dte"] == 1 and em["sentences"][1].endswith("with 1 trading day to expiry.")
    line = next(p for p in dish.build(results, chain, [])["points"] if p["key"] == "expected_move")
    assert line["text"].endswith("with 1 trading day to expiry.")


def test_pcr_zone_defaults_equal_the_values_used_before():
    b = PcrBands()
    assert dish.DEFAULT_PCR_EDGES == (b.oversold_below, b.mild_bearish_below, b.sideways_max, b.mild_bullish_max) == (0.70, 0.90, 1.0, 1.3)
    assert "option_chain" in NAMESPACES
    assert defaults("option_chain") == {"pcr_zones": {"very_low_below": 0.70, "low_below": 0.90, "balanced_max": 1.0, "high_max": 1.3},
                                        "expiry_day_next": True}       # P1 27 Oct: the next expiry on an expiry day
    assert OptionChainPrefs().pcr_zones.edges() == dish.DEFAULT_PCR_EDGES
    assert [z[0] for z in dish.PCR_ZONES] == [0.70, 0.90, 1.0, 1.3, None]


def test_the_users_own_edges_word_the_pcr_line():
    assert dish.pcr_zone(1.1) == ("high", "1.00 to 1.30", "put open interest is larger than call open interest")
    assert dish.pcr_zone(1.1, (0.5, 0.8, 1.2, 1.5)) == ("balanced", "0.80 to 1.20", "put and call open interest are close to each other")
    assert dish.pcr_zone(0.4, (0.5, 0.8, 1.2, 1.5))[0] == "very low" and dish.pcr_zone(1.6, (0.5, 0.8, 1.2, 1.5))[0] == "very high"
    results = [{"key": "pcr", "state": "ok", "values": {"overall": 1.1}}]
    chain = _chain(TUE)
    own = next(p for p in dish.build(results, chain, [], pcr_edges=(0.5, 0.8, 1.2, 1.5))["points"] if p["key"] == "pcr_zone")
    std = next(p for p in dish.build(results, chain, [])["points"] if p["key"] == "pcr_zone")
    assert "balanced zone (0.80 to 1.20)" in own["text"] and "high zone (1.00 to 1.30)" in std["text"]


@pytest.mark.parametrize("bad", [
    {"pcr_zones": {"very_low_below": 0.9, "low_below": 0.7, "balanced_max": 1.0, "high_max": 1.3}},     # not ascending
    {"pcr_zones": {"very_low_below": 0.0, "low_below": 0.7, "balanced_max": 1.0, "high_max": 1.3}},     # not above 0
    {"pcr_zones": {"very_low_below": 0.7, "low_below": 0.7, "balanced_max": 1.0, "high_max": 1.3}},     # equal edges
    {"pcr_zones": {"other": 1}},
])
def test_bad_edges_are_refused_and_a_bad_stored_value_falls_back(bad):
    import json

    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        OptionChainPrefs.model_validate_json(json.dumps(bad))
    assert stored_value("option_chain", json.dumps(bad)) == defaults("option_chain")


def test_the_prefs_route_saves_and_reads_his_own_edges():
    from tests.test_auth_api import _register, client
    headers = {"Authorization": f"Bearer {_register(f'b4-{uuid.uuid4().hex[:8]}@example.com')}"}
    body = {"pcr_zones": {"very_low_below": 0.5, "low_below": 0.8, "balanced_max": 1.2, "high_max": 1.5}}
    put = client.put("/api/me/prefs/option_chain", headers=headers, json=body)
    assert put.status_code == 200, put.text
    assert client.get("/api/me/prefs", headers=headers).json()["prefs"]["option_chain"] == {**body, "expiry_day_next": True}
    bad = {"pcr_zones": {"very_low_below": 1.5, "low_below": 0.8, "balanced_max": 1.2, "high_max": 1.5}}
    assert client.put("/api/me/prefs/option_chain", headers=headers, json=bad).status_code == 422
