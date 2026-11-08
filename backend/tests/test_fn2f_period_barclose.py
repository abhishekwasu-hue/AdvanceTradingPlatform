"""FN2-f: weekly (1w) and monthly (1M) alert rules run at their bar close - the close of the period's LAST trading
session on the exchange calendar (weekends and holidays skipped) - on a bar built from Daily bars that reach that session.
Never a partial week or month, never a session after the period (no look-ahead), never intrabar."""
from datetime import UTC, date, timedelta

import pandas as pd

from app.alerts import barclose, periods
from app.alerts.barclose import PERIOD_INTRABAR_REFUSAL, expected_bar, forming_bar, period_frame
from app.alerts.staleness import alert_badge, closed_bars_after
from app.screener.runtime import SymbolData
from tests.test_auth_api import client
from tests.test_phase_l_ai import _owner
from tests.test_s4a_barclose import _evaluate, _events, _rule, ist, utc
from tests.test_s4b2_intrabar import _flags

MON, FRI, SAT = date(2026, 3, 9), date(2026, 3, 13), date(2026, 3, 14)
PREV_MON = date(2026, 3, 2)


def _daily(days, closes) -> pd.DataFrame:
    """Daily bars labelled at 00:00 IST, as brokers stamp them."""
    idx = pd.DatetimeIndex([pd.Timestamp(utc(ist(d, 0, 0))) for d in days])
    return pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes, "volume": [1000.0] * len(closes)}, index=idx, dtype=float)


def _sessions(start: date, end: date, holidays=()):
    out, d = [], start
    while d <= end:
        if d.weekday() < 5 and d not in set(holidays):
            out.append(d)
        d += timedelta(days=1)
    return out


class DailyFetch:
    """Serves Daily bars only and records the timeframe it was asked for."""
    def __init__(self, frames):
        self.frames, self.asked = frames, []

    async def __call__(self, session, tenant_id, symbols, exchange, base_tf, lookback):
        self.asked.append(base_tf)
        return [SymbolData(s, {base_tf: self.frames[s]}) for s in symbols if s in self.frames], {}, "test"


def test_a_week_closes_at_its_last_session():
    assert expected_bar(ist(FRI, 15, 29), "1w") == utc(ist(PREV_MON, 0, 0))                   # this week is still open
    assert expected_bar(ist(FRI, 15, 30), "1w") == utc(ist(MON, 0, 0))                        # Friday's close ends it
    assert expected_bar(ist(SAT, 12, 0), "1w") == utc(ist(MON, 0, 0))
    assert expected_bar(ist(date(2026, 3, 16), 15, 0), "1w") == utc(ist(MON, 0, 0))           # next Monday, in session
    # a Friday holiday: Thursday is the week's last session
    assert expected_bar(ist(date(2026, 3, 12), 15, 30), "1w", holidays={FRI}) == utc(ist(MON, 0, 0))
    assert expected_bar(ist(date(2026, 3, 12), 15, 30), "1w") == utc(ist(PREV_MON, 0, 0))
    # a week without one session has no bar: the week before it is the latest
    whole = set(_sessions(MON, FRI))
    assert expected_bar(ist(SAT, 12, 0), "1w", holidays=whole) == utc(ist(PREV_MON, 0, 0))


def test_a_month_closes_at_its_last_session():
    last = date(2026, 3, 31)                                                                    # a Tuesday
    assert expected_bar(ist(last, 15, 29), "1M") == utc(ist(date(2026, 2, 1), 0, 0))
    assert expected_bar(ist(last, 15, 30), "1M") == utc(ist(date(2026, 3, 1), 0, 0))
    assert expected_bar(ist(date(2026, 3, 30), 15, 30), "1M", holidays={last}) == utc(ist(date(2026, 3, 1), 0, 0))
    assert expected_bar(ist(date(2026, 3, 1), 10, 0), "1M") == utc(ist(date(2026, 2, 1), 0, 0))  # a Sunday: February closed on the 27th
    assert periods.last_session(date(2026, 2, 10), "1M") == date(2026, 2, 27)
    assert periods.period_end(date(2026, 12, 5), "1M") == date(2026, 12, 31)


def test_weeks_and_months_never_form_intrabar():
    assert forming_bar(ist(FRI, 11, 0), "1w") is None and forming_bar(ist(FRI, 11, 0), "1M") is None


def test_the_stale_gate_counts_closed_weeks_and_months():
    week = utc(ist(PREV_MON, 0, 0))
    assert closed_bars_after(week, "1w", ist(FRI, 15, 29)) == 0
    assert closed_bars_after(week, "1w", ist(FRI, 15, 30)) == 1
    assert closed_bars_after(week, "1w", ist(date(2026, 4, 6), 12, 0)) == 4
    assert closed_bars_after(week, "1w", ist(date(2026, 4, 6), 12, 0), cap=2) == 2
    assert not alert_badge(week, "1w", ist(SAT, 12, 0))["alerts_suppressed"]
    assert alert_badge(week, "1w", ist(date(2026, 4, 11), 12, 0))["alerts_suppressed"]          # more than three newer weeks
    month = utc(ist(date(2026, 1, 1), 0, 0))
    assert closed_bars_after(month, "1M", ist(date(2026, 3, 31), 15, 30)) == 2                   # February and March
    assert closed_bars_after(month, "1M", ist(date(2026, 3, 31), 15, 29)) == 1


