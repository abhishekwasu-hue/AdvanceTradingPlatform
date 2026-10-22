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

Members today: F1 <- D1 (Supertrend flip); F2 <- A2 (range edges). A1, D3 (F1) and E3 (F2) join as new `source`
choices in a new pack version once those templates are in the library (OPEN_QUESTIONS TPL-F1).
"""
from __future__ import annotations

from typing import Dict, Tuple

from app.strategy_templates import library
from app.strategy_templates.compile import Settings, resolve_settings
from app.strategy_templates.schema import Template

SOURCE = "source"
# pack id -> {source choice: member template id}
MEMBERS: Dict[str, Dict[str, str]] = {
    "F1": {"supertrend_flip": "D1"},
    "F2": {"range_edges": "A2"},
}
STRUCTURE_NOTE = ("Option seller pack: the signal only gives the side. The option structure, its strikes, the width, "
                  "the expiry and the lots come from your own bot settings.")


def is_pack(template: Template) -> bool:
    return template.id in MEMBERS


def member(pack: Template, settings: Settings) -> Tuple[Template, Settings]:
    """The member template the pack's `source` names, with the pack's settings less `source` (checked again as the
    member's own settings)."""
    member_id = MEMBERS[pack.id][str(settings.params[SOURCE])]
    template = library.get(member_id)
    params = {k: v for k, v in settings.params.items() if k != SOURCE}
    return template, resolve_settings(template, settings.timeframe, settings.sides, params)


__all__ = ["SOURCE", "MEMBERS", "STRUCTURE_NOTE", "is_pack", "member"]
