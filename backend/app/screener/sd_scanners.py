"""M97 PR-2 (docs/design/M97_SUPPLY_DEMAND.md, Z4): the Supply & Demand scanners - a screen library source of their own,
`supply_demand`, behind the `sd_pack` flag (off by default), built the way M94 PR-2 built the intraday source.

* Each scanner is a ScreenQL text over the PR-1 zone functions (app/price_action/sd_zones.py: SDZoneEnter, SDZoneFormed,
  SDZoneViolated, SDZoneDistancePct) with editable `$parameters`. Every detector number is a parameter carrying the
  M97-Q2 reference value ("Reference value - edit before use"); the edge choice is the M97-Q3 reference (wick); the
  leg-out reference is the function default (average range). The distance a user calls "near" is his own: Not set, the
  run asks for it.
* Any list and any timeframe: a scanner opens on the daily timeframe; the builder's timeframe and the universe are his
  to change, and the run route reads the store first for closed bars, as for every screen.
* Signals only (ADR-0006): a scanner lists matches; nothing here places, changes or blocks an order. No buy / sell /
  target / stop wording in names or cards (the taxonomy lint checks it). Every card carries FOOTER.
"""
from __future__ import annotations

from typing import Any

from app.price_action import sd_zones as sd
from app.screener import taxonomy as tx
from app.screener.intraday import FOOTER, NOT_SET, REF_LABEL, Param, Scanner
from app.screener.registry import SD_FLAG

SOURCE = "supply_demand"
FLAG = SD_FLAG
ALSO: tuple[str, ...] = ()
BASE_TF = "1d"
FORMATION_CHOICES: tuple[str, ...] = (*sd.FORMATIONS, "any")

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
}
_ATP_DEFAULT = {"out_ref"}          # the function default, not a value from the study (M97-Q2 lists the others)


def _settings() -> tuple[Param, ...]:
    return tuple(Param(name, ref, _SETTING_DOCS[name], name not in _ATP_DEFAULT) for name, _, ref, _ in sd.SETTINGS_TABLE)


SETTINGS = _settings()
KWARGS = ", ".join(f"{p.name}=${p.name}" for p in SETTINGS)


_DEMAND, _SUPPLY, _FRESH = '"demand"', '"supply"', '"fresh"'


def _call(name: str, *args: str) -> str:
    return f"{name}({', '.join((*args, KWARGS))})"


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
    "A leg-out candle closes after a small-candle base, forming a new zone of the chosen formation.",
    _call("SDZoneFormed", "$formation"),
    BASE_TF,
    (Param("formation", "any", "DBR, RBR (demand), RBD, DBD (supply) or any."), *SETTINGS))

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

SCANNERS: tuple[Scanner, ...] = (Z1, Z2, Z3, Z4, Z5)
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


__all__ = ["SOURCE", "FLAG", "ALSO", "BASE_TF", "FORMATION_CHOICES", "SETTINGS", "KWARGS", "SCANNERS", "BY_ID", "scanner",
           "payload", "taxonomy_sample"]


if __name__ == "__main__":      # python -m app.screener.sd_scanners [taxonomy] > ../frontend/src/screener/sd[Taxonomy]Sample.json
    import json
    import sys

    import app.main  # noqa: F401 - registers every template source (into the imported modules, not this __main__ copy)
    from app.screener import sd_scanners as _sds
    out = _sds.taxonomy_sample() if sys.argv[1:] == ["taxonomy"] else _sds.payload()
    print(json.dumps(out, indent=1, ensure_ascii=False))
