"""Strategy templates: the built-in library (one JSON file per template id, `templates/<id>.json`).

Each file is a versioned template; a change of logic is a new `version` (stored versions are immutable once users
adopt them - the adopt / copy flow and the store are the UI step's work, owner order section 10 step 3). TPL-7: the
version a user adopted before a newer one is kept in `templates/archive/<id>.v<version>.json`, unchanged, and a strategy id
naming it resolves to it (`get_version`), so an adopted pack keeps exactly its behaviour when the pack gains members.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.strategy_templates import language
from app.strategy_templates.compile import Settings
from app.strategy_templates.schema import Template

TEMPLATES_DIR = Path(__file__).parent / "templates"
# TPL-7: earlier versions of a template that users may have adopted, kept word for word (`<id>.v<version>.json`), so a
# strategy id that names one keeps resolving to exactly that version's behaviour (the library lists the newest only)
ARCHIVE_DIR = TEMPLATES_DIR / "archive"


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


@lru_cache(maxsize=1)
def _archive() -> Dict[Tuple[str, int], Template]:
    out: Dict[Tuple[str, int], Template] = {}
    for path in sorted(ARCHIVE_DIR.glob("*.json")):
        template = Template.model_validate_json(path.read_text(encoding="utf-8"))
        if path.stem != f"{template.id}.v{template.version}":
            raise ValueError(f"{path.name}: the file name must be <id>.v<version> ({template.id}.v{template.version})")
        current = _load().get(template.id)
        if current is None or template.version >= current.version:
            raise ValueError(f"{path.name}: an archived version must be older than the library's {template.id}")
        out[(template.id, template.version)] = template
    return out


def get_version(template_id: str, version: int) -> Template:
    """The template `template_id` at `version`: the library's own when it is the newest, else the archived one (TPL-7).
    KeyError when no such version is kept."""
    current = get(template_id)
    if current.version == version:
        return current
    try:
        return _archive()[(template_id, version)]
    except KeyError:
        raise KeyError(f"unknown template version {template_id!r} {version}") from None


def archived() -> List[Template]:
    """Every archived (older) template version, oldest first per id."""
    return [t for _, t in sorted(_archive().items())]


def signal_text(template: Template, settings: Settings, side: str, context_timeframe: Optional[str] = None) -> str:
    """The signal's sentence in structure words (section 6): the side's tokens whose `when` setting is on."""
    text = template.chosen_text(settings.params)
    parts = text.long if side == "long" else text.short
    tokens = [p.token for p in parts if p.when is None or bool(settings.params.get(p.when))]
    tf_words = {placeholder: settings.params.get(key) for placeholder, key in language.TIMEFRAME_SETTING_PLACEHOLDERS.items()}
    return language.render(tokens, timeframe=settings.timeframe, context_timeframe=context_timeframe, **tf_words)


__all__ = ["TEMPLATES_DIR", "ARCHIVE_DIR", "templates", "get", "get_version", "archived", "signal_text"]
