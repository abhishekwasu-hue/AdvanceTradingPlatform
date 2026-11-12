"""M97 PR-2 (docs/design/M97_SUPPLY_DEMAND.md, Z4): the Supply & Demand scanners - a screen library source of their own,
`supply_demand`, behind the `sd_pack` flag (off by default), built the way M94 PR-2 built the intraday source.

* Each scanner is a ScreenQL text over the PR-1 zone functions (app/price_action/sd_zones.py: SDZoneEnter, SDZoneFormed,
  SDZoneViolated, SDZoneDistancePct) with editable `$parameters`. Every detector number is a parameter carrying the
  M97-Q2 reference value ("Reference value - edit before use"); the edge choice is the M97-Q3 reference (wick); the
  leg-out reference is the function default (average range). The distance a user calls "near" is his own: Not set, the
  run asks for it.
* Any list and any timeframe: a scanner opens on the daily timeframe; the builder's timeframe and the universe are his
  to change, and the run route reads the store first for closed bars, as for every screen.
* M99 P1: the zone kinds (base / swing extreme / both, the two-candle engulfing zone), the swap level and five more
  scanners - zone entry by type, swap level first return, compression into a zone, odds score at least the user's
  value, zone with a higher-timeframe zone - over the shared evaluation (app/price_action/sd_odds.py); the odds
  settings are $parameters with reference values too.
* Signals only (ADR-0006): a scanner lists matches; nothing here places, changes or blocks an order. No buy / sell /
  target / stop wording in names or cards (the taxonomy lint checks it). Every card carries FOOTER.
"""
from __future__ import annotations

from typing import Any

from app.price_action import sd_odds as so
from app.price_action import sd_zones as sd
from app.screener import taxonomy as tx
from app.screener.intraday import FOOTER, NOT_SET, REF_LABEL, Param, Scanner
from app.screener.registry import SD_FLAG

SOURCE = "supply_demand"
FLAG = SD_FLAG
ALSO: tuple[str, ...] = ()
BASE_TF = "1d"
FORMATION_CHOICES: tuple[str, ...] = (*sd.ALL_FORMATIONS, "any")

# The detector's settings as $parameters, in sd_zones.SETTINGS_TABLE order (tests check the names, values and order).
_SETTING_DOCS: dict[str, str] = {
    "avg_n": "Candles in the average range a candle is measured against.",
    "leg_mult": "A leg candle's range is at least this multiple of the average range.",
    "base_mult": "A base candle's range is at most this multiple of the average range.",
    "out_mult": "The leg-out candle's range is at least this multiple of the average range (or ATR).",
    "leg_body_pct": "A leg candle's body is at least this percent of its range.",
    "base_body_pct": "A base candle's body is at most this percent of its range.",
    "base_max": "At most this many base candles in a row.",
    "out_ref": "What the leg-out is measured against: average (the average range) or atr.",
    "edge": "The zone's near line: wick (the base's high or low) or body (the base's body top or bottom).",
    "skip_flat_bars": "On: a flat candle (high equals low) is never counted as a base candle.",
    "kind": "Which zones: base (a base between two legs), swing (the swing extreme candle) or both.",
    "swing_left": "A swing extreme is beyond the highs (supply) or lows (demand) of this many candles before it.",
    "confirm_within": "A swing zone counts only if a later candle closes without touching it within this many candles.",
    "engulf": "On: when the extreme candle engulfs the one before, the zone is the two candles.",
    "swap": "On: a zone broken by a strong candle stays as a zone of the other side (a swap level).",
    "swap_mult": "The breaking candle's range is at least this multiple of the average range (0 = any break).",
}
_ODDS_DOCS: dict[str, str] = {
    "confirm_bars": "A confirmation candle counts within this many candles from the first touch.",
    "confirm_body_pct": "A confirmation candle's body is at least this percent of its range.",
    "comp_bars": "Compression: the candles before the touch that are read.",
    "comp_steps": "Compression: at least this many of them step towards the zone.",
    "comp_mult": "Compression: none of them is wider than this multiple of the average range.",
    "strong_out_mult": "Score: a departure of this many average ranges counts as full strength.",
    "w_fresh": "Score weight of freshness (0 leaves it out).",
    "w_leg_out": "Score weight of the departure strength (0 leaves it out).",
    "w_base_time": "Score weight of a short time in the base (0 leaves it out).",
    "w_htf": "Score weight of a matching higher-timeframe zone (0 leaves it out).",
    "w_compression": "Score weight of a compressed approach (0 leaves it out).",
}
_ATP_DEFAULT = {"out_ref"}          # the function default, not a value from the study (M97-Q2 lists the others)


