"""M96 L1 proof (owner: the OI difference, its signal and the banner taken from the Trade repo AS IS): Trade's OWN code and
ATP's port run on the same made-up chain sequences, and every displayed number and label must agree.

* Trade side: `oi_analysis.fetch_and_save_oi_snapshot` (the banner: diff, the stable Signal, the Put / Call classes, the
  direction and message, PCR and its zone) on a temporary SQLite file, and the dashboard's chain-table block (the "Net
  Diff" table with its day baseline and TOTAL row) executed from page_dashboard.py's source with a stand-in for the
  Streamlit calls; plus `compute_max_pain` (whole chain) and `compute_dte`.
* ATP side: snapshots.replay / oi_regime (Trade's rotation rule only: the structural flip off), net_diff.py,
  oi_words.trade_fact_message, analysis.compute_max_pain on the whole chain (the /live summary), oi_regime.compute_dte.
* With ATP's default settings (the structural flip ON, owner AUD-V5) every first difference in a sequence is a flip
  Trade does not have: Trade still holds the old side "Weakening", ATP has the new side.

The Trade checkout is read only and named by ATP_TRADE_REPO (unset: skipped with the reason, as the public CI). Nothing
of Trade is committed: its code is read at run time, its outputs live only in memory. The inputs are synthetic, seeded,
built here (no file).
"""
from __future__ import annotations

import importlib
import os
import random
import re
import sqlite3
import sys
import textwrap
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from app.brokers.models import OptionChain, OptionChainRow
from app.option_chain import analysis, net_diff, oi_regime, oi_words, snapshots
from app.option_chain.oi_regime import OIRegimeSettings, OISignal

TRADE = os.environ.get("ATP_TRADE_REPO", "")
pytestmark = pytest.mark.skipif(not TRADE or not (Path(TRADE) / "oi_analysis.py").is_file(),
                                reason="Trade checkout not mounted (ATP_TRADE_REPO): the M96 parity run is a local / host check")

IST = timezone(timedelta(hours=5, minutes=30))
DAY = date(2026, 11, 11)
EXPIRY = "2026-11-13"
SYMBOL = "NIFTY"
STEP = 50


# ------------------------------------------------------------------------------------------------ inputs (synthetic)

def _sequence(seed: int, steps: int = 24):
    """A made-up day of NIFTY chains, 5 minutes apart: spot walks, each side's OI and premium drift and turn."""
    rng = random.Random(seed)
    strikes = list(range(23_500, 25_501, STEP))
    spot = 24_500 + rng.uniform(-80, 80)
    call = {k: rng.randint(20_000, 400_000) for k in strikes}
    put = {k: rng.randint(20_000, 400_000) for k in strikes}
    for k in strikes:                                          # some strikes start with no call OI (Trade's 0 rule)
        if k > 24_600 and rng.random() < 0.4:
            call[k] = 0
    cprem = {k: max(0.05, round(rng.uniform(5, 300), 2)) for k in strikes}
    pprem = {k: max(0.05, round(rng.uniform(5, 300), 2)) for k in strikes}
    cdrift, pdrift = rng.choice([-4, 0, 4]), rng.choice([-4, 0, 4])
    out = []
    for i in range(steps):
        if i and i % rng.choice([5, 6, 7]) == 0:
            cdrift, pdrift = -cdrift or rng.choice([-4, 4]), -pdrift or rng.choice([-4, 4])
        prem_c, prem_p = rng.uniform(-4, 4), rng.uniform(-4, 4)
        for k in strikes:
            if i:
                base_c = call[k] or (rng.randint(5_000, 50_000) if rng.random() < 0.3 else 0)
                call[k] = max(0, round(base_c * (1 + (cdrift + rng.uniform(-3, 3)) / 100))) if call[k] else base_c
                put[k] = max(0, round(put[k] * (1 + (pdrift + rng.uniform(-3, 3)) / 100)))
                cprem[k] = max(0.05, round(cprem[k] * (1 + (prem_c + rng.uniform(-1, 1)) / 100), 2))
                pprem[k] = max(0.05, round(pprem[k] * (1 + (prem_p + rng.uniform(-1, 1)) / 100), 2))
        if i:
            spot += rng.uniform(-45, 45)
        out.append((round(spot, 2), dict(call), dict(put), dict(cprem), dict(pprem)))
    return strikes, out


