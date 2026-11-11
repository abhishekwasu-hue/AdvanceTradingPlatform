"""H-C1 c (review of #230, HIGH4): the instrument names the AI output filter recognises as the object of a trade call,
in any case - "buy reliance", "Buy Reliance Industries".

Two sources, merged into one in-process index (lower-cased, whitespace-collapsed):

* `data/instrument_names.json` - a seed of well-known symbols and company names, always present (tests, a worker that
  has not loaded the master yet);
* the instrument master (`securities`: every company name, with "Limited" / "Ltd" dropped, and every symbol of five
  letters or more), loaded by `refresh(session)` at API start-up and in the trading worker, and again every six hours
  (`refresh_if_stale`: a background task in the API, the worker's cycle); it never fails the caller.

`prefix_len(words)` / `suffix_len(words)` say how many leading / trailing words of a phrase name an instrument (0 = none).
The filter is synchronous and reads only this in-memory index. ATP is English only: text with Devanagari is withheld
before the filter reads it (`call_detect.SCRIPT_LABEL`).
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable, List, Sequence, Set

log = logging.getLogger(__name__)

SEED_FILE = Path(__file__).resolve().parent / "data" / "instrument_names.json"
MAX_WORDS = 5
_SUFFIXES = re.compile(r"\s+(?:limited|ltd\.?|ltd|inc\.?|corporation|corp\.?)$", re.IGNORECASE)
_EDGE = "\"'“”‘’«».,!?;:()[]{}"


def norm_word(word: str) -> str:
    w = unicodedata.normalize("NFKC", word).strip(_EDGE).lower()
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


def _seed_symbols() -> dict[str, str]:
    """COP-2 B: the seed's names, aliases and symbols (lower-cased) -> the symbol they name ("reliance" -> "RELIANCE")."""
    out: dict[str, str] = {}
    try:
        data = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    for row in data.get("instruments", []):
        symbol = str(row.get("symbol", "")).strip().upper()
        if not symbol:
            continue
        for name in [symbol if len(symbol) >= 3 else "", *row.get("names", []), *row.get("aliases", [])]:
            n = norm_name(str(name))
            if n:
                out.setdefault(n, symbol)
    return out


# COP-2 B (owner 11 Nov): a symbol written in any case ("read the reliance chart") is understood when it names a known
# instrument - the seed always, the instrument master once `refresh_symbols` has read it. Kept apart from `_NAMES` (the
# output filter's index), so reading the master here never changes what the filter withholds.
_SYMBOLS: dict[str, str] = _seed_symbols()
_LAST_SYMBOLS = {"at": 0.0}


def add(names: Iterable[str]) -> int:
    """Adds company names to the index (tests and `refresh`); returns how many were new. A one-word name shorter than
    five letters is left out (it would read ordinary words as instruments); upper-case tickers are matched separately."""
    before = len(_NAMES)
    for name in names:
        n = norm_name(str(name or ""))
        if n and len(n.split()) <= MAX_WORDS and (" " in n or (len(n) >= 5 and n not in ORDINARY_WORDS)):
            _NAMES.add(n)
            _FROM_MASTER.add(n)
    return len(_NAMES) - before


REFRESH_SECONDS = 6 * 3600
RETRY_SECONDS = 600                     # after a failed read, try again in ten minutes (not six hours)
_LAST_REFRESH = {"at": 0.0}
# Listed symbols that are ordinary words: never matched in lower case (the upper-case ticker rule still applies).
ORDINARY_WORDS = frozenset({
    "campus", "safari", "dollar", "signature", "apollo", "global", "future", "capital", "united", "nature", "focus", "prime",
    "smart", "orient", "sterling", "trident", "power", "steel", "cement", "energy", "india", "metal", "silver", "golden",
    "bharat", "royal", "vision", "unity", "trust", "union", "excel", "ideal", "alpha", "omega", "delta", "sigma", "matrix",
    "infinity", "fusion", "pioneer", "premier", "classic", "modern", "quality", "superior", "balance", "control", "action",
    "active", "stable", "secure", "simple", "silly", "happy", "honest", "master", "magic", "marine", "ocean", "river", "forest",
    "garden", "sunrise", "sunset", "zenith", "eagle", "tiger", "lotus", "rainbow", "crystal", "diamond", "pearl", "jewel",
})


