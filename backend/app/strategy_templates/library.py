"""Strategy templates: the built-in library (one JSON file per template id, `templates/<id>.json`).

Each file is a versioned template; a change of logic is a new `version` (stored versions are immutable once users
adopt them - the adopt / copy flow and the store are the UI step's work, owner order section 10 step 3).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

from app.strategy_templates import language
from app.strategy_templates.compile import Settings
from app.strategy_templates.schema import Template

TEMPLATES_DIR = Path(__file__).parent / "templates"


@lru_cache(maxsize=1)
def _load() -> Dict[str, Template]:
    out: Dict[str, Template] = {}
    for path in sorted(TEMPLATES_DIR.glob("*.json")):
        template = Template.model_validate_json(path.read_text(encoding="utf-8"))
        if path.stem != template.id:
            raise ValueError(f"{path.name}: the file name must be the template id {template.id}")
        out[template.id] = template
    return out


def templates() -> List[Template]:
    return list(_load().values())


def get(template_id: str) -> Template:
    try:
        return _load()[template_id]
    except KeyError:
        raise KeyError(f"unknown template {template_id!r}") from None


def signal_text(template: Template, settings: Settings, side: str, context_timeframe: Optional[str] = None) -> str:
    """The signal's sentence in structure words (section 6): the side's tokens whose `when` setting is on."""
    parts = template.signal_text.long if side == "long" else template.signal_text.short
    tokens = [p.token for p in parts if p.when is None or bool(settings.params.get(p.when))]
    return language.render(tokens, timeframe=settings.timeframe, context_timeframe=context_timeframe)


__all__ = ["TEMPLATES_DIR", "templates", "get", "signal_text"]