def _raw(strikes, spot, call, put, cprem, pprem):
    """The chain as Trade reads it (the Upstox chain items)."""
    return [{"strike_price": float(k), "underlying_spot_price": spot, "expiry": EXPIRY,
             "call_options": {"market_data": {"oi": call[k], "ltp": cprem[k]}},
             "put_options": {"market_data": {"oi": put[k], "ltp": pprem[k]}}} for k in strikes]


def _chain(strikes, spot, call, put, cprem, pprem) -> OptionChain:
    return OptionChain(underlying=SYMBOL, expiry=EXPIRY, underlying_ltp=spot,
                       rows=[OptionChainRow(strike=float(k), call_oi=call[k], put_oi=put[k], call_ltp=cprem[k], put_ltp=pprem[k]) for k in strikes])


def _moment(i: int) -> datetime:
    return datetime(2026, 11, 11, 9, 20, tzinfo=IST) + timedelta(minutes=5 * i)


# ------------------------------------------------------------------------------------------------ Trade's own code

@pytest.fixture(scope="module")
def trade():
    sys.path.insert(0, TRADE)
    try:
        oa = importlib.import_module("oi_analysis")
        cloud_db = importlib.import_module("cloud_db")
        assert not cloud_db.is_cloud_db_configured(), "the parity run needs Trade's local SQLite path (SUPABASE_DB_URL unset)"
        source = (Path(TRADE) / "page_dashboard.py").read_text(encoding="utf-8")
        yield oa, _chain_table_block(source)
    finally:
        sys.path.remove(TRADE)


def _chain_table_block(source: str) -> str:
    """The dashboard's chain-table block: from the day-baseline comment to the TOTAL row (the `with tab2:` after it)."""
    lines = source.splitlines()
    start = next(i for i, l in enumerate(lines) if "day_baseline_oi" in l and "SELECT" in l) - 1
    while "today_str = get_ist_now()" not in lines[start]:
        start -= 1
    end = next(i for i in range(start, len(lines)) if lines[i].strip().startswith("df = pd.concat([df, total_row]")) + 1
    return textwrap.dedent("\n".join(lines[start:end]))


class _St:
    """Stands in for the Streamlit calls inside the block (an expander and four metrics)."""
    def expander(self, *a, **k):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def columns(self, n):
        return [self] * n

    def metric(self, *a, **k):
        return None


def _trade_db(path: Path) -> str:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE day_baseline_oi (symbol TEXT, strike REAL, trade_date TEXT, initial_ce_oi INTEGER, initial_pe_oi INTEGER, "
                 "PRIMARY KEY (symbol, strike, trade_date))")
    conn.execute("CREATE TABLE oi_diff_snapshots (symbol TEXT, trade_date TEXT, snapshot_time TEXT, total_call_oi INTEGER, total_put_oi INTEGER, "
                 "diff INTEGER, delta_diff INTEGER, signal TEXT, underlying_price REAL, total_call_premium REAL, total_put_premium REAL, "
                 "PRIMARY KEY (symbol, trade_date, snapshot_time))")
    conn.commit()
    conn.close()
    return str(path)


def _trade_run(oa, block, strikes, seq, db):
    """Trade's banner snapshot and chain table for every reading of the sequence."""
    code = compile(block, "page_dashboard.py:chain-table", "exec")
    out = []
    for i, (spot, call, put, cprem, pprem) in enumerate(seq):
        raw = _raw(strikes, spot, call, put, cprem, pprem)
        now = _moment(i)
        snap, status = oa.fetch_and_save_oi_snapshot("t", SYMBOL, lambda t, s: (raw, "OK"), lambda: now, db, atm_range=6)
        assert status == "OK", status
        step = 50 if SYMBOL == "NIFTY" else 100                       # Trade shared_context.py: the table's step and ATM
        atm = round(raw[0]["underlying_spot_price"] / step) * step
        ns = {"st": _St(), "sqlite3": sqlite3, "pd": pd, "DB_PATH": db, "get_ist_now": lambda: now, "raw_chain": raw,
              "atm_strike": atm, "step": step, "symbol": SYMBOL}
        exec(code, ns)                                                 # noqa: S102 - Trade's own block, read only
        pcr, _ = oa.compute_pcr_signal(snap["total_put_oi"], snap["total_call_oi"])
        out.append({"snap": snap, "table": ns["df"], "pcr": pcr, "zone": oa.compute_pcr_zone_label(pcr),
                    "max_pain": oa.compute_max_pain(raw), "dte": oa.compute_dte(raw, DAY)})
    return out


# ------------------------------------------------------------------------------------------------ reading Trade's labels

