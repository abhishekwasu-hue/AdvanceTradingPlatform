"""M77 SX-1: the one backend Indian number formatter (app/study/numfmt.py).

1. Parity with the frontend: every case in frontend/src/ui/numberFormatShared.json (which the frontend's own
   numberFormatParity.test.ts checks against ui/format.ts) gives the same text here.
2. The two older helpers, now wrappers, give byte-identical output: their previous code is frozen below and compared over
   a wide set of inputs (their existing callers' shapes: prices, OI, P&L, quantities, ties, tiny and huge values).
3. A missing value is "not available".
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from app.journal import export as journal_export
from app.option_chain import analyses
from app.study import numfmt

PARITY = Path(__file__).resolve().parents[2] / "frontend" / "src" / "ui" / "numberFormatShared.json"


# --- the previous implementations, frozen (journal/export.py and option_chain/analyses.py before SX-1) -------------

def _old_indian(value, decimals=2):
    if value is None:
        return "-"
    text = f"{abs(value):.{decimals}f}"
    whole, _, frac = text.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        pairs = []
        while len(head) > 2:
            pairs.insert(0, head[-2:])
            head = head[:-2]
        whole = ",".join(([head] if head else []) + pairs + [tail])
    sign = "-" if value < 0 and float(text) != 0 else ""
    return f"{sign}{whole}" + (f".{frac}" if frac else "")


def _old_rupees(value, *, signed=False):
    if value is None:
        return "-"
    body = f"Rs {_old_indian(abs(value))}"
    if value < 0 and round(value, 2) != 0:
        return f"-{body}"
    return f"+{body}" if signed and round(value, 2) > 0 else body


def _old_group_indian(digits):
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail])


def _old_fmt_number(value, decimals=0):
    text = f"{abs(value):.{decimals}f}"
    whole, _, frac = text.partition(".")
    sign = "-" if value < 0 and float(text) != 0 else ""
    return f"{sign}{_old_group_indian(whole)}{'.' + frac if frac else ''}"


def _old_fmt_signed(value, decimals=0):
    plain = _old_fmt_number(value, decimals)
    return f"+{plain}" if value > 0 and float(f"{abs(value):.{decimals}f}") != 0 else plain


def _inputs() -> list[float]:
    base = [0.0, -0.0, 0.004, 0.005, 0.0049, 0.015, 0.125, 0.5, 1.5, 2.5, 1.005, 2.675, 9.995, 99.5, 999.5, 3120.5,
            7.0, 42.25, 999.0, 1000.0, 99999.0, 99999.995, 100000.0, 123456.789, 1234567.891, 9999999.6, 10000000.0,
            24000000.0, 12345678.5, 1234567890.123, 1e15 + 0.5, 1e21, 1e23, 1.7976931348623157e308, 5e-324,
            22000.0, 24812.35, 24812.05, 122.5, 0.05, 37.65, 1250000.0, 87654321.0]
    out: list[float] = []
    for v in base:
        out += [v, -v]
    k = 1.0
    for _ in range(40):                      # a sweep of magnitudes with fractional parts
        out += [k * 1.37, -k * 2.71, k + 0.5, -(k + 0.005)]
        k *= 3.3
    return out + [math.inf, -math.inf, math.nan]


INPUTS = _inputs()


@pytest.mark.parametrize("decimals", [0, 1, 2, 3])
def test_indian_wrapper_is_byte_identical(decimals: int) -> None:
    for v in INPUTS:
        assert journal_export.indian(v, decimals) == _old_indian(v, decimals), (v, decimals)
    assert journal_export.indian(None, decimals) == _old_indian(None, decimals) == "-"


def test_rupees_wrapper_is_byte_identical() -> None:
    for v in [*INPUTS, None]:
        for signed in (False, True):
            assert journal_export.rupees(v, signed=signed) == _old_rupees(v, signed=signed), (v, signed)


@pytest.mark.parametrize("decimals", [0, 1, 2])
def test_option_chain_wrappers_are_byte_identical(decimals: int) -> None:
    for v in INPUTS:
        assert analyses.fmt_number(v, decimals) == _old_fmt_number(v, decimals), (v, decimals)
        assert analyses.fmt_signed(v, decimals) == _old_fmt_signed(v, decimals), (v, decimals)
    for digits in ["", "7", "999", "1000", "12345", "1234567", "123456789012"]:
        assert analyses._group_indian(digits) == _old_group_indian(digits)


def test_existing_examples_keep_their_text() -> None:
    assert journal_export.indian(12345678.5) == "1,23,45,678.50"
    assert journal_export.rupees(-123456.0) == "-Rs 1,23,456.00"
    assert analyses.fmt_number(1250000) == "12,50,000"


def _parity_cases() -> list[dict]:
    return json.loads(PARITY.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", _parity_cases(), ids=lambda c: f"{c['fn']}-{c['v']}-{c.get('decimals', '')}-{c.get('opts', {})}")
def test_parity_with_frontend(case: dict) -> None:
    opts = case.get("opts", {})
    if case["fn"] == "number":
        got = numfmt.format_number(case["v"], case["decimals"], trim=opts.get("trim", False))
    else:
        got = numfmt.format_inr(case["v"], paise=opts.get("paise", False), compact=opts.get("compact", False),
                                signed=case["fn"] == "signedInr")
    assert got == case["out"]


def test_parity_file_is_shared() -> None:
    assert len(_parity_cases()) > 40
    test_ts = PARITY.with_name("numberFormatParity.test.ts").read_text(encoding="utf-8")
    assert "numberFormatShared.json" in test_ts


@pytest.mark.parametrize("v", [None, math.nan, math.inf, -math.inf])
def test_missing_is_not_available(v) -> None:
    assert numfmt.format_number(v, 2) == "not available"
    assert numfmt.format_inr(v) == "not available"
    assert numfmt.format_inr(v, paise=True, signed=True) == "not available"
    assert numfmt.format_number(v, missing="-") == "-"


def test_sign_and_symbols() -> None:
    assert numfmt.format_number(-1234.5, 1) == "−1,234.5"
    assert "-" not in numfmt.format_number(-1234.5, 1)
    assert numfmt.format_number(12.3, 2, signed=True) == "+12.30"
    assert numfmt.format_number(0.001, 2, signed=True) == "0.00"
    assert numfmt.format_inr(-700) == "−₹700"
    assert numfmt.group_indian("12450000") == "1,24,50,000"


def test_journal_pdf_bytes_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """The journal PDF built through the new wrappers equals the one built with the frozen previous helpers."""
    from datetime import UTC, datetime

    def row(i: int, qty: float, pnl: float | None, r: float | None) -> dict:
        return {"entry_time": f"2026-01-0{i}T04:00:00", "outside_atp": i % 2 == 0, "symbol": "SYMBOL", "direction": "LONG",
                "setup": "setup", "timeframe": "15m", "quantity": qty, "pnl": pnl, "r_multiple": r, "user_tags": ["mine"],
                "tags": [{"tag": "late", "status": "OPEN"}]}
    rows = [row(1, 75, 1234567.5, 1.5), row(2, 12.5, -0.004, None), row(3, 1500, -98765.435, -0.75), row(4, 1, None, None)]
    totals = {"trades": 4, "closed": 3, "win_rate": 33.35, "net_pnl": 1135802.06, "profit_factor": 12.505,
              "expectancy": -0.005, "avg_win": 1234567.5, "avg_loss": None, "avg_r": 0.25, "outside_atp": 2}
    now = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
    new = journal_export.to_pdf(rows, totals, {"setup": "setup"}, now=now)
    monkeypatch.setattr(journal_export, "indian", _old_indian)
    monkeypatch.setattr(journal_export, "rupees", _old_rupees)
    old = journal_export.to_pdf(rows, totals, {"setup": "setup"}, now=now)
    assert new == old
