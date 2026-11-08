"""M88-R S3: the output check - the report describes; it never recommends. Any of the owner's words (buy / sell /
target / stop / entry / should, in any form) anywhere in the AI's text refuses the answer: the run asks once more,
naming the words found; a second refusal stores nothing and the admin sees why.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

BANNED = re.compile(r"\b(buy\w*|sell\w*|targets?|targeting|targeted|stop\w*|entry|entries|should\w*)\b", re.IGNORECASE)


def texts(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from texts(v)
    elif isinstance(value, list):
        for v in value:
            yield from texts(v)


def banned_words(value: Any) -> list[str]:
    found: list[str] = []
    for text in texts(value):
        for m in BANNED.finditer(text):
            word = m.group(1).lower()
            if word not in found:
                found.append(word)
    return found


SECTIONS = ("index_read", "breadth_read", "sectors_read", "leaders_laggards", "notable_structures", "conclusion")


def parse(text: str) -> dict[str, Any]:
    """The AI's JSON answer (a fenced block or the first {...}); ValueError when it is not the asked shape."""
    raw = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1)
    else:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("the answer was not JSON")
        raw = raw[start:end + 1]
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("the answer was not a JSON object")
    out: dict[str, Any] = {}
    for key in SECTIONS:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"the answer has no '{key}' text")
        out[key] = value.strip()
    stocks = data.get("stocks")
    if not isinstance(stocks, dict):
        raise ValueError("the answer has no per-stock reads")
    out["stocks"] = {str(k).upper(): str(v).strip() for k, v in stocks.items() if isinstance(v, str) and v.strip()}
    return out
