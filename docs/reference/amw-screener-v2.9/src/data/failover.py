"""Controlled provider failover with discrepancy monitoring and a circuit breaker (opt-in: data.failover.enabled).

Rules (documented in docs/OPERATIONS.md):
* Candles from two providers are NEVER merged: each symbol/timeframe frame comes entirely from one provider.
* The secondary is used for a symbol only when the primary returned nothing for it (or the primary's circuit is open).
* Failover is REFUSED for a symbol whose last reconciliation showed a close discrepancy above `max_discrepancy_pct`
  (unsafe combination) - the symbol is then reported as unavailable instead.
* Every frame is attributed: `source_for(ticker, tf)` returns the provider that supplied it; the scanner stamps it into
  the canonical signal record (`provider`).
* Circuit breaker: after `fail_threshold` consecutive failed primary calls the primary is skipped for `cooldown_s`.
Symbols must use the same ticker convention in both providers (both adapters accept AMW tickers, e.g. RELIANCE.NS).
"""
from __future__ import annotations

import time

import pandas as pd


class FailoverProvider:
    cache = None

    def __init__(self, primary, secondary, max_discrepancy_pct: float = 1.0, fail_threshold: int = 3,
                 cooldown_s: float = 300.0, clock=time.monotonic):
        if getattr(primary, "data_kind", None) != getattr(secondary, "data_kind", None):
            raise ValueError(f"unsafe failover: data kinds differ ({getattr(primary, 'data_kind', None)} vs "
                             f"{getattr(secondary, 'data_kind', None)}); delayed and live/broker data are never mixed")
        self.primary, self.secondary = primary, secondary
        self.max_disc, self.fail_threshold, self.cooldown_s, self.clock = max_discrepancy_pct, fail_threshold, cooldown_s, clock
        self.name = f"{primary.name} (failover: {secondary.name})"
        self.data_kind = getattr(primary, "data_kind", "unknown")
        self.sources: dict[tuple[str, str], str] = {}
        self.discrepancy: dict[str, float] = {}           # ticker -> last measured max close difference in %
        self.events: list[dict] = []
        self._fails, self._open_until = 0, 0.0

    # ---- circuit breaker
    def primary_available(self) -> bool:
        return self.clock() >= self._open_until

    def _primary_fetch(self, tickers, tf):
        if not self.primary_available():
            return {}
        try:
            got = self.primary.fetch_many(tickers, tf)
            self._fails = 0
            return got
        except Exception as e:  # noqa: BLE001
            code = getattr(e, "code", None)
            if code in ("token_expired", "rate_limit_guard"):
                raise                                         # explicit stop conditions are not failover cases
            self._fails += 1
            self.events.append({"event": "primary_error", "error": type(e).__name__, "tf": tf})
            if self._fails >= self.fail_threshold:
                self._open_until = self.clock() + self.cooldown_s
                self.events.append({"event": "circuit_open", "seconds": self.cooldown_s})
            return {}

    def fetch_many(self, tickers: list[str], tf: str) -> dict[str, pd.DataFrame | None]:
        got = self._primary_fetch(tickers, tf)
        out: dict[str, pd.DataFrame | None] = {}
        missing = []
        for t in tickers:
            df = got.get(t)
            if df is not None and len(df):
                out[t] = df
                self.sources[(t, tf)] = self.primary.name
            else:
                missing.append(t)
        if missing:
            safe = [t for t in missing if self.discrepancy.get(t, 0.0) <= self.max_disc]
            for t in set(missing) - set(safe):
                out[t] = None
                self.sources[(t, tf)] = "unavailable (failover refused: discrepancy "
                self.sources[(t, tf)] += f"{self.discrepancy[t]:.2f}% > {self.max_disc}%)"
                self.events.append({"event": "failover_refused", "ticker": t, "tf": tf})
            sec = self.secondary.fetch_many(safe, tf) if safe else {}
            for t in safe:
                df = sec.get(t)
                out[t] = df if df is not None and len(df) else None
                self.sources[(t, tf)] = self.secondary.name if out[t] is not None else "unavailable"
                if out[t] is not None:
                    self.events.append({"event": "failover", "ticker": t, "tf": tf})
        return out

    def source_for(self, ticker: str, tf: str) -> str | None:
        return self.sources.get((ticker, tf))

    def reconcile(self, tickers: list[str], tf: str = "1D", bars: int = 20) -> list[dict]:
        """Compare the last `bars` common closes of both providers (timestamps normalised to exchange-local dates for
        1D/1W, exact timestamps intraday). Records the max absolute % difference per ticker."""
        a, b = self.primary.fetch_many(tickers, tf), self.secondary.fetch_many(tickers, tf)
        rep = []
        for t in tickers:
            x, y = a.get(t), b.get(t)
            if x is None or y is None or not len(x) or not len(y):
                rep.append({"ticker": t, "status": "NOT_COMPARABLE", "common_bars": 0})
                continue
            def key(df):
                idx = pd.to_datetime(df.index)
                if idx.tz is not None:
                    idx = idx.tz_convert("Asia/Kolkata")
                return idx.normalize().tz_localize(None) if tf in ("1D", "1W") and idx.tz is not None else \
                    (idx.normalize() if tf in ("1D", "1W") else idx)
            xs = pd.Series(x.Close.to_numpy(), index=key(x))
            ys = pd.Series(y.Close.to_numpy(), index=key(y))
            j = pd.concat([xs.rename("a"), ys.rename("b")], axis=1, join="inner").dropna().tail(bars)
            if j.empty:
                rep.append({"ticker": t, "status": "NOT_COMPARABLE", "common_bars": 0})
                continue
            d = float(((j.a - j.b).abs() / j.a.abs()).max() * 100)
            self.discrepancy[t] = d
            rep.append({"ticker": t, "status": "OK" if d <= self.max_disc else "DISCREPANCY", "common_bars": len(j),
                        "max_diff_pct": round(d, 4)})
        return rep

    def health(self) -> dict:
        return {"provider": self.name, "data_kind": self.data_kind, "primary_available": self.primary_available(),
                "recent_events": self.events[-20:]}
