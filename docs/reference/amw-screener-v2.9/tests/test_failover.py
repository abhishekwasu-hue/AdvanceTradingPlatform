"""Controlled failover: no candle merging, secondary only for missing symbols, refusal on discrepancy and on mixed data
kinds, per-symbol attribution reaching the canonical record, circuit breaker, explicit stop codes pass through."""
import pandas as pd
import pytest

from src.data.failover import FailoverProvider


class Fake:
    def __init__(self, name, data, kind="delayed/historical", fail=None):
        self.name, self.data, self.data_kind, self.fail, self.calls = name, data, kind, fail, 0

    def fetch_many(self, tickers, tf):
        self.calls += 1
        if self.fail:
            raise self.fail
        return {t: self.data.get(t) for t in tickers}


def bars(base, n=30):
    idx = pd.bdate_range("2026-09-01", periods=n, tz="Asia/Kolkata")
    c = [base + i for i in range(n)]
    return pd.DataFrame({"Open": c, "High": [x + 1 for x in c], "Low": [x - 1 for x in c], "Close": c, "Volume": 1.0}, index=idx)


def test_secondary_only_for_missing_symbols_and_attribution():
    p, s = Fake("P", {"A": bars(100)}), Fake("S", {"A": bars(500), "B": bars(200)})
    f = FailoverProvider(p, s)
    out = f.fetch_many(["A", "B"], "1D")
    assert out["A"].Close.iloc[0] == 100 and out["B"].Close.iloc[0] == 200     # A never mixed with S
    assert f.source_for("A", "1D") == "P" and f.source_for("B", "1D") == "S"


def test_discrepancy_blocks_failover():
    p, s = Fake("P", {"A": bars(100)}), Fake("S", {"A": bars(103)})
    f = FailoverProvider(p, s, max_discrepancy_pct=1.0)
    rep = f.reconcile(["A"])
    assert rep[0]["status"] == "DISCREPANCY" and rep[0]["max_diff_pct"] > 1.0
    p.data = {}                                                                   # primary now has nothing for A
    assert f.fetch_many(["A"], "1D")["A"] is None and "refused" in f.source_for("A", "1D")
    assert FailoverProvider(Fake("P", {"A": bars(100)}), Fake("S", {"A": bars(100)})).reconcile(["A"])[0]["status"] == "OK"


def test_mixed_data_kinds_are_rejected():
    with pytest.raises(ValueError, match="unsafe failover"):
        FailoverProvider(Fake("FYERS", {}, kind="broker (user-licensed)"), Fake("Yahoo", {}))


def test_circuit_breaker_and_stop_codes():
    t = {"now": 0.0}
    p, s = Fake("P", {}, fail=RuntimeError("down")), Fake("S", {"A": bars(1)})
    f = FailoverProvider(p, s, fail_threshold=2, cooldown_s=60, clock=lambda: t["now"])
    f.fetch_many(["A"], "1D")
    f.fetch_many(["A"], "1D")
    assert not f.primary_available()
    n = p.calls
    f.fetch_many(["A"], "1D")
    assert p.calls == n                                                           # skipped while open
    t["now"] = 61
    assert f.primary_available()

    class Stop(Exception):
        code = "token_expired"
    with pytest.raises(Stop):
        FailoverProvider(Fake("P", {}, fail=Stop()), Fake("S", {})).fetch_many(["A"], "1D")


def test_canonical_record_stamps_actual_source():
    from src.screener.scanner import stamp_canonical
    f = FailoverProvider(Fake("P", {}), Fake("S", {"B": bars(1)}))
    f.fetch_many(["B"], "1D")
    row = stamp_canonical({"ticker": "B", "timeframe": "1D"}, None, f, "s", "t")
    assert row["provider"] == "S"
