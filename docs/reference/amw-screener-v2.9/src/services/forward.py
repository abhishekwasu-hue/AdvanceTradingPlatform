"""Paper forward testing tied to stored signals (no orders, simulation only).

Every QUALIFIED signal recorded for a user is enrolled once (`forward_tests`, unique per user + signal). Marking walks
the COMPLETED bars that appeared after the signal bar, using the same timeframe and the user's data source:

* entry  = open of the first bar after the signal bar (never the signal bar itself - no look-ahead);
* exit   = stop or target touched by a later bar's low/high. If one bar touches both, the STOP is assumed (conservative,
           because the order of intrabar moves is unknown); a gap through a level exits at that bar's open;
* expiry = still open after `max_bars` bars -> closed at that bar's close (EXPIRED).
R multiple = (exit - entry) / (entry - stop) for longs (mirrored for shorts). Stats per strategy version carry the same
INSUFFICIENT EVIDENCE label as backtests below 30 closed trades. Results are simulations: no costs, slippage or fills
beyond the documented assumptions, and they are not a promise of future results.
"""
from __future__ import annotations

import logging

import pandas as pd

from ..data.validation import clean_and_validate, usable

log = logging.getLogger(__name__)
DEFAULT_MAX_BARS = {"5m": 48, "15m": 32, "30m": 26, "1H": 30, "1D": 20, "1W": 12}
MIN_EVIDENCE = 30


def enroll(store, rows: list[dict], user_id: int) -> int:
    """Enroll qualified signals (called after each scan batch). Idempotent."""
    n = 0
    for r in rows:
        if r.get("status") != "qualified" or r.get("stop") is None or r.get("target") is None:
            continue
        rec = {"signal_key": store.scoped_key(user_id, r["key"]), "ticker": r["ticker"], "strategy": r.get("strategy"),
               "strategy_version": r.get("strategy_version"), "timeframe": r.get("timeframe"), "direction": r.get("direction"),
               "signal_time": str(r.get("signal_time")), "stop": float(r["stop"]), "target": float(r["target"]),
               "max_bars": DEFAULT_MAX_BARS.get(r.get("timeframe"), 20)}
        n += store.forward_enroll(user_id, rec)
    return n


def _r(direction: str, entry: float, stop: float, exit_: float) -> float | None:
    risk = (entry - stop) if direction == "bullish" else (stop - entry)
    if risk <= 0:
        return None
    return round(((exit_ - entry) if direction == "bullish" else (entry - exit_)) / risk, 3)


def evaluate(t: dict, bars: pd.DataFrame) -> dict:
    """Pure function: new field values for forward test `t` given completed bars (index = bar start, exchange tz)."""
    sig = pd.Timestamp(t["signal_time"])
    if bars.index.tz is not None and sig.tzinfo is None:
        sig = sig.tz_localize(bars.index.tz)
    after = bars[bars.index > sig]
    if after.empty:
        return {}
    long_ = t["direction"] == "bullish"
    stop, target = float(t["stop"]), float(t["target"])
    first = after.iloc[0]
    entry = float(first.Open)
    out = {"status": "OPEN", "entry_time": str(after.index[0]), "entry_price": entry}
    if (long_ and entry <= stop) or (not long_ and entry >= stop):
        return {**out, "status": "INVALID", "exit_time": str(after.index[0]), "exit_price": entry,
                "exit_reason": "gap through stop before entry", "r_multiple": None, "bars_held": 0}
    for i, (ts, b) in enumerate(after.iterrows(), start=1):
        hit_stop = b.Low <= stop if long_ else b.High >= stop
        hit_tgt = b.High >= target if long_ else b.Low <= target
        if hit_stop:
            px = (min(stop, float(b.Open)) if long_ else max(stop, float(b.Open))) if i > 1 else stop
            return {**out, "status": "LOSS", "exit_time": str(ts), "exit_price": px,
                    "exit_reason": "stop" + (" (stop and target in one bar: stop assumed)" if hit_tgt else ""),
                    "r_multiple": _r(t["direction"], entry, stop, px), "bars_held": i}
        if hit_tgt:
            px = (max(target, float(b.Open)) if long_ else min(target, float(b.Open))) if i > 1 else target
            return {**out, "status": "WIN", "exit_time": str(ts), "exit_price": px, "exit_reason": "target",
                    "r_multiple": _r(t["direction"], entry, stop, px), "bars_held": i}
        if i >= int(t.get("max_bars") or 20):
            px = float(b.Close)
            return {**out, "status": "EXPIRED", "exit_time": str(ts), "exit_price": px, "exit_reason": f"{i} bars elapsed",
                    "r_multiple": _r(t["direction"], entry, stop, px), "bars_held": i}
    return {**out, "bars_held": len(after)}