async def refresh(session) -> int:  # type: ignore[no-untyped-def]
    """Loads every listed security's company name, and its symbol when it has five letters or more (any case: "reliance",
    "infosys"), from the instrument master. Never raises; a failed read is rolled back so the caller's session stays
    usable."""
    import time
    try:
        from sqlalchemy import select

        from app.db.models import SecurityRecord
        rows = (await session.execute(select(SecurityRecord.symbol, SecurityRecord.name).where(SecurityRecord.status != "DELISTED"))).all()
    except Exception:  # noqa: BLE001 - the seed still applies
        log.warning("instrument names: master not loaded", exc_info=True)
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001 - nothing more to undo
            pass
        _LAST_REFRESH["at"] = time.monotonic() - REFRESH_SECONDS + RETRY_SECONDS
        return 0
    _LAST_REFRESH["at"] = time.monotonic()
    symbols = [symbol for symbol, _ in rows if len(symbol or "") >= 5 and norm_word(symbol) not in ORDINARY_WORDS]
    return add([company for _, company in rows] + symbols)


async def refresh_if_stale(session) -> int:  # type: ignore[no-untyped-def]
    """`refresh` at most every REFRESH_SECONDS (new listings and renames reach the filter without a restart)."""
    import time
    if time.monotonic() - _LAST_REFRESH["at"] < REFRESH_SECONDS:
        return 0
    return await refresh(session)


def add_symbols(rows: Iterable[tuple[Any, Any]]) -> int:
    """(symbol, company name) rows into the symbol index: the symbol itself when it has five letters or more and is not an
    ordinary word, and the company name (a one-word name only with five letters or more, not an ordinary word). Returns how
    many were new. The seed's own entries are never replaced."""
    before = len(_SYMBOLS)
    for symbol, company in rows:
        sym = str(symbol or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9&-]{1,19}", sym):
            continue
        for name in (sym if len(sym) >= 5 else "", str(company or "")):
            n = norm_name(name)
            if n and len(n.split()) <= MAX_WORDS and (" " in n or (len(n) >= 5 and n not in ORDINARY_WORDS)):
                _SYMBOLS.setdefault(n, sym)
    return len(_SYMBOLS) - before


async def refresh_symbols_if_stale(session) -> int:  # type: ignore[no-untyped-def]
    """COP-2 B: the instrument master's listed securities into the symbol index, at most every REFRESH_SECONDS (after a
    failed read, again in RETRY_SECONDS). Never raises; a failed read is rolled back so the caller's session stays usable."""
    import time
    if _LAST_SYMBOLS["at"] and time.monotonic() - _LAST_SYMBOLS["at"] < REFRESH_SECONDS:
        return 0
    try:
        from sqlalchemy import select

        from app.db.models import SecurityRecord
        rows = (await session.execute(select(SecurityRecord.symbol, SecurityRecord.name).where(SecurityRecord.status != "DELISTED"))).all()
    except Exception:  # noqa: BLE001 - the seed still applies
        log.warning("instrument symbols: master not loaded", exc_info=True)
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001 - nothing more to undo
            pass
        _LAST_SYMBOLS["at"] = time.monotonic() - REFRESH_SECONDS + RETRY_SECONDS
        return 0
    _LAST_SYMBOLS["at"] = time.monotonic()
    return add_symbols([(symbol, name) for symbol, name in rows])


def symbol_named(words: Sequence[str]) -> str | None:
    """COP-2 B: the symbol the leading words name, in any case (the longest match, at most MAX_WORDS words), else None."""
    cleaned = [norm_word(w) for w in words[:MAX_WORDS]]
    for n in range(len(cleaned), 0, -1):
        hit = _SYMBOLS.get(" ".join(cleaned[:n]))
        if hit:
            return hit
    return None


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


__all__ = ["refresh", "refresh_if_stale", "add", "known", "prefix_len", "suffix_len", "names", "norm_name", "norm_word"]
