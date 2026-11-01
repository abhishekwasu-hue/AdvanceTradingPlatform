"""M72 PR-2 (CP-2, finding F7): the planner's answer cards and follow-up chips, built by code from tool results.

The model never writes a table, a chart level or a figure inside a card. Its JSON answer only NAMES what to show:
`cards: [{"call": "<tool call id>", "title": "<short words>"}]` and `chips: ["chart" | "alert" | "backtest" | "terminal"]`.
Here the server turns each named call's own result into a table block (the same `{"type": "table"}` block the chat jobs
send), so every number in a card is a value of that call's result - nothing else can reach it (tested).

* A card is built only from a SUCCESSFUL first-party call of this request: an unknown id, a failed call or third-party
  data (news) is skipped; a calc_* step shows its inputs and its result.
* Ids and internal keys are never shown (`id`, `*_id`, `*_sha256`, `run_*`); column labels are plain words.
* The title the model gives may carry no digit (a figure belongs to the data); otherwise the tool's plain name.
* At most `MAX_CARDS` cards, `MAX_ROWS` rows and `MAX_COLUMNS` columns each - the rest stays in the data.
* Chips are fixed actions with fixed words: "Show on chart" and "Open in Terminal" open the symbol a tool read, in place;
  "Make this an alert" and "Backtest this" ask the next question in the user's name only when he taps (the alert stays a
  PROPOSED row a person approves, the backtest uses only values he states). A chip that needs a symbol is dropped when no
  tool read one. Nothing here places, blocks or changes an order (ADR-0006).
"""
from __future__ import annotations

import re
from typing import Any

MAX_CARDS = 3
MAX_ROWS = 12
MAX_COLUMNS = 6
TITLE_MAX = 60
CHIP_KINDS = ("chart", "alert", "backtest", "terminal")
_HIDDEN = re.compile(r"^(id|.*_id|.*_ids|.*_sha256|run_.*|.*_hash|token.*|.*_token)$")
_WORDS = {"pnl": "P&L", "oi": "OI", "pcr": "PCR", "iv": "IV", "atm": "ATM", "ltp": "Last price", "sl": "Stop", "qty": "Quantity",
          "pct": "%", "inr": "(INR)", "ce": "CE", "pe": "PE", "tf": "Timeframe", "as": "as", "of": "of"}


def label_of(key: str) -> str:
    """A data key in plain words: `highest_put_oi_strike` -> "Highest put OI strike"."""
    parts = [p for p in re.split(r"[_\s]+", str(key)) if p]
    words = [_WORDS.get(p.lower(), p.lower()) for p in parts]
    text = " ".join(words).replace(" %", " %").strip() or str(key)
    return text[0].upper() + text[1:]


def _scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _kind(values: list[Any]) -> str:
    """"number" (shown whole) only when every value is a whole number; "ratio" (two decimals) for any other figure, so a
    value like 101.5 is never shown rounded to 102."""
    real = [v for v in values if v is not None]
    if not real or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in real):
        return "text"
    return "number" if all(float(v).is_integer() for v in real) else "ratio"


def _visible(key: str) -> bool:
    return not _HIDDEN.match(str(key).lower())


def _rows_table(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    keys: list[str] = []
    for row in rows[:MAX_ROWS]:
        for k, v in row.items():
            if k not in keys and _visible(k) and _scalar(v):
                keys.append(k)
    keys = keys[:MAX_COLUMNS]
    shown = [{k: row.get(k) for k in keys} for row in rows[:MAX_ROWS]]
    columns = [{"key": k, "label": label_of(k), "kind": _kind([r.get(k) for r in shown])} for k in keys]
    return columns, shown


def _pairs_table(data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = [{"item": label_of(k), "value": v} for k, v in data.items() if _visible(k) and _scalar(v)][:MAX_ROWS]
    return [{"key": "item", "label": "Item", "kind": "text"}, {"key": "value", "label": "Value", "kind": _kind([r["value"] for r in rows])}], rows


def table_from(data: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
    """(columns, rows) for a tool result: its first list of records as rows, else its plain fields as item / value pairs."""
    if isinstance(data, list) and data and all(isinstance(r, dict) for r in data):
        return _rows_table(data)
    if not isinstance(data, dict):
        return None
    for value in data.values():
        if isinstance(value, list) and value and all(isinstance(r, dict) for r in value):
            columns, rows = _rows_table(value)
            if columns and rows:
                return columns, rows
    columns, rows = _pairs_table(data)
    return (columns, rows) if rows else None


def _title(requested: Any, tool: str) -> str:
    from app.ai.copilot_chat import TOOL_NAMES
    text = str(requested or "").strip()[:TITLE_MAX]
    if not text or re.search(r"\d", text):
        return TOOL_NAMES.get(tool) or label_of(tool.removeprefix("get_"))
    return text


def build_cards(requested: list[dict[str, Any]], calls: list[dict[str, Any]], results: dict[str, Any], first_party: set) -> list[dict[str, Any]]:
    """Table blocks for the calls the model named, each from that call's own result only."""
    by_id = {c["id"]: c for c in calls if c.get("ok")}
    out: list[dict[str, Any]] = []
    seen: set = set()
    for item in requested:
        call_id = str(item.get("call") or "")
        call = by_id.get(call_id)
        if call is None or call_id in seen or call_id not in first_party or call_id not in results:
            continue
        data = results[call_id]
        if call["name"].startswith("calc_"):
            data = {**{k: v for k, v in (call.get("arguments") or {}).items() if k != "label"}, **(data if isinstance(data, dict) else {"result": data})}
        table = table_from(data)
        if table is None:
            continue
        seen.add(call_id)
        columns, rows = table
        out.append({"type": "table", "table": {"title": _title(item.get("title"), call["name"]), "columns": columns, "rows": rows}, "call": call_id})
        if len(out) >= MAX_CARDS:
            break
    return out


def symbol_read(calls: list[dict[str, Any]]) -> str | None:
    """The first symbol a successful tool call read (its own argument), upper case."""
    for c in calls:
        sym = (c.get("arguments") or {}).get("symbol") if c.get("ok") else None
        if isinstance(sym, str) and re.fullmatch(r"[A-Za-z0-9&\-\s]{1,30}", sym.strip()):
            return sym.strip().upper()
    return None


def build_chips(requested: list[str], calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The follow-up chips the model asked for, in fixed words; a chip that needs a symbol needs one a tool read."""
    symbol = symbol_read(calls)
    out: list[dict[str, Any]] = []
    for kind in requested:
        if kind not in CHIP_KINDS or any(c["kind"] == kind for c in out):
            continue
        if kind == "chart" and symbol:
            out.append({"kind": "chart", "label": "Show on chart", "symbol": symbol})
        elif kind == "terminal" and symbol:
            out.append({"kind": "terminal", "label": "Open in Terminal", "symbol": symbol})
        elif kind == "alert" and symbol:
            out.append({"kind": "ask", "label": "Make this an alert", "question": f"Make an alert for {symbol}"})
        elif kind == "backtest" and symbol:
            out.append({"kind": "ask", "label": "Backtest this", "question": f"Backtest a strategy on {symbol}"})
    return out


def card_numbers(card: dict[str, Any]) -> list[float]:
    """Every number a card shows (the tests check each one is a value of its call's result)."""
    nums: list[float] = []
    for row in card["table"]["rows"]:
        nums.extend(float(v) for v in row.values() if isinstance(v, (int, float)) and not isinstance(v, bool))
    return nums


__all__ = ["build_cards", "build_chips", "table_from", "label_of", "card_numbers", "symbol_read", "CHIP_KINDS", "MAX_CARDS"]
