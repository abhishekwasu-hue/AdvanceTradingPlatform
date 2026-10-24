"""OC5 (owner's order, handover 2b + 2c): the Option chain "Charts" series (app/option_chain/charts.py), the reading facts
(PCR zone, days to expiry, IV change over recent sessions, the 5 / 10 / 15-minute views), `GET /api/option-chain/charts`
(app/option_chain/charts_routes.py, behind `ui_v2`), the collector's futures price / OI and its migration.

Every expected number is worked out by hand in the comments from small made-up chains (strikes 100-140, spot about 118,
an underlying called DEMO) - no real market data."""
import asyncio
import importlib.util
import math
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple

import pytest

from app.brokers.models import OptionChain, OptionChainRow
from app.option_chain import charts as C
from app.option_chain import oi_regime, snapshots
from app.option_chain.oi_regime import OIRegimeSettings, PcrBand
from app.portfolio.risk_text import has_advice
from app.screener.taxonomy import advice_words
from tests.test_amw2_watchlists import _set_flag
from tests.test_auth_api import _session_factory, client
from tests.test_oc2_option_chain_analyses import _ChainBroker, _install, _usage
from tests.test_phase_aa_market_data_api import _store_credentials
from tests.test_phase_k_commercial import _owner

UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))
EXPIRY = "2026-10-27"
NOW = datetime(2026, 10, 21, 4, 11, tzinfo=UTC)                   # 09:41 IST, 6 days before the expiry
SLOTS = [datetime(2026, 10, 21, 3, 50 + 5 * i, tzinfo=UTC) for i in range(2)] + [
    datetime(2026, 10, 21, 4, 5 * i, tzinfo=UTC) for i in range(2)]   # 09:20, 09:25, 09:30, 09:35 IST
URL = "/api/option-chain/charts"
run = asyncio.run


def row(strike, call_oi=None, put_oi=None, call_ltp=None, put_ltp=None, call_iv=None, put_iv=None, **kw) -> OptionChainRow:
    return OptionChainRow(strike=strike, call_oi=call_oi, put_oi=put_oi, call_ltp=call_ltp, put_ltp=put_ltp, call_iv=call_iv, put_iv=put_iv, **kw)


def chain(rows, spot, expiry=EXPIRY, underlying="DEMO") -> OptionChain:
    return OptionChain(underlying=underlying, expiry=expiry, underlying_ltp=spot, rows=rows)


def session_chain(atm_call: float, atm_put: float, spot: float, straddle: Tuple[float, float]) -> OptionChain:
    """The stored readings: only the 120 strike's OI and price move. 100 (10, 50), 110 (20, 40), 130 (60, 10), 140 (30, 5)."""
    return chain([row(100, 10, 50), row(110, 20, 40), row(120, atm_call, atm_put, *straddle), row(130, 60, 10), row(140, 30, 5)], spot)


# (120 call OI, 120 put OI, spot, (120 call price, 120 put price)) at 09:20, 09:25, 09:30, 09:35 IST
STORED = [session_chain(40, 30, 116.0, (6, 5)), session_chain(45, 35, 117.0, (6.5, 5)), session_chain(50, 40, 117.5, (7, 5)),
          session_chain(55, 45, 118.0, (7, 5.5))]
FUTURES = [(120.5, 1000.0), (121.0, 1100.0), (121.5, 1200.0), (122.0, 1300.0)]
CURRENT = chain([row(100, 15, 50), row(110, 30, 45), row(120, 35, 60, 7, 4), row(130, 50, 10), row(140, 40, 3)], 118.0)
# Today's first reading (09:20) per strike: the collector's oi_day_baselines.
BASELINES = {100.0: (10.0, 50.0), 110.0: (20.0, 40.0), 120.0: (40.0, 30.0), 130.0: (60.0, 10.0), 140.0: (30.0, 5.0)}


def readings() -> List[C.Reading]:
    return [C.Reading(at, ch, fp, fo) for at, ch, (fp, fo) in zip(SLOTS, STORED, FUTURES)]


def by_key(body) -> Dict[str, dict]:
    return {c["key"]: c for c in body["charts"]}


def ys(chart, key) -> List[Optional[float]]:
    return [p["y"] for p in next(s for s in chart["series"] if s["key"] == key)["points"]]


