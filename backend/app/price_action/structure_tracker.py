"""Per-timeframe market-structure state machine: swings with HH / LH / EQH / HL / LL / EQL labels, BOS, CHoCH, reversal,
recovery, range, and liquidity sweeps of confirmed swings - incremental and causal (one call per closed bar).

Ported from Trade@1e6c9ac, opportunity_engine/structure.py (StructureTracker) with opportunity_engine/measures.py
`ref_range_from_ranges` and the EngineConfig fields it reads. Logic unchanged; comments in English; parity with Trade's
own output on the frozen fixtures: tests/test_ch3b_trade_parity.py.

Rules:
  * Every break is by CLOSE. A wick beyond a confirmed swing that closes back inside is a SWEEP event; the state holds.
  * A pivot at bar p is confirmed at p + R (R = pivot_n[tf]). Swing significance: k x ref_range from the last swing; of two
    same-kind swings in a row the more extreme one is kept.
  * UPTREND -> a close below the protected low (the last HL) = UPTREND_WEAK (event CHOCH).
    UPTREND_WEAK: weak_low = the lowest bar low since the CHoCH (a spike's body low). A qualifying LH = a swing high
    confirmed after the CHoCH, below last_HH - lh_tol x ref_range. Once it is confirmed, a close below min(low[CHoCH..t-1])
    = DOWNTREND (REVERSAL_CONFIRMED); a close above last_HH = UPTREND (RECOVERY); weak_to_range_swings swings with
    neither = RANGE. A close above the last swing high in a trend = BOS.
  * DOWN is the mirror image (one signed code path: s = +1 up, -1 down; prices x s).
  * RANGE: a close beyond an edge by range_exit_k x ref_range plus a confirmed higher low / lower high = UP / DOWN.
  * No look-ahead: the decision at bar t uses bars[0..t] only. A spike needs 1-2 later bars to be recognised, so its body
    edge applies only once those bars have closed; until then the plain wick (conservative).
No indicator; ref_range only as k x ref_range. Reference only - nothing here places an order.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

INIT, RANGE = "INIT", "RANGE"
UP, UP_PB, UP_WEAK = "UPTREND", "UPTREND_PULLBACK", "UPTREND_WEAK"
DN, DN_PB, DN_WEAK = "DOWNTREND", "DOWNTREND_PULLBACK", "DOWNTREND_WEAK"
STATES = (INIT, RANGE, UP, UP_PB, UP_WEAK, DN, DN_PB, DN_WEAK)
_STATE_OF = {1: (UP, UP_PB, UP_WEAK), -1: (DN, DN_PB, DN_WEAK)}


@dataclass
class StructureConfig:
    """The EngineConfig fields the tracker reads (Trade@1e6c9ac opportunity_engine/config.py defaults)."""
    ref_range_bars: int = 20             # median(high - low) of the last this many closed FULL bars
    ref_range_min_bars: int = 10         # fewer full bars -> NaN (warm-up; no decision on that timeframe)
    pivot_n: Dict[str, int] = field(default_factory=lambda: {"5m": 2, "15m": 3, "1h": 3, "4h": 3, "1d": 3})
    swing_k: Dict[str, float] = field(default_factory=lambda: {"5m": 1.5, "15m": 1.5, "1h": 1.5, "4h": 2.0, "1d": 2.0})
    eq_tol: float = 0.1                  # |difference| <= 0.1 x ref_range -> EQH / EQL
    pullback_k: float = 1.0              # a close this many x ref_range below the trend extreme -> PULLBACK
    lh_tol: float = 0.1                  # qualifying LH: below last_HH - 0.1 x ref_range
    weak_to_range_swings: int = 4        # this many new swings after WEAK with nothing decided -> RANGE
    range_exit_k: float = 0.1            # a close >= 0.1 x ref_range beyond a RANGE edge + one confirmed HL / LH
    init_swings: int = 4
    spike_wick_k: float = 2.5
    spike_body_max: float = 0.30
    spike_return_bars: int = 2
    spike_body_edge: bool = True         # a spike's body edge, not its wick (swing price and weak_low)


def ref_range_from_ranges(ranges: Any, n: int = 20, min_bars: int = 10) -> float:
    """`ranges` = (high - low) of the full bars, oldest first. The median of the last n; NaN with fewer than min_bars."""
    n = int(n)
    arr = np.asarray(ranges[-(4 * n):], dtype="float64")         # the tail first, so a long history is never copied
    arr = arr[np.isfinite(arr)]
    if len(arr) < int(min_bars):
        return float("nan")
    return float(np.median(arr[-n:]))


@dataclass
class Swing:
    kind: str            # "H" / "L"
    idx: int             # the swing bar's index
    time: Any            # the swing bar's bar_end
    price: float         # (a spike's body edge)
    confirmed_idx: int
    confirmed_time: Any
    label: str = ""      # HH / LH / EQH (kind H) or HL / LL / EQL (kind L)
    spike: bool = False


class _Neg:
    """A read-only view of -x (no copy of the list): signed lows for s = -1. Index and slice both work."""
    __slots__ = ("_a",)

    def __init__(self, a: List[float]) -> None:
        self._a = a

    def __getitem__(self, i: Any) -> Any:
        if isinstance(i, slice):
            return [-x for x in self._a[i]]
        return -self._a[i]


class StructureTracker:
    """One timeframe's incremental structure tracker. Call `on_bar()` once per closed bar (bar_end <= t)."""

    def __init__(self, tf: str, cfg: Optional[StructureConfig] = None) -> None:
        self.tf = tf
        self.cfg = cfg or StructureConfig()
        self.R = int(self.cfg.pivot_n.get(tf, 3))
        self.k = float(self.cfg.swing_k.get(tf, 1.5))
        self.o: List[float] = []
        self.h: List[float] = []
        self.l: List[float] = []
        self.c: List[float] = []
        self.bar_end: List[Any] = []
        self.full: List[bool] = []
        self.eff_lo: List[float] = []            # with a spike's body edge (only once resolved)
        self.eff_hi: List[float] = []
        self._ranges: List[float] = []
        self.rr = float("nan")
        self.rr_hist: List[float] = []           # ref_range at each bar
        self.swings: List[Swing] = []
        self._swept: set = set()
        self.state = INIT
        self.s = 0                               # trend direction (+1 / -1) in UP / DOWN states
        self.prot: Optional[float] = None        # protected level (signed price)
        self.ext: Optional[float] = None         # trend extreme (signed running max of the signed high)
        self.last_sh: Optional[tuple] = None     # (signed price, swing idx) - the last "high" swing in the trend's direction
        self.last_sh_broken = False
        self.range_hi: Optional[float] = None
        self.range_lo: Optional[float] = None
        self.weak_ext: Optional[float] = None
        self.choch_idx: Optional[int] = None
        self.lh: Optional[Swing] = None
        self.weak_min_settled: Optional[float] = None
        self.weak_swing_count = 0
        self.events: List[Dict[str, Any]] = []
        self.changes: List[Dict[str, Any]] = []  # history of state changes

    # ------------------------------------------------------------------ helpers
    def _signed_bar(self, i: int, s: int) -> tuple:
        if s > 0:
            return self.o[i], self.h[i], self.l[i], self.c[i]
        return -self.o[i], -self.l[i], -self.h[i], -self.c[i]

    def _tol(self, mult: float) -> float:
        return mult * self.rr

    def _emit(self, t: int, etype: str, price: Optional[float], **details: Any) -> Dict[str, Any]:
        ev = {"tf": self.tf, "bar_idx": t, "time": self.bar_end[t], "type": etype, "price": None if price is None else float(price),
              "state": self.state, **details}
        self.events.append(ev)
        return ev

    def _set_state(self, t: int, new_state: str, etype: str, price: Optional[float], **details: Any) -> Dict[str, Any]:
        old = self.state
        self.state = new_state
        ev = self._emit(t, etype, price, from_state=old, to_state=new_state, **details)
        if old != new_state:
            self.changes.append({"tf": self.tf, "time": self.bar_end[t], "bar_idx": t, "from_state": old, "state": new_state, "event": etype,
                                 "price": None if price is None else float(price), "protected": self.protected_level(),
                                 "trigger_bar": details.get("trigger_bar", self.bar_end[t])})
        return ev

    def protected_level(self) -> Optional[float]:
        if self.prot is None or self.s == 0 or self.state not in _STATE_OF[self.s]:
            return None
        return float(self.prot * self.s)

    # ------------------------------------------------------------------ spike
    def _spike_kind(self, k: int, upto: int) -> Optional[str]:
        """Is bar k a spike? "UP" (upper wick), "DOWN" (lower wick) or None, from the closes of bars k+1..upto
        (<= k + spike_return_bars) - None while those bars are not there. Wick >= 2.5 x ref_range, body <= 30 % of the range,
        price back at the wick's root within the next 1-2 bars."""
        cfg = self.cfg
        if not np.isfinite(self.rr) or self.rr <= 0:
            return None
        rng = self.h[k] - self.l[k]
        if rng <= 0:
            return None
        body_hi, body_lo = max(self.o[k], self.c[k]), min(self.o[k], self.c[k])
        if (body_hi - body_lo) > cfg.spike_body_max * rng:
            return None
        window = range(k + 1, min(k + int(cfg.spike_return_bars), upto) + 1)
        if (self.l[k] <= body_lo - cfg.spike_wick_k * self.rr) and (body_lo - self.l[k]) >= (self.h[k] - body_hi):
            if any(self.c[j] >= body_lo for j in window):
                return "DOWN"
        if (self.h[k] >= body_hi + cfg.spike_wick_k * self.rr) and (self.h[k] - body_hi) > (body_lo - self.l[k]):
            if any(self.c[j] <= body_hi for j in window):
                return "UP"
        return None

    def _settle_spikes(self, t: int) -> None:
        """Fix the effective high / low of bar t - spike_return_bars (now fully resolved)."""
        k = t - int(self.cfg.spike_return_bars)
        if k < 0 or not self.cfg.spike_body_edge:
            return
        kind = self._spike_kind(k, t)
        if kind == "DOWN":
            self.eff_lo[k] = min(self.o[k], self.c[k])
        elif kind == "UP":
            self.eff_hi[k] = max(self.o[k], self.c[k])

    # ------------------------------------------------------------------ swings
    def _is_pivot(self, p: int, kind: str) -> bool:
        R = self.R
        if p < R:
            return False
        if kind == "H":
            left, right = self.h[p - R:p], self.h[p + 1:p + R + 1]
            return self.h[p] >= max(left) and self.h[p] > max(right)
        left, right = self.l[p - R:p], self.l[p + 1:p + R + 1]
        return self.l[p] <= min(left) and self.l[p] < min(right)

    def _pivot_price(self, p: int, kind: str, t: int) -> tuple:
        """The swing price - a spike's body edge when it is one."""
        if self.cfg.spike_body_edge:
            sk = self._spike_kind(p, t)
            if kind == "H" and sk == "UP":
                return max(self.o[p], self.c[p]), True
            if kind == "L" and sk == "DOWN":
                return min(self.o[p], self.c[p]), True
        return (self.h[p] if kind == "H" else self.l[p]), False

    def _label(self, kind: str, price: float, upto_swings: List[Swing]) -> str:
        prev = next((sw for sw in reversed(upto_swings) if sw.kind == kind), None)
        if prev is None:
            return ""
        tol = self._tol(self.cfg.eq_tol)
        if abs(price - prev.price) <= tol:
            return "EQH" if kind == "H" else "EQL"
        if kind == "H":
            return "HH" if price > prev.price else "LH"
        return "HL" if price > prev.price else "LL"

    def _add_pivot(self, p: int, kind: str, t: int) -> tuple:
        """Add a newly confirmed pivot (significance + alternation) -> ("added" | "replaced" | "ignored", Swing | None)."""
        price, spike = self._pivot_price(p, kind, t)
        last = self.swings[-1] if self.swings else None
        if last is not None and last.kind == kind:
            better = price > last.price if kind == "H" else price < last.price
            if not better:
                return "ignored", None
            self.swings.pop()
            sw = Swing(kind, p, self.bar_end[p], price, t, self.bar_end[t], "", spike)
            sw.label = self._label(kind, price, self.swings)
            self.swings.append(sw)
            return "replaced", sw
        if last is not None and abs(price - last.price) < self.k * self.rr:
            return "ignored", None
        sw = Swing(kind, p, self.bar_end[p], price, t, self.bar_end[t], "", spike)
        sw.label = self._label(kind, price, self.swings)
        self.swings.append(sw)
        return "added", sw

    # ------------------------------------------------------------------ main
    def on_bar(self, bar_end: Any, o: float, h: float, l: float, c: float, full: bool = True) -> List[Dict[str, Any]]:
        """One closed bar -> the events on this bar."""
        t = len(self.c)
        self.o.append(float(o))
        self.h.append(float(h))
        self.l.append(float(l))
        self.c.append(float(c))
        self.eff_lo.append(float(l))
        self.eff_hi.append(float(h))
        self.bar_end.append(bar_end)
        self.full.append(bool(full))
        if full:
            self._ranges.append(float(h) - float(l))
        self.rr = ref_range_from_ranges(self._ranges, self.cfg.ref_range_bars, self.cfg.ref_range_min_bars)
        self.rr_hist.append(self.rr)
        n_before = len(self.events)
        if not np.isfinite(self.rr) or self.rr <= 0:
            return []
        self._settle_spikes(t)

        new_swings = []
        for kind in ("H", "L"):
            p = t - self.R
            if p >= self.R and self._is_pivot(p, kind):
                status, sw = self._add_pivot(p, kind, t)
                if sw is not None:
                    new_swings.append((status, sw))
                    self._emit(t, "SWING", sw.price, kind=kind, label=sw.label, swing_idx=p, swing_time=sw.time, status=status, spike=sw.spike)

        self._step(t, new_swings)
        self._detect_sweeps(t)
        return self.events[n_before:]

    # ------------------------------------------------------------------ state step
    def _step(self, t: int, new_swings: List[tuple]) -> None:
        if self.state == INIT:
            self._try_init(t)
            return
        if self.state == RANGE:
            self._step_range(t, new_swings)
            return
        s = self.s
        assert self.ext is not None and self.prot is not None
        ext_candidate = self._signed_bar(t, s)[1]           # trend extreme: running max of the signed high
        _o_s, _h_s, _l_s, c_s = self._signed_bar(t, s)
        sh_kind = "H" if s > 0 else "L"

        base, pb, weak = _STATE_OF[s]
        if self.state in (base, pb):
            prev_ext = self.ext
            self.ext = max(self.ext, ext_candidate)
            for _status, sw in new_swings:
                sp = sw.price * s
                if sw.kind == sh_kind:
                    self.last_sh, self.last_sh_broken = (sp, sw.idx), False
                elif sp > self.prot - self._tol(self.cfg.eq_tol):
                    self.prot = sp                                    # HL (or EQL) = the new protected level
            if c_s < self.prot:
                self.weak_ext = self.ext
                self.choch_idx = t
                self.lh = None
                self.weak_swing_count = 0
                self.weak_min_settled = None
                self._set_state(t, weak, "CHOCH", self.prot * s, protected_broken=self.prot * s)
                self._weak_bookkeeping(t, s)
                return
            if self.last_sh is not None and not self.last_sh_broken and c_s > self.last_sh[0]:
                self.last_sh_broken = True
                self._set_state(t, base, "BOS", self.last_sh[0] * s, broke_swing_idx=self.last_sh[1])
            elif self.state == pb and c_s > prev_ext:
                self._set_state(t, base, "PULLBACK_END", c_s * s)             # a close at a new extreme ends the pullback
            elif self.state == base and c_s < self.ext - self._tol(self.cfg.pullback_k):
                self._set_state(t, pb, "PULLBACK_START", c_s * s)
            return
        self._step_weak(t, s, new_swings)

    # --- WEAK
    def _weak_lows(self, s: int) -> Any:
        """Signed effective lows (spike body edge included); for s = -1 a -eff_hi view (no copy)."""
        return self.eff_lo if s > 0 else _Neg(self.eff_hi)

    def _weak_bookkeeping(self, t: int, s: int) -> None:
        """Fold bar t - spike_return_bars (now settled) into the settled min since the CHoCH (O(1))."""
        assert self.choch_idx is not None
        k = t - int(self.cfg.spike_return_bars)
        if k >= self.choch_idx:
            v = self._weak_lows(s)[k]
            self.weak_min_settled = v if self.weak_min_settled is None else min(self.weak_min_settled, v)

    def _min_low_before(self, t: int, s: int, upto: int) -> Optional[float]:
        """min(signed low[CHoCH..upto]) - the settled part plus the raw lows of bars not resolved yet."""
        assert self.choch_idx is not None
        lows = self._weak_lows(s)
        settle_to = t - int(self.cfg.spike_return_bars)
        start = self.choch_idx
        parts: List[float] = []
        if self.weak_min_settled is not None and settle_to >= start:
            parts.append(self.weak_min_settled)
        pending_from = max(start, settle_to + 1)
        if upto >= pending_from:
            parts.extend(lows[pending_from:upto + 1])
        return min(parts) if parts else None

    def _step_weak(self, t: int, s: int, new_swings: List[tuple]) -> None:
        cfg = self.cfg
        _o, h_s, _l_s, c_s = self._signed_bar(t, s)
        base, _pb, _weak = _STATE_OF[s]
        sh_kind = "H" if s > 0 else "L"
        assert self.weak_ext is not None and self.choch_idx is not None
        self._weak_bookkeeping(t, s)
        # recovery
        if c_s > self.weak_ext:
            self.ext = max(self.weak_ext, h_s)
            floor = self._min_low_before(t, s, t)
            # protected = the lowest point since the CHoCH (as the HL) - updated by later HL swings
            self.prot = floor if floor is not None else self.prot
            self.last_sh, self.last_sh_broken = (self.weak_ext, -1), True
            self._set_state(t, base, "RECOVERY", c_s * s, recovered_above=self.weak_ext * s)
            return
        for _status, sw in new_swings:
            if sw.idx <= self.choch_idx:
                continue
            self.weak_swing_count += 1
            if sw.kind == sh_kind and sw.price * s < self.weak_ext - self._tol(cfg.lh_tol):
                self.lh = sw
                catch = self._catch_up_down(t, s, sw)
                if catch is not None:
                    self._to_opposite(t, s, trigger_k=catch)
                    return
        # with a confirmed LH: trigger on close_t < min(low[CHoCH..t-1])
        if self.lh is not None and t > self.choch_idx:
            m = self._min_low_before(t, s, t - 1)
            if m is not None and c_s < m:
                self._to_opposite(t, s, trigger_k=t)
                return
        if self.weak_swing_count >= cfg.weak_to_range_swings:
            recent = self.swings[-cfg.init_swings:]
            self.range_hi = max(sw.price for sw in recent)
            self.range_lo = min(sw.price for sw in recent)
            self._set_state(t, RANGE, "RANGE_START", self.c[t], range_high=self.range_hi, range_low=self.range_lo)
            self.s, self.prot, self.ext = 0, None, None

    def _catch_up_down(self, t: int, s: int, lh_swing: Swing) -> Optional[int]:
        """The first k (<= t) between the LH's pivot bar and its confirmation whose close was already below
        min(low[CHoCH..k-1]), else None. Nothing from the future - k <= t."""
        assert self.choch_idx is not None
        lows = self._weak_lows(s)
        for k in range(lh_swing.idx + 1, t + 1):
            floor = min(lows[self.choch_idx:k]) if k > self.choch_idx else None
            if floor is not None and self._signed_bar(k, s)[3] < floor:
                return k
        return None

    def _to_opposite(self, t: int, s: int, trigger_k: int) -> None:
        """Reversal confirmed: UP_WEAK -> DOWNTREND (or the mirror). Event time = t (the confirmation bar); trigger_bar kept apart."""
        assert self.choch_idx is not None and self.lh is not None
        lows = self._weak_lows(s)
        floor = min(lows[self.choch_idx:max(trigger_k, self.choch_idx + 1)])      # the broken weak_low (signed, in s's space)
        ns = -s
        lh_price = self.lh.price
        self.s = ns
        self.prot = lh_price * ns
        # the new direction's running extreme (signed high): the most extreme since the CHoCH + the bars after the trigger
        self.ext = max([-floor] + [self._signed_bar(i, ns)[1] for i in range(trigger_k, t + 1)])
        old_swing_kind = "L" if s > 0 else "H"                  # the new direction's "high" swing = the old direction's low swing
        last = next((sw for sw in reversed(self.swings) if sw.kind == old_swing_kind), None)
        if last is None:
            self.last_sh, self.last_sh_broken = None, True
        else:
            self.last_sh = (last.price * ns, last.idx)
            self.last_sh_broken = self._signed_bar(t, ns)[3] > self.last_sh[0]
        self.lh = None
        self.weak_ext = None
        self.choch_idx = None
        self._set_state(t, _STATE_OF[ns][0], "REVERSAL_CONFIRMED", floor * s,
                        trigger_bar=self.bar_end[trigger_k], trigger_idx=trigger_k, lh_price=lh_price)

    # --- INIT / RANGE
    def _try_init(self, t: int) -> None:
        n = self.cfg.init_swings
        if len(self.swings) < n:
            return
        recent = self.swings[-n:]
        highs = [sw for sw in recent if sw.kind == "H"]
        lows = [sw for sw in recent if sw.kind == "L"]
        if len(highs) < 2 or len(lows) < 2:
            return
        tol = self._tol(self.cfg.eq_tol)
        up = highs[-1].price > highs[-2].price + tol and lows[-1].price > lows[-2].price + tol
        down = highs[-1].price < highs[-2].price - tol and lows[-1].price < lows[-2].price - tol
        if up or down:
            s = 1 if up else -1
            self.s = s
            self.prot = (lows[-1].price if s > 0 else highs[-1].price) * s
            hi_kind = "H" if s > 0 else "L"
            tops = [sw for sw in recent if sw.kind == hi_kind]
            self.ext = max(self._signed_bar(i, s)[1] for i in range(tops[-1].idx, t + 1))
            self.last_sh, self.last_sh_broken = (tops[-1].price * s, tops[-1].idx), False
            self._set_state(t, _STATE_OF[s][0], "INIT_TREND", self.c[t])
        else:
            self.range_hi = max(sw.price for sw in recent)
            self.range_lo = min(sw.price for sw in recent)
            self._set_state(t, RANGE, "INIT_RANGE", self.c[t], range_high=self.range_hi, range_low=self.range_lo)

    def _step_range(self, t: int, new_swings: List[tuple]) -> None:
        cfg = self.cfg
        for _status, _sw in new_swings:
            recent = self.swings[-cfg.init_swings:]
            self.range_hi = max(x.price for x in recent)
            self.range_lo = min(x.price for x in recent)
        tol = self._tol(cfg.range_exit_k)
        close = self.c[t]
        lows = [sw for sw in self.swings if sw.kind == "L"]
        highs = [sw for sw in self.swings if sw.kind == "H"]
        assert self.range_hi is not None and self.range_lo is not None
        if close > self.range_hi + tol and len(lows) >= 2 and lows[-1].price > lows[-2].price:
            self._enter_trend(t, 1, lows[-1].price, "RANGE_EXIT_UP")
        elif close < self.range_lo - tol and len(highs) >= 2 and highs[-1].price < highs[-2].price:
            self._enter_trend(t, -1, highs[-1].price, "RANGE_EXIT_DOWN")

    def _enter_trend(self, t: int, s: int, prot_price: float, etype: str) -> None:
        self.s = s
        self.prot = prot_price * s
        self.ext = self._signed_bar(t, s)[1]
        hi_kind = "H" if s > 0 else "L"
        last = next((sw for sw in reversed(self.swings) if sw.kind == hi_kind), None)
        self.last_sh = (last.price * s, last.idx) if last else None
        self.last_sh_broken = True
        self._set_state(t, _STATE_OF[s][0], etype, self.c[t])

    # --- SWEEP
    def _detect_sweeps(self, t: int) -> None:
        """A wick beyond a confirmed swing closing back inside = a liquidity sweep (the state holds). Once per swing and side."""
        if len(self.swings) < 2:
            return
        for sw in self.swings[-4:]:
            key = (sw.idx, sw.kind)
            if key in self._swept or t <= sw.confirmed_idx:
                continue
            if sw.kind == "H" and self.h[t] > sw.price and self.c[t] <= sw.price:
                self._swept.add(key)
                self._emit(t, "SWEEP", sw.price, side="HIGH", swing_idx=sw.idx)
            elif sw.kind == "L" and self.l[t] < sw.price and self.c[t] >= sw.price:
                self._swept.add(key)
                self._emit(t, "SWEEP", sw.price, side="LOW", swing_idx=sw.idx)

    # ------------------------------------------------------------------ snapshot
    def snapshot(self) -> Dict[str, Any]:
        sw_h = next((sw for sw in reversed(self.swings) if sw.kind == "H"), None)
        sw_l = next((sw for sw in reversed(self.swings) if sw.kind == "L"), None)
        return {
            "tf": self.tf, "trend_state": self.state, "protected_level": self.protected_level(),
            "last_sh": None if sw_h is None else sw_h.price, "last_sl": None if sw_l is None else sw_l.price,
            "range_high": self.range_hi if self.state == RANGE else None, "range_low": self.range_lo if self.state == RANGE else None,
            "ref_range": None if not np.isfinite(self.rr) else self.rr,
            "updated_at": self.bar_end[-1] if self.bar_end else None, "bars": len(self.c),
        }


def run(frame: Any, tf: str, cfg: Optional[StructureConfig] = None, time_col: str = "bar_end") -> StructureTracker:
    """Feed a closed-bar frame (oldest first) through a fresh tracker, one bar at a time."""
    tr = StructureTracker(tf, cfg)
    for be, o, h, l, c in zip(frame[time_col], frame["open"].to_numpy(float), frame["high"].to_numpy(float),
                              frame["low"].to_numpy(float), frame["close"].to_numpy(float)):
        tr.on_bar(be, o, h, l, c)
    return tr
