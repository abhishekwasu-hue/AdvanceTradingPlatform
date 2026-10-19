"""Extension point for OPTIONAL subjective pattern modules (Q-010).

Elliott Wave and Harmonic detection are deliberately EXCLUDED from the core scanner. If a future authorised phase adds
such a module it must be registered here and obey these rules, enforced at registration and at run time:
* every result is labelled exactly "candidate" and carries `evidence` (what was measured) and `uncertainty`;
* results are informational only: they are never passed to strategies, scores, qualification or alerts
  (`affects_score` must be False and the core never reads this registry);
* modules run only when a user explicitly asks for them.
The registry is EMPTY in this release.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

REGISTRY: dict[str, "OptionalModule"] = {}
EXCLUDED_FROM_CORE = ("elliott_wave", "harmonic_patterns")


@dataclass
class OptionalModule:
    name: str
    detect: Callable[[object], list[dict]]        # TFAnalysis -> raw results
    description: str = ""
    affects_score: bool = False
    tags: list[str] = field(default_factory=list)


def register(m: OptionalModule) -> None:
    if m.affects_score:
        raise ValueError("optional subjective modules may not influence scores, qualification or alerts")
    REGISTRY[m.name] = m


def run(name: str, analysis) -> list[dict]:
    """Explicit, user-requested run. Every result is validated: label 'candidate' + evidence + uncertainty."""
    if name not in REGISTRY:
        raise KeyError(f"optional module '{name}' is not installed")
    out = []
    for r in REGISTRY[name].detect(analysis):
        if r.get("label") != "candidate" or not r.get("evidence") or r.get("uncertainty") in (None, ""):
            raise ValueError(f"{name}: results must be labelled 'candidate' with evidence and uncertainty")
        out.append({**r, "module": name, "informational_only": True})
    return out