def _settings() -> tuple[Param, ...]:
    return tuple(Param(name, ref, _SETTING_DOCS[name], name not in _ATP_DEFAULT) for name, _, ref, _ in sd.SETTINGS_TABLE)


SETTINGS = _settings()
KWARGS = ", ".join(f"{p.name}=${p.name}" for p in SETTINGS)
ODDS = tuple(Param(name, ref, _ODDS_DOCS[name], True) for name, _, ref, _ in so.ODDS_TABLE)
ODDS_KWARGS = ", ".join(f"{p.name}=${p.name}" for p in ODDS)
HTF = Param("htf", "none", "A higher timeframe whose closed candles' zones are read (none: no higher timeframe).")


_DEMAND, _SUPPLY, _FRESH, _BREAK_RETEST = '"demand"', '"supply"', '"fresh"', '"break_retest"'


def _call(name: str, *args: str) -> str:
    return f"{name}({', '.join((*args, KWARGS))})"


def _odds_call(name: str, *args: str) -> str:
    return f"{name}({', '.join((*args, KWARGS, ODDS_KWARGS, 'htf=$htf'))})"


def _with(params: tuple[Param, ...], **values: Any) -> tuple[Param, ...]:
    """The settings with some values changed for one scanner (still his to edit)."""
    return tuple(Param(p.name, values[p.name], p.doc, p.ref) if p.name in values else p for p in params)


Z1 = Scanner(
    "supply_demand.demand_enter", "Price entering a fresh demand zone",
    "The bar's low first touches the near line of a demand zone no candle has visited since it formed.",
    _call("SDZoneEnter", _DEMAND),
    BASE_TF, SETTINGS)

Z2 = Scanner(
    "supply_demand.supply_enter", "Price entering a fresh supply zone",
    "The bar's high first touches the near line of a supply zone no candle has visited since it formed.",
    _call("SDZoneEnter", _SUPPLY),
    BASE_TF, SETTINGS)

Z3 = Scanner(
    "supply_demand.zone_formed", "New zone formed",
    "A new zone of the chosen formation is known: a leg-out after a small-candle base, a swing zone's confirming candle,"
    " or a strong break (swap).",
    _call("SDZoneFormed", "$formation"),
    BASE_TF,
    (Param("formation", "any", "DBR, RBR (demand), RBD, DBD (supply), SWING, ENGULF (swing zones), SWAP (swap levels) or"
                                    " any."), *SETTINGS))

Z4 = Scanner(
    "supply_demand.zone_violated", "Zone violated",
    "The bar closes beyond the far line of a demand or a supply zone; the zone is then violated.",
    f"{_call('SDZoneViolated', _DEMAND)} OR {_call('SDZoneViolated', _SUPPLY)}",
    BASE_TF, SETTINGS)

Z5 = Scanner(
    "supply_demand.near_fresh_zone", "Near a fresh zone",
    "The close is within your percent of the near line of the nearest fresh demand or supply zone.",
    f"{_call('SDZoneDistancePct', _DEMAND, _FRESH)} <= $within_pct OR {_call('SDZoneDistancePct', _SUPPLY, _FRESH)} <= $within_pct",
    BASE_TF,
    (Param("within_pct", None, "Distance from the close to the zone's near line, in percent of the close."), *SETTINGS))

# M99 P1
Z6 = Scanner(
    "supply_demand.zone_reached", "Zone reached your way",
    "A zone is reached your way: the first touch of a fresh zone, a confirmation candle back outside its near line, or"
    " the first return to a broken zone.",
    f"{_odds_call('SDZoneEntry', _DEMAND, '$entry')} OR {_odds_call('SDZoneEntry', _SUPPLY, '$entry')}",
    BASE_TF,
    (Param("entry", "touch", "touch, confirm or break_retest (break_retest reads swap levels: turn swap on)."), *SETTINGS, *ODDS, HTF))