# ---------------------------------------------------------------- the window and the charts by strike

def test_the_window_is_atm_and_the_chosen_listed_strikes_either_side():
    out = C.compute(CURRENT, [], NOW, each_side=1)
    assert out["window"] == {"each_side": 1, "strikes": [110, 120, 130], "atm": 120}
    assert C.compute(CURRENT, [], NOW, each_side=10)["window"]["strikes"] == [100, 110, 120, 130, 140]   # the chain's ends clip it
    assert [c["key"] for c in out["charts"]] == list(C.KEYS)


def test_oi_by_strike_and_todays_change_against_the_first_reading():
    out = C.compute(CURRENT, readings(), NOW, each_side=1, baselines=BASELINES)
    charts = by_key(out)
    oi = charts["oi_by_strike"]
    assert oi["state"] == "ok" and oi["type"] == "bar" and oi["marker"] == {"x": 118.0, "label": "Spot"}
    assert ys(oi, "call_oi") == [30, 35, 50] and ys(oi, "put_oi") == [45, 60, 10]
    # now - first reading: 110 (30-20, 45-40), 120 (35-40, 60-30), 130 (50-60, 10-10)
    assert [p["d"] for p in oi["series"][0]["points"]] == [10, -5, -10] and [p["d"] for p in oi["series"][1]["points"]] == [5, 30, 0]
    change = charts["oi_change_by_strike"]
    assert change["change_basis"] == "first_reading" and ys(change, "call_change") == [10, -5, -10] and ys(change, "put_change") == [5, 30, 0]
    assert change["caption"].endswith("since the first reading at 09:20 IST.")


def test_oi_change_falls_back_to_the_brokers_own_change_and_else_builds():
    broker = chain([row(110, 30, 45, call_change_oi=3, put_change_oi=-2), row(120, 35, 60), row(130, 50, 10, put_change_oi=4)], 118.0)
    change = by_key(C.compute(broker, [], NOW, each_side=1))["oi_change_by_strike"]
    assert change["change_basis"] == "previous_session" and ys(change, "call_change") == [3, None, None] and ys(change, "put_change") == [-2, None, 4]
    assert "previous session" in change["caption"]
    none = by_key(C.compute(CURRENT, [], NOW, each_side=1))
    assert none["oi_change_by_strike"]["state"] == "building" and none["oi_change_by_strike"]["message"] == C.NO_CHANGE
    assert none["oi_by_strike"]["state"] == "ok" and none["oi_by_strike"]["change_basis"] is None
    # A strike the first reading never stored has no change (never 0).
    partial = C.oi_change(CURRENT, C.window(CURRENT, 1), {120.0: (40.0, 30.0)})
    assert partial == ({110: (None, None), 120: (-5, 30), 130: (None, None)}, "first_reading")


def test_iv_smile_now_reads_the_quote_and_builds_without_any_iv():
    with_iv = chain([row(110, 1, 1, call_iv=14.0, put_iv=16.0), row(120, 1, 1, call_iv=12.0, put_iv=0.13), row(130, 1, 1, call_iv=11.0)], 118.0)
    smile = by_key(C.compute(with_iv, [], NOW, each_side=1))["iv_smile"]
    # a quote of 3 or more is a percentage, below 3 a fraction (0.13 -> 13 %); a missing quote stays missing
    assert smile["state"] == "ok" and ys(smile, "call_iv") == [14.0, 12.0, 11.0] and ys(smile, "put_iv") == [16.0, 13.0, None]
    no_iv = chain([row(110, 1, 1), row(120, 1, 1), row(130, 1, 1)], 118.0)        # no quote and no price to solve from
    assert by_key(C.compute(no_iv, [], NOW, each_side=1))["iv_smile"]["message"] == C.NO_IV
    solved = by_key(C.compute(CURRENT, [], NOW, each_side=1))["iv_smile"]               # 120's prices give an IV by themselves
    assert solved["state"] == "ok" and ys(solved, "call_iv")[0] is None and ys(solved, "call_iv")[1] > 0


# ---------------------------------------------------------------- the session charts and the 5 / 10 / 15-minute views