def mark(svc, user_id: int, now=None) -> dict:
    """Update all PENDING/OPEN forward tests of a user from validated, completed bars of their data source."""
    open_ = svc.store.forward_list(user_id, "PENDING,OPEN")
    if not open_:
        return {"checked": 0, "closed": 0, "errors": 0}
    provider, cfg = svc.provider_for(user_id), svc.cfg(user_id)
    closed = errors = 0
    by_tf: dict[str, list[dict]] = {}
    for t in open_:
        by_tf.setdefault(t["timeframe"], []).append(t)
    for tf, items in by_tf.items():
        tickers = sorted({t["ticker"] for t in items})
        try:
            raw = provider.fetch_many(tickers, tf)
        except Exception as e:  # noqa: BLE001
            log.warning("forward mark fetch failed %s: %s", tf, e)
            errors += len(items)
            continue
        for t in items:
            try:
                clean, rep = clean_and_validate(raw.get(t["ticker"]), t["ticker"], tf, cfg, now=now,
                                                data_kind=getattr(provider, "data_kind", "unknown"))
                if not usable(rep):
                    continue
                upd = evaluate(t, clean)
                if upd:
                    svc.store.forward_update(user_id, t["id"], **upd)
                    if upd.get("status") in ("WIN", "LOSS", "EXPIRED", "INVALID"):
                        closed += 1
                        from .notify_events import notify
                        notify(svc.store, user_id, "forward_closed", f"fwd:{t['id']}",
                               f"AMW forward test closed (paper): {t['ticker']} {t['strategy']} v{t['strategy_version']} "
                               f"{t['direction']} -> {upd['status']} ({upd.get('exit_reason')}), R {upd.get('r_multiple')}. "
                               "Simulation only.")
            except Exception:  # noqa: BLE001
                log.exception("forward mark failed for %s", t["ticker"])
                errors += 1
    return {"checked": len(open_), "closed": closed, "errors": errors}


def stats(store, user_id: int) -> list[dict]:
    """Per strategy version: closed trades, win rate, average/total R, open count, evidence label."""
    rows = store.forward_list(user_id, limit=100000)
    out: dict[tuple, dict] = {}
    for r in rows:
        k = (r["strategy"], r["strategy_version"])
        d = out.setdefault(k, {"strategy": k[0], "version": k[1], "closed": 0, "wins": 0, "open": 0, "total_r": 0.0})
        if r["status"] in ("WIN", "LOSS", "EXPIRED"):
            d["closed"] += 1
            d["wins"] += r["status"] == "WIN"
            d["total_r"] += r["r_multiple"] or 0.0
        elif r["status"] in ("PENDING", "OPEN"):
            d["open"] += 1
    res = []
    for d in out.values():
        c = d["closed"]
        res.append({**d, "total_r": round(d["total_r"], 2), "win_rate": round(d["wins"] / c, 3) if c else None,
                    "avg_r": round(d["total_r"] / c, 3) if c else None,
                    "evidence": "SUFFICIENT" if c >= MIN_EVIDENCE else "INSUFFICIENT EVIDENCE"})
    return sorted(res, key=lambda x: (str(x["strategy"]), x["version"] or 0))
