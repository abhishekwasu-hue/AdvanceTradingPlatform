"""Strategy templates, family F: the option seller packs (owner order section 7, family F).

A pack is NOT a new recipe. It is an ordinary `atp.template/1` template (`templates/F1.json`, `F2.json`) whose setting
`source` picks a MEMBER template, and every part of each `source` variant is the member's own expression, word for word,
with the member's own settings (same keys, labels, defaults and bounds). So a pack signals on exactly the member's bars,
with the member's invalidation and exit signal; only the words differ: the signal text adds the pack's reading of the
side ("read as a bullish continuation ..." / "read as the lower edge of the range ...") for the option structure the user
set up. `MEMBERS` records that mapping, and the tests hold every pack variant equal to its member, part by part and
series by series, so a later change to one of them without the other fails.

What the user's option deployment does with the side is his own setting (instrument kind, option strategy - a bull put
spread on a bullish signal, a bear call spread on a bearish one, an iron condor on either -, the strikes, the width, the
expiry, the lots and the exits). A pack has no field for any of these (the strict schema has none) and never chooses one.

Members: F1 <- D1 (Supertrend flip), D3 (moving-average pullback in trend); F2 <- A2 (range edges), E3 (Bollinger mean
reversion in a range). Version 2 (TPL-7) added D3 and E3 as new `source` choices: each member's settings join the pack's
(a key two members share - F2's swing size - is one setting with the same type, default and bounds), a member that
switches a part on its own setting keeps that switch nested under `source`, the signal text is the member's own per
`source`, and a member's timeframes stay its own (`timeframes_by`). Version 1 of each pack is kept word for word
(`templates/archive/`), so a strategy id adopted on version 1 keeps exactly its behaviour. A1 joins F1 once it is in the
library (areas, TPL-Q23; OPEN_QUESTIONS TPL-Q50).
"""
from __future__ import annotations

from typing import Dict, Tuple

from app.strategy_templates import library
from app.strategy_templates.compile import Settings, resolve_settings
from app.strategy_templates.schema import Template

SOURCE = "source"
# pack id -> {source choice: member template id}
MEMBERS: Dict[str, Dict[str, str]] = {
    "F1": {"supertrend_flip": "D1", "ma_pullback": "D3"},
    "F2": {"range_edges": "A2", "band_reversion": "E3"},
}
STRUCTURE_NOTE = ("Option seller pack: the signal only gives the side. The option structure, its strikes, the width, "
                  "the expiry and the lots come from your own bot settings.")


def is_pack(template: Template) -> bool:
    return template.id in MEMBERS


def member(pack: Template, settings: Settings) -> Tuple[Template, Settings]:
    """The member template the pack's `source` names, with the pack's settings that are the member's own (checked again
    as the member's own settings; version 2: the other members' settings are left out)."""
    member_id = MEMBERS[pack.id][str(settings.params[SOURCE])]
    template = library.get(member_id)
    keys = {p.key for p in template.params}
    params = {k: v for k, v in settings.params.items() if k in keys}
    return template, resolve_settings(template, settings.timeframe, settings.sides, params)


__all__ = ["SOURCE", "MEMBERS", "STRUCTURE_NOTE", "is_pack", "member"]