def test_session_readings_keep_this_expiry_with_a_spot_and_bucket_by_interval():
    other = C.Reading(SLOTS[1] + timedelta(minutes=1), chain(STORED[0].rows, 116.0, expiry="2026-11-03"))
    no_spot = C.Reading(SLOTS[2] + timedelta(minutes=1), chain(STORED[0].rows, 0.0))
    later = C.Reading(NOW + timedelta(minutes=1), STORED[0])                       # read after NOW: never part of it
    slots = readings() + [other, no_spot, later]
    five = C.session_readings(CURRENT, [C.Reading(s.at, C.A.clean_spot(s.chain)) for s in slots], NOW, 5)
    assert [r.at for r in five] == SLOTS + [NOW] and five[-1].stored is False
    # 10 minutes: 09:20 bucket keeps 09:25, 09:30 bucket keeps 09:35, 09:40 bucket is the read now
    assert [r.at for r in C.session_readings(CURRENT, readings(), NOW, 10)] == [SLOTS[1], SLOTS[3], NOW]
    # 15 minutes: 09:15 bucket keeps 09:25; 09:30 bucket (09:30, 09:35, 09:41) keeps the read now
    assert [r.at for r in C.session_readings(CURRENT, readings(), NOW, 15)] == [SLOTS[1], NOW]
    with pytest.raises(ValueError):
        C.compute(CURRENT, [], NOW, interval=7)


def test_cumulative_oi_pcr_max_pain_and_straddle_through_the_session():
    charts = by_key(C.compute(CURRENT, readings(), NOW, each_side=1, baselines=BASELINES))
    cum = charts["cumulative_oi"]
    # over 110, 120, 130: calls 20+40+60, 20+45+60, 20+50+60, 20+55+60, now 30+35+50; puts 40+30+10 ... now 45+60+10
    assert ys(cum, "call_oi") == [120, 125, 130, 135, 115] and ys(cum, "put_oi") == [80, 85, 90, 95, 115]
    assert ys(cum, "spot") == [116.0, 117.0, 117.5, 118.0, 118.0]
    assert next(s for s in cum["series"] if s["key"] == "spot")["axis"] == "right" and cum["y2_label"] == "Spot"
    assert [p["x"] for p in cum["series"][0]["points"]] == [t.isoformat() for t in SLOTS + [NOW]]
    # 80/120, 85/125, 90/130, 95/135, 115/115
    assert ys(charts["pcr_session"], "pcr") == [0.67, 0.68, 0.69, 0.70, 1.0]
    assert "Zone now: Sideways (range-bound)." in charts["pcr_session"]["caption"]
    # 09:20 max pain: K=110 -> 100 + 650 = 750, K=120 -> 400 + 200 = 600, K=130 -> 1100 + 50 = 1150, so 120; now also 120
    assert ys(charts["max_pain_session"], "max_pain") == [120, 120, 120, 120, 120]
    assert ys(charts["straddle_session"], "straddle") == [11.0, 11.5, 12.0, 12.5, 11.0]
    # no IV is quoted: each reading's ATM IV is solved from its ATM prices (a number above 0 at every reading)
    assert charts["atm_iv_session"]["state"] == "ok" and all(v > 0 for v in ys(charts["atm_iv_session"], "atm_iv"))
    no_prices = [C.Reading(r.at, chain([x.model_copy(update={"call_ltp": None, "put_ltp": None}) for x in r.chain.rows], r.chain.underlying_ltp))
                 for r in readings()]
    assert by_key(C.compute(chain([row(120, 1, 1)], 118.0), no_prices, NOW))["atm_iv_session"]["message"] == C.NO_IV
    ten = by_key(C.compute(CURRENT, readings(), NOW, each_side=1, interval=10))
    assert ys(ten["pcr_session"], "pcr") == [0.68, 0.70, 1.0]


def test_multi_strike_oi_opens_on_atm_and_one_strike_either_side():
    multi = by_key(C.compute(CURRENT, readings(), NOW, each_side=2))["multi_strike_oi"]
    assert multi["strikes"] == [100, 110, 120, 130, 140] and multi["default_strikes"] == [110, 120, 130]
    assert ys(multi, "call_120") == [40, 45, 50, 55, 35] and ys(multi, "put_120") == [30, 35, 40, 45, 60]
    put = next(s for s in multi["series"] if s["key"] == "put_120")
    assert put["dashed"] is True and put["strike"] == 120 and put["label"] == "120 put OI"