Z7 = Scanner(
    "supply_demand.swap_retest", "Swap level first return",
    "A zone broken by a strong candle is now the other side; price comes back to it for the first time.",
    f"{_odds_call('SDZoneEntry', _DEMAND, _BREAK_RETEST)} OR {_odds_call('SDZoneEntry', _SUPPLY, _BREAK_RETEST)}",
    BASE_TF, (*_with(SETTINGS, swap=True), *ODDS, HTF))

Z8 = Scanner(
    "supply_demand.compression", "Compression into a zone",
    "Price reaches a live zone after stepping towards it in small candles (higher lows into supply, lower highs into"
    " demand).",
    f"{_odds_call('SDZoneCompression', _DEMAND)} OR {_odds_call('SDZoneCompression', _SUPPLY)}",
    BASE_TF, (*SETTINGS, *ODDS, HTF))

Z9 = Scanner(
    "supply_demand.zone_score", "Zone odds score at least",
    "The nearest fresh demand or supply zone has an odds score (0-100, its parts weighted by you) of at least your value.",
    f"{_odds_call('SDZoneScore', _DEMAND, _FRESH)} >= $min_score OR {_odds_call('SDZoneScore', _SUPPLY, _FRESH)} >= $min_score",
    BASE_TF,
    (Param("min_score", None, "The lowest score listed (0-100)."), *SETTINGS, *ODDS, HTF))

Z10 = Scanner(
    "supply_demand.htf_with", "Zone with a higher-timeframe zone",
    "The nearest fresh demand or supply zone overlaps a live zone of the same side on your higher timeframe (its closed"
    " candles only; the zones stay separate).",
    f"{_odds_call('SDZoneHtfWith', _DEMAND, _FRESH)} OR {_odds_call('SDZoneHtfWith', _SUPPLY, _FRESH)}",
    BASE_TF,
    (*SETTINGS, *ODDS, Param("htf", "1w", "The higher timeframe whose closed candles' zones are read (above the scan's own).")))

SCANNERS: tuple[Scanner, ...] = (Z1, Z2, Z3, Z4, Z5, Z6, Z7, Z8, Z9, Z10)
BY_ID: dict[str, Scanner] = {s.id: s for s in SCANNERS}


def scanner(ident: str) -> Scanner | None:
    return BY_ID.get(ident)


def payload() -> dict[str, Any]:
    return {"scanners": [s.as_dict() for s in SCANNERS], "footer": FOOTER, "ref_label": REF_LABEL, "not_set": NOT_SET}


def _ids() -> list[str]:
    return [s.id for s in SCANNERS]


tx.register_source(SOURCE, _ids, flag=FLAG, also=ALSO, note=FOOTER)


def taxonomy_sample() -> dict[str, Any]:
    """The taxonomy an organisation with the pack on sees (`sd_pack` on, every other default-off source still hidden):
    frontend/src/screener/taxonomySdSample.json."""
    hidden = [name for name in tx.default_hidden() if name != SOURCE]
    return tx.payload(tx.load(), hidden_sources=hidden, hidden_ids=tx.default_hidden_ids())


__all__ = ["SOURCE", "FLAG", "ALSO", "BASE_TF", "FORMATION_CHOICES", "SETTINGS", "KWARGS", "ODDS", "ODDS_KWARGS", "HTF", "SCANNERS", "BY_ID", "scanner",
           "payload", "taxonomy_sample"]


if __name__ == "__main__":      # python -m app.screener.sd_scanners [taxonomy] > ../frontend/src/screener/sd[Taxonomy]Sample.json
    import json
    import sys

    import app.main  # noqa: F401 - registers every template source (into the imported modules, not this __main__ copy)
    from app.screener import sd_scanners as _sds
    out = _sds.taxonomy_sample() if sys.argv[1:] == ["taxonomy"] else _sds.payload()
    print(json.dumps(out, indent=1, ensure_ascii=False))
