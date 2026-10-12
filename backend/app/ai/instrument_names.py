"""H-C1 c (review of #230, HIGH4): the instrument names the AI output filter recognises as the object of a trade call,
in any case - "buy reliance", "Buy Reliance Industries", "रिलायन्स खरेदी करा", "रिलायंस खरीदें".

Two sources, merged into one in-process index (lower-cased, whitespace-collapsed, nukta dropped):

* `data/instrument_names.json` - a seed of well-known symbols, company names and their Marathi / Hindi spellings,
  always present (tests, a worker that has not loaded the master yet);
* the instrument master (`securities`: every company name, with "Limited" / "Ltd" dropped), loaded by
  `refresh(session)` at API start-up and in the trading worker; it never fails the caller.

`prefix_len(words)` / `suffix_len(words)` say how many leading / trailing words of a phrase name an instrument (0 = none).
The filter is synchronous and reads only this in-memory index.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Iterable, List, Sequence, Set

log = logging.getLogger(__name__)

SEED_FILE = Path(__file__).resolve().parent / "data" / "instrument_names.json"
MAX_WORDS = 5
_SUFFIXES = re.compile(r"\s+(?:limited|ltd\.?|ltd|inc\.?|corporation|corp\.?)$", re.IGNORECASE)
_EDGE = "\"'“”‘’«».,!?;:()[]{}।॥"


def norm_word(word: str) -> str:
    w = unicodedata.normalize("NFKC", word).replace("़", "").strip(_EDGE).lower()
    return w


def norm_name(name: str) -> str:
    name = _SUFFIXES.sub("", " ".join(name.split()))
    return " ".join(norm_word(w) for w in name.split() if norm_word(w))


def _seed() -> Set[str]:
    out: Set[str] = set()
    try:
        data = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.exception("instrument names seed not readable: %s", SEED_FILE)
        return out
    for row in data.get("instruments", []):
        symbol = str(row.get("symbol", ""))
        for name in [symbol if len(symbol) >= 3 else "", *row.get("names", []), *row.get("aliases", [])]:
            n = norm_name(str(name))
            if n:
                out.add(n)
    return out


_NAMES: Set[str] = _seed()
_FROM_MASTER: Set[str] = set()


def add(names: Iterable[str]) -> int:
    """Adds company names to the index (tests and `refresh`); returns how many were new. A one-word name shorter than
    five letters is left out (it would read ordinary words as instruments); upper-case tickers are matched separately."""
    before = len(_NAMES)
    for name in names:
        n = norm_name(str(name or ""))
        if n and len(n.split()) <= MAX_WORDS and (" " in n or len(n) >= 5):
            _NAMES.add(n)
            _FROM_MASTER.add(n)
    return len(_NAMES) - before


async def refresh(session) -> int:  # type: ignore[no-untyped-def]
    """Loads every active security's symbol and company name from the instrument master. Never raises."""
    try:
        from sqlalchemy import select

        from app.db.models import SecurityRecord
        rows = (await session.execute(select(SecurityRecord.symbol, SecurityRecord.name).where(SecurityRecord.status != "DELISTED"))).all()
    except Exception:  # noqa: BLE001 - the seed still applies
        log.warning("instrument names: master not loaded", exc_info=True)
        return 0
    return add([company for _symbol, company in rows])


def known(name: str) -> bool:
    return norm_name(name) in _NAMES


def prefix_len(words: Sequence[str]) -> int:
    """How many leading words name an instrument (the longest match, at most MAX_WORDS)."""
    cleaned = [norm_word(w) for w in words[:MAX_WORDS]]
    for n in range(len(cleaned), 0, -1):
        if " ".join(cleaned[:n]) in _NAMES:
            return n
    return 0


def suffix_len(words: Sequence[str]) -> int:
    """How many trailing words name an instrument (the longest match, at most MAX_WORDS)."""
    cleaned = [norm_word(w) for w in list(words)[-MAX_WORDS:]]
    for n in range(len(cleaned), 0, -1):
        if " ".join(cleaned[-n:]) in _NAMES:
            return n
    return 0


def names() -> List[str]:
    return sorted(_NAMES)


__all__ = ["refresh", "add", "known", "prefix_len", "suffix_len", "names", "norm_name", "norm_word"]