def test_futures_price_vs_oi_from_the_stored_readings_only():
    fut = by_key(C.compute(CURRENT, readings(), NOW, each_side=1))["futures"]
    assert fut["state"] == "ok" and ys(fut, "future_price") == [120.5, 121.0, 121.5, 122.0] and ys(fut, "future_oi") == [1000, 1100, 1200, 1300]
    assert next(s for s in fut["series"] if s["key"] == "future_oi")["axis"] == "right"
    assert [p["y"] for p in by_key(C.compute(CURRENT, readings(), NOW, each_side=1, interval=10))["futures"]["series"][0]["points"]] == [121.0, 122.0]
    bare = [C.Reading(r.at, r.chain) for r in readings()]                          # slots stored before the futures columns
    assert by_key(C.compute(CURRENT, bare, NOW))["futures"]["message"] == C.NO_FUTURE


def test_early_in_the_session_every_path_says_it_builds_never_a_number():
    out = C.compute(CURRENT, [], NOW, each_side=1)
    charts = by_key(out)
    for key in ("multi_strike_oi", "cumulative_oi", "pcr_session", "max_pain_session", "straddle_session", "atm_iv_session", "futures"):
        assert charts[key]["state"] == "building" and charts[key]["series"] == [] and charts[key]["message"] == C.BUILDING, key
    assert out["session"] == {"readings": 0, "since": None, "note": C.BUILDING}
    off = by_key(C.compute(CURRENT, [], NOW, history_off=True))
    assert off["cumulative_oi"]["message"] == C.HISTORY_OFF and off["futures"]["message"] == C.HISTORY_OFF
    assert C.compute(CURRENT, [], NOW, history_off=True)["session"]["note"] == C.HISTORY_OFF


# ---------------------------------------------------------------- 2b: the reading facts

def _iv_chain(call_iv, put_iv, spot=118.0, expiry=EXPIRY):
    return chain([row(110, 10, 10), row(120, 10, 10, call_iv=call_iv, put_iv=put_iv), row(130, 10, 10)], spot, expiry=expiry)


def _earlier(day: date, iv: float, prices) -> C.EarlierSession:
    at = datetime(day.year, day.month, day.day, 9, 55, tzinfo=UTC)               # 15:25 IST, the day's last reading
    return C.EarlierSession(day, at, _iv_chain(iv, iv, expiry="2026-10-20"), tuple(prices))


def test_facts_days_to_expiry_pcr_zone_and_the_iv_change_over_recent_sessions():
    earlier = [_earlier(date(2026, 10, 16), 10.0, (100, 104, 99, 100.5)),      # body 0.5 of a 5-point range: sideways
               _earlier(date(2026, 10, 19), 12.0, (100, 101, 99.5, 100.2)),    # sideways
               _earlier(date(2026, 10, 20), 14.0, (100, 110))]                  # body = range: trending, left out of the sideways baseline
    out = C.facts(_iv_chain(14.0, 16.0), C.window(_iv_chain(14.0, 16.0), 1), NOW, OIRegimeSettings(), earlier)
    assert out["expiry"] == EXPIRY and out["dte"] == 6
    assert out["pcr"] == 1.0 and out["pcr_band"] == PcrBand.SIDEWAYS.value and out["pcr_zone"] == "Sideways (range-bound)"
    change = out["iv_change"]
    # now (14 + 16) / 2 = 15; the 3 closes average (10 + 12 + 14) / 3 = 12 -> +25 %
    assert change["atm_iv"] == 15.0 and change["average"] == 12.0 and change["change_pct"] == 25.0
    assert [s["day"] for s in change["sessions"]] == ["2026-10-16", "2026-10-19", "2026-10-20"]
    # the Trade repo's baseline: sideways sessions only, (10 + 12) / 2 = 11 -> (15 - 11) / 11 = +36.36 %
    assert change["sideways_baseline"] == {"average": 11.0, "change_pct": 36.36, "sessions": 2}
    assert out["sentences"] == ["6 days to expiry.", "PCR 1.00 over the 3 strikes on screen: Sideways (range-bound).",
                                "ATM IV 15.00% is 25.0% above its average close of 12.00% over the last 3 sessions."]