CLASS = {"Writing": "Writing", "Buying": "Buying", "Short Covering": "Short covering", "Long Unwinding": "Long unwinding"}


def _class(text: str) -> str:
    for key, name in CLASS.items():
        if key in text:
            return name
    return "Insufficient data" if "\u0905\u092a\u0941\u0930\u093e" in text else "Flat/unclear"   # Trade's "insufficient"


def _zone(label: str):
    if label.startswith("\U0001f7e2 Oversold"):
        return "OVERSOLD"
    if label.startswith("\U0001f534 Overbought"):
        return "OVERBOUGHT"
    if "Bearish" in label:
        return "MILD_BEARISH"
    if "Bullish" in label:
        return "MILD_BULLISH"
    if "Sideways" in label:
        return "SIDEWAYS"
    return None


_PHRASE = re.compile(r"(Put|Call) (Writing|Buying|Short Covering|Unwinding)")
_ATP_KEY = {"Writing": "WRITING", "Buying": "BUYING", "Short Covering": "SHORT COVERING", "Unwinding": "LONG UNWINDING"}


def _message_kind(direction: str, message: str):
    """Trade's banner message as (kind, activity phrases): kind bullish / bearish / mixed / mixed-stable / mixed-level /
    neutral."""
    phrases = [(side, _ATP_KEY[act]) for side, act in _PHRASE.findall(message.split("→")[0])]
    if direction == "MIXED":
        if "\u0938\u094d\u0925\u093f\u0930" in message:                 # Trade's word for "stable"
            return "mixed-stable", phrases
        if "\u092a\u093e\u0924\u0933\u0940" in message:                 # Trade's word for "level"
            return "mixed-level", phrases
        return "mixed", []
    return direction.lower(), phrases


def _atp_kind(st):
    if st.mixed_reason:
        return f"mixed-{st.mixed_reason}"
    return st.direction.value.lower()


def _atp_phrases(text: str):
    lead = "Mixed readings: the recent momentum ("
    head = text[len(lead):].split(") and the ", 1)[0] if text.startswith(lead) else text.split(" → ")[0]
    found = []
    for (side, key), (_, phrase) in oi_words.TRADE_ACTIVITY.items():
        at = head.find(phrase)
        if at >= 0:
            found.append((at, (side, key)))
    return [k for _, k in sorted(found)]


def _strike(label) -> float:
    return float(re.search(r"(\d+(?:\.\d+)?)", str(label)).group(1))


# ------------------------------------------------------------------------------------------------ the parity run

SEEDS = range(1, 13)


def _atp_banner(strikes, seq, settings):
    slots = [snapshots.SlotChain(_moment(i).astimezone(UTC), _moment(i).astimezone(UTC), _chain(strikes, *r))
             for i, r in enumerate(seq)]
    return snapshots.replay(SYMBOL, slots, settings, DAY)