def test_period_frame_needs_the_last_session_and_reads_nothing_after_it():
    days = _sessions(PREV_MON, FRI)
    daily = _daily(days, [90.0 + i for i in range(len(days))])
    expected = utc(ist(MON, 0, 0))
    frame = period_frame(daily, "1w", expected, FRI)
    assert frame is not None and frame.index[-1] == pd.Timestamp(expected) and len(frame) == 2
    assert frame.iloc[-1]["close"] == daily.iloc[-1]["close"] and frame.iloc[-1]["volume"] == 5000
    assert period_frame(daily.iloc[:-1], "1w", expected, FRI) is None                           # Friday not in yet: wait
    later = pd.concat([daily, _daily([date(2026, 3, 16)], [10.0])])                            # next Monday is in the data
    cut = period_frame(later, "1w", expected, FRI)
    assert cut is not None and cut.index[-1] == pd.Timestamp(expected) and cut.iloc[-1]["close"] == daily.iloc[-1]["close"]
    assert cut.iloc[-1]["low"] == daily.iloc[5:]["low"].min()                                  # the next week's 10 never leaks in


def test_a_weekly_rule_fires_once_at_the_weeks_close():
    _, me = _owner("fn2f-weekly@example.com")
    rid = _rule(me["tenant_id"], tf="1w", condition="close > 100")
    days = _sessions(PREV_MON, FRI)
    closes = [99.0] * (len(days) - 1) + [105.0]
    fetch = DailyFetch({"TCS": _daily(days[:-1], closes[:-1])})
    # Friday 15:31, but the Daily data still ends on Thursday: wait, never a partial week
    assert _evaluate(rid, ist(FRI, 15, 31), fetch) == (("waiting", 0), "waiting for the closed bar from the broker")
    assert fetch.asked == ["1d"]                                                                # weeks are built from Daily bars
    fetch.frames["TCS"] = pd.concat([_daily(days, closes), _daily([date(2026, 3, 16)], [50.0])])  # plus a later session
    assert _evaluate(rid, ist(FRI, 15, 33), fetch) == (("done", 1), None)
    ev = _events(rid)
    assert len(ev) == 1 and ev[0].bar_time.replace(tzinfo=UTC) == utc(ist(MON, 0, 0))
    assert ev[0].values_json and '"close": 105.0' in ev[0].values_json
    assert _evaluate(rid, ist(SAT, 12, 0), fetch)[0] == ("skipped", 0)                         # the same week: once only


def test_a_monthly_rule_runs_on_the_month_close():
    _, me = _owner("fn2f-monthly@example.com")
    rid = _rule(me["tenant_id"], tf="1M", condition="close > 100")
    days = _sessions(date(2026, 2, 2), date(2026, 3, 31))
    fetch = DailyFetch({"TCS": _daily(days, [99.0] * (len(days) - 1) + [101.0])})
    assert _evaluate(rid, ist(date(2026, 3, 31), 15, 31), fetch) == (("done", 1), None)
    assert _events(rid)[0].bar_time.replace(tzinfo=UTC) == utc(ist(date(2026, 3, 1), 0, 0))


def test_weekly_intrabar_is_refused_by_the_engine_and_the_api():
    _, me = _owner("fn2f-intrabar@example.com")
    rid = _rule(me["tenant_id"], tf="1w", fire_on="intrabar")
    fetch = DailyFetch({})
    assert _evaluate(rid, ist(FRI, 11, 0), fetch, intrabar_enabled=True) == (("unsupported", 0), PERIOD_INTRABAR_REFUSAL)
    assert fetch.asked == []
    headers, _ = _owner("fn2f-api@example.com")
    body = {"name": "w", "kind": "instrument", "symbol": "TCS", "condition": "close > 100", "base_tf": "1w"}
    _flags(screener_v2=True, screener_intrabar=True)
    try:
        refused = client.post("/api/alerts/rules", headers=headers, json={**body, "fire_on": "intrabar"})
        assert refused.status_code == 422 and refused.json()["detail"]["message"] == PERIOD_INTRABAR_REFUSAL
        made = client.post("/api/alerts/rules", headers=headers, json=body)
        assert made.status_code == 201 and made.json()["base_tf"] == "1w"
    finally:
        _flags(screener_v2=False, screener_intrabar=False)


def test_supported_timeframes_include_weeks_and_months():
    assert {"1w", "1M"} <= barclose.SUPPORTED
