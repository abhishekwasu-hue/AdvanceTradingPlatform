"""M94 PR-2 (docs/design/M94_INTRADAY_PACK.md section 3, S1-S8 and S10-S12): the intraday scanners - a screen library
source of its own, `intraday`, behind the `intraday_pack` flag (off by default) and `screener_breakouts` (their session
functions are that family's). S9 Circuit Watch comes with PR-3.

* Each scanner is a ScreenQL text on a 1m or 5m base with editable `$parameters`. A parameter carries a reference value
  only where the value carries over to NSE (`ref`: "Reference value - edit before use"); a value in another market's
  units (a share count, a currency amount, a price) is Not set (`value` None, M94-Q2) and the run asks for it (the
  validator refuses "parameter $name has no value" until he sets it). A parameter the study gives no number for takes
  the ATP function default where one exists (M94-Q3), else Not set.
* A 0 in a volume parameter means "no volume condition", as in the breakout parameters (`tod_vol_mult`).
* The short counterparts New Day Low, Bottom Reversal and Turbo Breakdown are their own ids (M94-Q12); their text is
  generated from the long screen by the library's mirror rule (`mirror.mirror`), never written by hand, and the tests
  check it on reflected bars.
* Data (I4): the user's own broker session or the stored intraday bars the existing run path allows; closed bars only;
  no new data source. Every result carries FOOTER (M94-Q10).

Signals only (ADR-0006): a scanner lists matches; nothing here places, changes or blocks an order. No buy / sell /
target / stop wording in names or cards (the taxonomy lint checks it).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.screener import mirror as mr
from app.screener import taxonomy as tx
from app.screener.parser import parse
from app.screener.registry import BREAKOUT_FLAG, INTRADAY_FLAG

SOURCE = "intraday"
FLAG = INTRADAY_FLAG
ALSO = (BREAKOUT_FLAG,)
# M94-Q10 / I5: the order's sentence, exactly, on every M94 surface (the older line stays where it is, F14).
FOOTER = "Information and education only, not investment advice."
REF_LABEL = "Reference value - edit before use"
NOT_SET = "Not set"
INTRADAY_TFS = ("1m", "5m")

Value = float | int | str | bool


@dataclass(frozen=True)
class Param:
    """One editable setting. `value` None = Not set (the run asks for it); `ref` = a reference value from the study that
    carries over to NSE, shown with REF_LABEL; otherwise a set value is the ATP function default (M94-Q3)."""
    name: str
    value: Value | None
    doc: str
    ref: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "value": self.value, "doc": self.doc, "ref": self.ref,
                "note": NOT_SET if self.value is None else REF_LABEL if self.ref else "ATP default"}


@dataclass(frozen=True)
class Scanner:
    id: str
    name: str
    definition: str
    text: str
    base_tf: str
    params: tuple[Param, ...] = ()
    mirror_of: str | None = None           # the long screen this one is the generated mirror of (M94-Q12)

    def defaults(self) -> dict[str, Value]:
        """The set values only: a Not set parameter is left out, so validation asks for it."""
        return {p.name: p.value for p in self.params if p.value is not None}

    def unset(self) -> list[str]:
        return [p.name for p in self.params if p.value is None]

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "definition": self.definition, "text": self.text, "base_tf": self.base_tf,
                "params": [p.as_dict() for p in self.params], "unset": self.unset(), "mirror_of": self.mirror_of,
                "footer": FOOTER}


# Shared settings of the DayExtremeBreak scanners: the breakout parameters' own defaults (breakouts/params.py).
def _extreme_params(v: Value, v_ref: bool, v_doc: str) -> tuple[Param, ...]:
    return (Param("v", v, v_doc, v_ref),
            Param("vwap", True, "The close is on the side of the session VWAP the move goes to."),
            Param("skip", 15, "No signal from a bar that starts in the first N minutes of the session."),
            Param("k", 0.2, "The close must be beyond the session's high or low so far by at least this many ATRs."))


_VOLUME_ZERO = " 0 = no volume condition."

S1 = Scanner(
    "intraday.gappers", "Intraday Gappers",
    "The session opened away from the previous close by at least a set percent; optional pre-open volume, daily turnover "
    "and daily range checks.",
    "Abs(DayOpen() - PDC()) / PDC() * 100 >= $gap_pct AND ($pre_vol <= 0 OR PreOpenVolume() >= $pre_vol) "
    "AND SMA(close * volume, 20)@1d >= $min_turnover AND ATR(14)@1d / close * 100 >= $min_atr_pct",
    "5m",
    (Param("gap_pct", 2, "Gap from the previous close to the session's open, in percent (up or down).", True),
     Param("pre_vol", None, "Volume of the pre-open bars (shares). Needs pre-open bars in your data, else not available." + _VOLUME_ZERO),
     Param("min_turnover", None, "Average daily turnover (close x volume) over 20 sessions, in rupees." + _VOLUME_ZERO),
     Param("min_atr_pct", None, "Daily ATR(14) as a percent of the price: the stock's normal daily range.")))

S2 = Scanner(
    "intraday.new_day_high", "New Day High",
    "A close above the session's high so far, on high volume for the time of day, the session's volume above normal and a "
    "recent range wide enough to move.",
    'DayExtremeBreak("up", $v, $vwap, $skip, $k) AND RelVolumeTOD(20, "bar") <= $v_max '
    'AND RelVolumeTOD(20, "cumulative") >= $cum_v AND Max(high, $range_bars) - Min(low, $range_bars) >= $range_atr * ATR(14)[1]',
    "5m",
    (*_extreme_params(4, True, "This bar's volume against the same time of day in the last 20 sessions (4 = 400 %)." + _VOLUME_ZERO),
     Param("v_max", 20, "At most this multiple of the same time of day's volume on this bar (20 = 2,000 %).", True),
     Param("cum_v", 2, "The session's volume so far against the average by the same time.", True),
     Param("range_bars", 3, "Bars of the recent range (3 bars of 5 minutes = 15 minutes).", True),
     Param("range_atr", None, "The recent range is at least this many ATRs.")))

S4 = Scanner(
    "intraday.top_reversal", "Top Reversal",
    "A large rise from the session's open, then a bearish reversal candle at or near the session's high.",
    'SessionHigh() - DayOpen() >= $move_atr * ATR(14)[1] AND SessionHigh() - high <= $near_atr * ATR(14)[1] '
    'AND (Pattern("shooting_star") OR Pattern("bearish_engulfing") OR Pattern("evening_star"))',
    "5m",
    (Param("move_atr", None, "The rise from the session's open to its high so far, in ATRs."),
     Param("near_atr", None, "The bar's high is at most this many ATRs below the session's high.")))

S6 = Scanner(
    "intraday.vwap_break", "VWAP Break",
    "The close crosses above the session VWAP on high volume for the time of day; the mirror reads the cross below.",
    'CrossAbove(close, SessionVWAP()) AND RelVolumeTOD(20, "bar") >= $v',
    "5m",
    (Param("v", 1.5, "This bar's volume against the same time of day in the last 20 sessions." + _VOLUME_ZERO),))

S7 = Scanner(
    "intraday.turbo_breakup", "Turbo Breakup",
    "A new session high on 1-minute bars with unusual 1-minute volume for the time of day.",
    'DayExtremeBreak("up", $v, $vwap, $skip, $k)',
    "1m",
    _extreme_params(1.5, False, "This 1-minute bar's volume against the same minute in the last 20 sessions." + _VOLUME_ZERO))

S10 = Scanner(
    "intraday.rsi_extreme", "RSI Extreme",
    "5-minute RSI above a high level or below a low level, with the session's volume so far and the price inside a set range.",
    "(RSI($n)@5m >= $hi OR RSI($n)@5m <= $lo) AND close >= $pmin AND close <= $pmax AND ($min_vol <= 0 OR SessionVolume() >= $min_vol)",
    "1m",
    (Param("n", 14, "RSI period on 5-minute bars."),
     Param("hi", 90, "RSI at or above this level.", True),
     Param("lo", 10, "RSI at or below this level.", True),
     Param("pmin", None, "Lowest price, in rupees."),
     Param("pmax", None, "Highest price, in rupees."),
     Param("min_vol", None, "The session's volume so far (shares)." + _VOLUME_ZERO)))

S11 = Scanner(
    "intraday.volume_surge", "Volume Surge",
    "Volume against the average of the same time of day in previous sessions: this bar, or the session so far.",
    "RelVolumeTOD($sessions, $mode) >= $v",
    "5m",
    (Param("sessions", 20, "Previous sessions in the time-of-day average."),
     Param("mode", "bar", 'bar: this bar\'s volume; cumulative: the session\'s volume so far.'),
     Param("v", None, "At least this multiple of the time-of-day average.")))


def _near(level: str) -> str:
    return f"Abs(close - {level}) / close * 100 <= $tol_pct"


# PDC / PDH / PDL: the previous session on every bar of the session (PR-1, F24). Lag(high, 1)@1d: the session before it -
# on the session's last bar the latest two closed sessions, as every @1d read on an intraday base (closed daily bars).
S12_LEVELS = ("PDC()", "PDH()", "PDL()", "Lag(high, 1)@1d", "Lag(low, 1)@1d", "PreOpenHigh()", "PreOpenLow()",
              "SMA(close, $sma_a)@1d", "SMA(close, $sma_b)@1d", "EMA(close, $ema_a)", "EMA(close, $ema_b)",
              "Max(high, 250)@1d", "Min(low, 250)@1d")
S12 = Scanner(
    "intraday.key_levels", "Key Levels",
    "The close is within a set distance of a key level: previous close, high or low, pre-open high or low, moving averages "
    "or the 52-week high or low.",
    " OR ".join(_near(level) for level in S12_LEVELS),
    "5m",
    (Param("tol_pct", None, "Distance from the level, in percent of the price."),
     Param("sma_a", 50, "Daily simple moving average.", True),
     Param("sma_b", 200, "Daily simple moving average.", True),
     Param("ema_a", 9, "Exponential moving average on the scan's own bars.", True),
     Param("ema_b", 20, "Exponential moving average on the scan's own bars.", True)))


def _mirrored(long: Scanner, ident: str, name: str, definition: str) -> Scanner:
    """The short counterpart filed as its own id (M94-Q12): the long screen's text through the mirror rule."""
    m = mr.mirror(parse(long.text))
    if m.text is None:
        raise ValueError(f"{long.id}: {m.reason}")
    return Scanner(ident, name, definition, m.text, long.base_tf, long.params, mirror_of=long.id)