def test_banner_signal_classes_message_pcr_and_net_diff_match_trade(trade, tmp_path):
    oa, block = trade
    checked = {"steps": 0, "mixed-stable": 0, "mixed-level": 0, "bullish": 0, "bearish": 0, "neutral": 0, "mixed": 0,
               "zero_call_replaced": 0}
    for seed in SEEDS:
        strikes, seq = _sequence(seed)
        db = _trade_db(tmp_path / f"trade_{seed}.db")
        theirs = _trade_run(oa, block, strikes, seq, db)
        ours = _atp_banner(strikes, seq, OIRegimeSettings(structural_flip_min_rise_pct=None))
        assert len(ours) == len(theirs)
        baseline: net_diff.Baseline = {}
        for i, (t, e) in enumerate(zip(theirs, ours)):
            snap, st, where = t["snap"], e.state, f"seed {seed} step {i}"
            # the banner: totals, diff, the stable Signal, both classes, the direction and the message's meaning
            assert (e.totals.total_call_oi, e.totals.total_put_oi, st.diff) == (snap["total_call_oi"], snap["total_put_oi"], snap["diff"]), where
            assert st.atm_strike == snap["atm_strike"], where
            assert st.stable_signal == OISignal.parse(snap["signal"]), where
            assert (st.put_class.value, st.call_class.value) == (_class(snap["put_oi_price_class"]), _class(snap["call_oi_price_class"])), where
            assert st.direction.value == snap["oi_price_direction"], where
            kind, phrases = _message_kind(snap["oi_price_direction"], snap["oi_price_message"])
            fact = oi_words.trade_fact_message(st, SYMBOL)
            assert _atp_kind(st) == kind, (where, snap["oi_price_message"], fact)
            if kind != "mixed" and kind != "neutral":
                assert _atp_phrases(fact) == phrases, (where, snap["oi_price_message"], fact)
            if kind == "mixed-level":
                assert re.search(r"[+-]\d+\.\dL", snap["oi_price_message"]).group(0) in fact, (where, fact)
            # PCR over ATM +- 6 and its zone
            assert st.pcr == t["pcr"] and (st.pcr_band.value if st.pcr_band else None) == _zone(t["zone"]), where
            # the Net Diff table, row by row, and the TOTAL row
            chain = _chain(strikes, *seq[i])
            step = net_diff.trade_step(SYMBOL, [r.strike for r in chain.rows])
            atm = net_diff.trade_atm(chain.underlying_ltp, step)
            before = dict(baseline)
            net_diff.update_baseline(baseline, net_diff.window(chain.rows, atm, step))
            checked["zero_call_replaced"] += sum(1 for k, v in before.items() if v[0] == 0 and baseline[k][0] > 0)
            table = net_diff.table(chain.rows, atm, step, baseline)
            df = t["table"]
            body, total = df.iloc[:-1], df.iloc[-1]
            assert [_strike(s) for s in body["Strike Price"]] == [r.strike for r in table.rows], where
            for (_, row), r in zip(body.iterrows(), table.rows):
                assert (row["CE Chg OI"], row["CE Total OI"], row["PE Total OI"], row["PE Chg OI"], row["Net Diff"]) == (
                    r.ce_chg, r.ce_oi, r.pe_oi, r.pe_chg, r.diff), (where, r.strike)
                assert (row["CE LTP"], row["PE LTP"]) == (r.ce_ltp or 0.0, r.pe_ltp or 0.0), (where, r.strike)
                assert row["Dominance"].split(" ", 1)[1] == r.dominance, (where, r.strike)
            assert total["Strike Price"] == "TOTAL"
            assert (total["CE Chg OI"], total["CE Total OI"], total["PE Total OI"], total["PE Chg OI"], total["Net Diff"]) == (
                table.total["ce_chg"], table.total["ce_oi"], table.total["pe_oi"], table.total["pe_chg"], table.total["diff"]), where
            # the table's PCR is the banner's (one window, one definition) and so is its zone
            assert table.pcr == t["pcr"], where
            # max pain over the whole chain (the /live summary) and calendar days to expiry
            assert analysis.compute_max_pain(sorted(chain.rows, key=lambda r: r.strike)) == t["max_pain"], where
            assert oi_regime.compute_dte(EXPIRY, DAY) == t["dte"], where
            checked["steps"] += 1
            checked[kind] += 1
    # the sequences reach every case the comparison is about
    assert checked["steps"] == len(SEEDS) * 24
    for kind in ("bullish", "bearish", "neutral", "mixed-stable", "mixed-level"):
        assert checked[kind] > 0, (kind, checked)
    assert checked["zero_call_replaced"] > 0, checked


def test_with_the_structural_flip_on_every_first_difference_is_that_flip(trade, tmp_path):
    oa, block = trade
    flips = 0
    for seed in SEEDS:
        strikes, seq = _sequence(seed)
        theirs = _trade_run(oa, block, strikes, seq, _trade_db(tmp_path / f"flip_{seed}.db"))
        ours = _atp_banner(strikes, seq, OIRegimeSettings())                    # ATP default: the AUD-V5 flip ON
        for i, (t, e) in enumerate(zip(theirs, ours)):
            trade_signal = OISignal.parse(t["snap"]["signal"])
            if e.state.stable_signal == trade_signal:
                continue
            # Trade holds the old side "Weakening"; ATP's flip already took the new side, the diff's own sign.
            assert trade_signal.strength == oi_regime.Strength.WEAKENING, (seed, i)
            assert e.state.stable_signal.direction != trade_signal.direction, (seed, i)
            assert (e.state.diff > 0) == (e.state.stable_signal.direction == oi_regime.Direction.BULLISH), (seed, i)
            flips += 1
            break                                                               # later readings follow different histories
    assert flips > 0                     # the seeded sequences hold flips (5 of 12 at this writing), each one checked above


def test_the_block_read_from_trade_is_the_net_diff_block(trade):
    _, block = trade
    assert "PE Chg OI" in block and "Net Diff" in block and "TOTAL" in block and "day_baseline_oi" in block
    assert block.count("\n") < 140                                              # the block, not the whole page