def test_facts_without_earlier_sessions_or_iv_say_so_plainly():
    out = C.facts(_iv_chain(14.0, 16.0), C.window(_iv_chain(14.0, 16.0), 1), NOW, OIRegimeSettings(), [])
    assert out["iv_change"] is None and out["sentences"][-1].startswith("ATM IV is 15.00%; the change against earlier sessions shows")
    oi_only = chain([row(110, 30, 45), row(120, 35, 60), row(130, 50, 10)], 118.0)
    bare = C.facts(oi_only, oi_only.rows, NOW, OIRegimeSettings(), [])
    assert bare["atm_iv"] is None and len(bare["sentences"]) == 2
    expiry_day = C.facts(chain(CURRENT.rows, 118.0, expiry="2026-10-21"), C.window(CURRENT, 1), NOW, OIRegimeSettings(), [])
    assert expiry_day["dte"] == 0 and expiry_day["sentences"][0] == "Expiry is today."
    no_calls = C.facts(chain([row(120, 0, 5)], 118.0), [row(120, 0, 5)], NOW, OIRegimeSettings(), [])
    assert no_calls["pcr"] is None and no_calls["pcr_zone"] == "Not available"


def test_the_pcr_zone_follows_the_organisations_bands():
    bands = OIRegimeSettings(pcr_bands={"oversold_below": 0.5, "mild_bearish_below": 0.6, "sideways_max": 0.8, "mild_bullish_max": 0.9})
    out = C.facts(CURRENT, C.window(CURRENT, 1), NOW, bands, [])
    assert out["pcr"] == 1.0 and out["pcr_band"] == PcrBand.OVERBOUGHT.value
    assert out["pcr_zone"] == oi_regime.compute_pcr_zone_label(1.0, bands.pcr_bands)


def test_no_text_carries_advice_wording():
    lines = [C.BUILDING, C.HISTORY_OFF, C.NO_FUTURE, C.NO_CHANGE, C.NO_IV]
    lines += [oi_regime.compute_pcr_zone_label(p) for p in (0.5, 0.8, 0.95, 1.2, 1.5)]
    full = C.compute(CURRENT, readings(), NOW, each_side=1, baselines=BASELINES)
    empty = C.compute(CURRENT, [], NOW, each_side=1)
    for body in (full, empty):
        for c in body["charts"]:
            lines += [c["title"], c["caption"], c["message"] or "", c["y_label"], c["y2_label"] or ""] + [s["label"] for s in c["series"]]
        lines += body["facts"]["sentences"]
    lines += C.facts(_iv_chain(14.0, 16.0), C.window(_iv_chain(14.0, 16.0), 1), NOW, OIRegimeSettings(),
                     [_earlier(date(2026, 10, 20), 12.0, (100, 101))])["sentences"]
    for line in lines:
        assert not has_advice(line) and advice_words(line) == [], line


# ---------------------------------------------------------------- the collector stores the future's price and OI

@pytest.fixture()
def clean_oi():
    from sqlalchemy import delete

    from app.db.models import OIDayBaselineRecord, OISnapshotRecord, StrikeOISnapshotRecord

    async def go():
        async with _session_factory() as session:
            for model in (StrikeOISnapshotRecord, OISnapshotRecord, OIDayBaselineRecord):
                await session.execute(delete(model))
            await session.commit()
    run(go())
    yield


ORG = 1          # TM-7: the organisation whose own session read the stored slots (pure store tests)


def test_collect_stores_the_future_and_reads_it_back(clean_oi):
    async def go():
        async with _session_factory() as session:
            await snapshots.collect(session, "DEMO", STORED[0], SLOTS[0], tenant_id=ORG, future_price=120.5, future_oi=1000.0)
            await snapshots.collect(session, "DEMO", STORED[1], SLOTS[1], tenant_id=ORG, future_price=0.0, future_oi=math.nan)    # not prices: None
            await snapshots.collect(session, "DEMO", STORED[2], SLOTS[2], tenant_id=ORG)
            await session.commit()
            return await snapshots.day_chains(session, "DEMO", date(2026, 10, 21), tenant_id=ORG)
    slots = run(go())
    assert [(s.future_price, s.future_oi) for s in slots] == [(120.5, 1000.0), (None, None), (None, None)]