S3 = _mirrored(S2, "intraday.new_day_low", "New Day Low",
               "A close below the session's low so far, on high volume for the time of day, the session's volume above normal "
               "and a recent range wide enough to move.")
S5 = _mirrored(S4, "intraday.bottom_reversal", "Bottom Reversal",
               "A large fall from the session's open, then a bullish reversal candle at or near the session's low.")
S8 = _mirrored(S7, "intraday.turbo_breakdown", "Turbo Breakdown",
               "A new session low on 1-minute bars with unusual 1-minute volume for the time of day.")

SCANNERS: tuple[Scanner, ...] = (S1, S2, S3, S4, S5, S6, S7, S8, S10, S11, S12)
BY_ID: dict[str, Scanner] = {s.id: s for s in SCANNERS}


def scanner(ident: str) -> Scanner | None:
    return BY_ID.get(ident)


def payload() -> dict[str, Any]:
    return {"scanners": [s.as_dict() for s in SCANNERS], "footer": FOOTER, "ref_label": REF_LABEL, "not_set": NOT_SET}


def _ids() -> list[str]:
    return [s.id for s in SCANNERS]


tx.register_source(SOURCE, _ids, flag=FLAG, also=ALSO, note=FOOTER)

def taxonomy_sample() -> dict[str, Any]:
    """The taxonomy an organisation with the pack on sees (`intraday_pack` and `screener_breakouts` on, every other
    default-off source still hidden): frontend/src/screener/taxonomyIntradaySample.json."""
    hidden = [name for name in tx.default_hidden() if name not in (SOURCE, "breakouts")]
    return tx.payload(tx.load(), hidden_sources=hidden, hidden_ids=tx.default_hidden_ids())


__all__ = ["SOURCE", "FLAG", "ALSO", "FOOTER", "REF_LABEL", "NOT_SET", "INTRADAY_TFS", "Param", "Scanner", "SCANNERS", "BY_ID",
           "S12_LEVELS", "scanner", "payload", "taxonomy_sample"]


if __name__ == "__main__":      # python -m app.screener.intraday [taxonomy] > ../frontend/src/screener/intraday[Taxonomy]Sample.json
    import json
    import sys

    import app.main  # noqa: F401 - registers every template source (into the imported modules, not this __main__ copy)
    from app.screener import intraday as _itd
    out = _itd.taxonomy_sample() if sys.argv[1:] == ["taxonomy"] else _itd.payload()
    print(json.dumps(out, indent=1, ensure_ascii=False))