def test_recent_session_closes_read_each_days_last_chain_and_its_spot_path(clean_oi):
    async def go():
        async with _session_factory() as session:
            for day, prices in ((15, (100.0, 104.0)), (16, (101.0, 99.0, 100.5)), (21, (118.0,))):
                for i, price in enumerate(prices):
                    at = datetime(2026, 10, day, 4, 5 * i, tzinfo=UTC)
                    await snapshots.collect(session, "DEMO", chain(STORED[i].rows, price), at, tenant_id=ORG)
            await session.commit()
            return await snapshots.recent_session_closes(session, "DEMO", date(2026, 10, 21), 5, tenant_id=ORG)
    closes = run(go())
    assert [c.day for c in closes] == [date(2026, 10, 15), date(2026, 10, 16)]     # before the day asked, oldest first
    assert closes[1].prices == (101.0, 99.0, 100.5) and closes[1].last.chain.underlying_ltp == 100.5
    assert [r.call_oi for r in closes[1].last.chain.rows if r.strike == 120] == [50]  # the day's last slot (STORED[2])


def test_worker_reads_the_futures_quote_and_never_fails_the_slot_on_it(monkeypatch):
    from app.option_chain import chain_view
    from app.workers.trading_worker import TradingWorker

    async def contract(session, underlying, today):
        return chain_view.FutureContract(symbol="DEMO26OCTFUT", exchange="NFO", expiry=date(2026, 10, 27))
    monkeypatch.setattr(chain_view, "near_future", contract)

    class Quoting:
        asked: List[Tuple[str, str]] = []

        async def get_quote_for_symbol(self, symbol, exchange="NSE"):
            self.asked.append((symbol, exchange))
            return SimpleNamespace(ltp=121.5, oi=1500.0)

    class Failing:
        async def get_quote_for_symbol(self, symbol, exchange="NSE"):
            raise RuntimeError("quote down")

    async def go(broker):
        async with _session_factory() as session:
            return await TradingWorker._oi_future_quote(session, broker, "DEMO", NOW)
    quoting = Quoting()
    assert run(go(quoting)) == (121.5, 1500.0) and quoting.asked == [("DEMO26OCTFUT", "NFO")]
    assert run(go(Failing())) == (None, None)
    assert run(go(object())) == (None, None)                                      # an adapter with no single-symbol quote


# ---------------------------------------------------------------- the migration

def test_migration_is_additive_rollback_safe_and_the_only_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from scripts.migration_rollback_safety import lint, risky_operations

    root = Path(__file__).resolve().parents[1]
    path = root / "alembic" / "versions" / "a7c9e1f3b5d8_oc5_oi_snapshot_futures.py"
    spec = importlib.util.spec_from_file_location("oc5_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.ROLLBACK_SAFE is True and module.revision == "a7c9e1f3b5d8" and module.down_revision == "e6b8d0f2a4c7"
    assert risky_operations(path.read_text(encoding="utf-8")) == [] and not hasattr(module, "ROLLBACK_SAFE_REASON")
    assert not [p for p in lint() if p.startswith(path.name)]
    assert callable(module.downgrade)
    script = ScriptDirectory.from_config(Config(str(root / "alembic.ini")))
    heads = script.get_heads()
    assert len(heads) == 1                                                     # one head, with OC5 on its chain
    assert "a7c9e1f3b5d8" in {rev.revision for rev in script.walk_revisions("base", heads[0])}


# ---------------------------------------------------------------- the endpoint

def _ist_today() -> date:
    return datetime.now(UTC).astimezone(IST).date()


def _name() -> str:
    return "OCFIVE" + uuid.uuid4().hex[:6].upper()


def _v2_owner(tag: str):
    headers, me = _owner(f"oc5-{tag}-{uuid.uuid4().hex[:8]}@example.com")
    _set_flag("ui_v2", me["tenant_id"], on=True)
    return headers, me


def _store(name: str, ch: OptionChain, at: datetime, future: Tuple[Optional[float], Optional[float]] = (None, None), *,
           tenant_id: int) -> None:
    async def go():
        async with _session_factory() as session:
            await snapshots.collect(session, name, ch, at, tenant_id=tenant_id, source="test", future_price=future[0], future_oi=future[1])
            await session.commit()
    run(go())


def _live(name: str, expiry: str, rows=None, spot=118.0) -> OptionChain:
    return OptionChain(underlying=name, expiry=expiry, underlying_ltp=spot, rows=rows or CURRENT.rows)


def test_flag_off_is_404_and_login_is_required(monkeypatch):
    broker = _ChainBroker({})
    _install(monkeypatch, broker)
    headers, me = _owner(f"oc5-off-{uuid.uuid4().hex[:8]}@example.com")
    _store_credentials(me)
    res = client.get(URL, headers=headers, params={"symbol": "NIFTY"})
    assert res.status_code == 404 and res.headers.get("X-Feature-Disabled") == "ui_v2" and broker.reads == []
    assert client.get(URL, params={"symbol": "NIFTY"}).status_code == 401


def test_no_broker_bad_input_and_unavailable_are_plain_states(monkeypatch):
    broker = _ChainBroker({"FAILS": RuntimeError("upstream"), "EMPTY": chain([], 100.0)})
    _install(monkeypatch, broker)
    headers, me = _v2_owner("states")
    body = client.get(URL, headers=headers, params={"symbol": "nifty"}).json()
    assert body["state"] == "no_broker" and body["charts"] == [] and body["facts"] is None and "Connect your broker" in body["message"]
    assert broker.reads == []
    for params in ({"symbol": "BAD/NAME"}, {"symbol": "NIFTY", "interval": 7}, {"symbol": "NIFTY", "strikes": 0},
                   {"symbol": "NIFTY", "strikes": C.MAX_EACH_SIDE + 1}):
        assert client.get(URL, headers=headers, params=params).status_code == 422, params
    _store_credentials(me)
    failed = client.get(URL, headers=headers, params={"symbol": "FAILS"}).json()
    assert failed["state"] == "unavailable" and failed["charts"] == [] and "RuntimeError" not in failed["message"]
    empty = client.get(URL, headers=headers, params={"symbol": "EMPTY"}).json()
    assert empty["state"] == "no_data" and empty["charts"] == []
    assert _usage(me["tenant_id"]) == 1                                         # only the chain that came back is counted


def test_an_empty_session_shows_the_chain_now_and_says_the_rest_builds(monkeypatch):
    name = _name()
    expiry = (_ist_today() + timedelta(days=6)).isoformat()
    broker = _ChainBroker({name: _live(name, expiry)})
    _install(monkeypatch, broker)
    headers, me = _v2_owner("empty")
    _store_credentials(me)
    res = client.get(URL, headers=headers, params={"symbol": name, "expiry": expiry, "strikes": 1})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["state"] == "ok" and body["symbol"] == name and body["expiry"] == expiry and body["spot"] == 118.0
    assert body["strikes"] == 1 and body["interval"] == 5 and body["window"]["strikes"] == [110, 120, 130]
    assert body["session"] == {"readings": 0, "since": None, "note": C.BUILDING}
    assert body["facts"]["dte"] == 6 and body["facts"]["pcr"] == 1.0
    charts = by_key(body)
    assert charts["oi_by_strike"]["state"] == "ok" and charts["cumulative_oi"]["state"] == "building"
    assert charts["cumulative_oi"]["message"] == C.BUILDING and charts["cumulative_oi"]["series"] == []
    assert broker.reads == [(name, date.fromisoformat(expiry))] and _usage(me["tenant_id"]) == 1


def _pin_route_clock(monkeypatch, at: datetime) -> None:
    """The route reads the session of the IST day of `datetime.now`; pin it so the test does not depend on the hour
    it runs at (slots stored 16 minutes before 00:05 IST would fall on the previous IST day)."""
    from app.option_chain import charts_routes

    class _Pinned(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return at.astimezone(tz) if tz else at.replace(tzinfo=None)

    monkeypatch.setattr(charts_routes, "datetime", _Pinned)


def test_the_session_comes_from_the_stored_readings_with_the_baselines_and_futures(monkeypatch):
    name = _name()
    now = datetime.combine(_ist_today(), datetime.min.time().replace(hour=12), IST).astimezone(UTC)   # midday IST today
    _pin_route_clock(monkeypatch, now)
    expiry = (now.astimezone(IST).date() + timedelta(days=6)).isoformat()
    _install(monkeypatch, _ChainBroker({name: _live(name, expiry)}))
    headers, me = _v2_owner("session")
    _store_credentials(me)
    for i, (ch, fut) in enumerate(zip(STORED[:3], FUTURES[:3])):          # three slots, 5 minutes apart, before now
        _store(name, OptionChain(underlying=name, expiry=expiry, underlying_ltp=ch.underlying_ltp, rows=ch.rows),
               now - timedelta(minutes=16 - 5 * i), fut, tenant_id=me["tenant_id"])
    body = client.get(URL, headers=headers, params={"symbol": name, "strikes": 1}).json()
    assert body["state"] == "ok" and body["session"]["readings"] == 3 and body["session"]["note"] is None
    charts = by_key(body)
    assert ys(charts["cumulative_oi"], "call_oi") == [120, 125, 130, 115] and ys(charts["pcr_session"], "pcr") == [0.67, 0.68, 0.69, 1.0]
    assert ys(charts["futures"], "future_price") == [120.5, 121.0, 121.5]
    # the collector's oi_day_baselines (today's first reading of this expiry) give today's change
    assert charts["oi_change_by_strike"]["change_basis"] == "first_reading"
    assert ys(charts["oi_change_by_strike"], "call_change") == [10, -5, -10]


def test_baselines_of_another_expiry_are_never_used(monkeypatch):
    name = _name()
    expiry = (_ist_today() + timedelta(days=6)).isoformat()
    other = (_ist_today() + timedelta(days=13)).isoformat()
    _install(monkeypatch, _ChainBroker({name: _live(name, expiry)}))
    headers, me = _v2_owner("other-expiry")
    _store_credentials(me)
    _store(name, OptionChain(underlying=name, expiry=other, underlying_ltp=116.0, rows=STORED[0].rows), datetime.now(UTC) - timedelta(minutes=6), tenant_id=me["tenant_id"])
    body = client.get(URL, headers=headers, params={"symbol": name, "strikes": 1}).json()
    charts = by_key(body)
    assert body["session"]["readings"] == 0 and charts["oi_change_by_strike"]["state"] == "building"


def test_tenancy_each_organisation_reads_through_its_own_broker_only(monkeypatch):
    name = _name()
    used: List[int] = []
    _install(monkeypatch, _ChainBroker({name: _live(name, EXPIRY)}), used)
    a_headers, a = _v2_owner("tenant-a")
    _store_credentials(a)
    b_headers, b = _v2_owner("tenant-b")                                  # no broker of its own
    assert client.get(URL, headers=a_headers, params={"symbol": name}).json()["state"] == "ok"
    body = client.get(URL, headers=b_headers, params={"symbol": name}).json()
    assert body["state"] == "no_broker" and body["charts"] == [] and used == [a["tenant_id"]]
    other_headers, other = _owner(f"oc5-flagoff-{uuid.uuid4().hex[:8]}@example.com")      # the flag is per organisation
    _store_credentials(other)
    assert client.get(URL, headers=other_headers, params={"symbol": name}).status_code == 404
    assert _usage(a["tenant_id"]) == 1 and _usage(b["tenant_id"]) == 0


def test_own_broker_tier_reads_no_shared_rows(monkeypatch):
    name = _name()
    used: List[int] = []
    _install(monkeypatch, _ChainBroker({name: _live(name, EXPIRY)}), used)
    headers, me = _v2_owner("byob")
    _set_flag("byob_data_tier", me["tenant_id"], on=True)
    try:
        refused = client.get(URL, headers=headers, params={"symbol": name}).json()
        assert refused["state"] == "no_broker" and refused["charts"] == []
        _store_credentials(me)
        _store(name, OptionChain(underlying=name, expiry=EXPIRY, underlying_ltp=116.0, rows=STORED[0].rows),
               datetime.now(UTC) - timedelta(minutes=6), (120.0, 900.0), tenant_id=me["tenant_id"])
        body = client.get(URL, headers=headers, params={"symbol": name}).json()
        assert body["state"] == "ok" and used == [me["tenant_id"]]
        assert body["session"]["note"] == C.HISTORY_OFF and by_key(body)["futures"]["message"] == C.HISTORY_OFF
        assert by_key(body)["oi_change_by_strike"]["state"] == "building"          # the shared baselines are not read either
    finally:
        _set_flag("byob_data_tier", me["tenant_id"], on=False)
