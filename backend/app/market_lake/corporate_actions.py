"""HB1: loading corporate actions into the lake (`md_corporate_actions`) for the B4 adjusted view - from a CSV the
owner uploads (Admin) or passes to the CLI. There is no automatic source yet (an open question for the owner).

Two layouts are accepted (headers matched after removing spaces / punctuation, any case):
1. ATP's own: `symbol` (or `isin`), `ex_date`, `action` (SPLIT / BONUS / RIGHTS / DIVIDEND), `ratio_new`, `ratio_old`,
   `amount`, optional `exchange` (default NSE). Ratios follow B4: `ratio_new / ratio_old` is the share multiplier
   (a 1:2 split, face value 10 -> 5: ratio_new 2, ratio_old 1; a 1:1 bonus: 2 and 1). RIGHTS: `ratio_new` new shares
   for every `ratio_old` held at issue price `amount`. DIVIDEND: `amount` per share.
2. The exchange's corporate-actions export: `SYMBOL`, `PURPOSE`, `FACE VALUE`, `EX-DATE`. The purpose text is read
   for "Bonus a:b", "Face Value Split (Sub-Division) - From Rs X ... To Rs Y" (and consolidations), "Rights a:b @
   Premium Rs p" (issue price = face value + premium) and "Dividend - Rs x" (several dividends on one ex-date add up).
   A purpose that names an adjusting action but cannot be read exactly (a demerger, a percentage dividend without a
   face value, ...) is REPORTED, never guessed; other purposes (AGM, interest, buy-back) are counted and ignored.

Writes follow B1: the same action again is skipped, a changed one becomes the next `version`, nothing is deleted. A
row given only an ISIN is stored under the symbol that ISIN traded as on the ex-date (`md_isin_symbols`).
"""
import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MdCorporateActionRecord, MdIsinSymbolRecord
from app.universe.parsers import parse_date

ACTIONS = ("SPLIT", "BONUS", "RIGHTS", "DIVIDEND")
_MONEY = r"(?:rs\.?|re\.?|inr)\s*([0-9]+(?:\.[0-9]+)?)"


@dataclass(frozen=True)
class Action:
    symbol: Optional[str]
    isin: Optional[str]
    exchange: str
    ex_date: date
    action: str
    ratio_new: Optional[float] = None
    ratio_old: Optional[float] = None
    amount: Optional[float] = None


@dataclass
class LoadReport:
    rows: int = 0
    actions: int = 0
    inserted: int = 0
    corrected: int = 0
    unchanged: int = 0
    ignored: int = 0
    unreadable: List[str] = field(default_factory=list)       # "line N: why" - reported, never guessed

    def as_dict(self) -> Dict:
        return {"rows": self.rows, "actions": self.actions, "inserted": self.inserted, "corrected": self.corrected,
                "unchanged": self.unchanged, "ignored": self.ignored, "unreadable": self.unreadable[:200],
                "unreadable_count": len(self.unreadable)}


def _norm(h: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (h or "").upper())


def _float(value: str) -> Optional[float]:
    text = (value or "").replace(",", "").strip()
    try:
        return float(text) if text not in ("", "-") else None
    except ValueError:
        return None


def parse_purpose(purpose: str, face_value: Optional[float]) -> Tuple[List[Tuple[str, Optional[float], Optional[float], Optional[float]]], Optional[str]]:
    """([(action, ratio_new, ratio_old, amount)], why-unreadable or None) for one exchange PURPOSE text."""
    text = (purpose or "").lower().replace("/-", " ")
    found: List[Tuple[str, Optional[float], Optional[float], Optional[float]]] = []
    problems: List[str] = []
    for m in re.finditer(r"bonus\s*([0-9]+)\s*:\s*([0-9]+)", text):
        a, b = float(m.group(1)), float(m.group(2))
        if a > 0 and b > 0:
            found.append(("BONUS", a + b, b, None))
    if "bonus" in text and not any(f[0] == "BONUS" for f in found):
        problems.append("bonus without a readable a:b ratio")
    split = re.search(r"(?:split|sub-?division|consolidation)[^0-9]*?" + _MONEY + r".*?" + _MONEY, text)
    if split:
        before, after = float(split.group(1)), float(split.group(2))
        if before > 0 and after > 0 and before != after:
            found.append(("SPLIT", before, after, None))
    elif re.search(r"split|sub-?division|consolidation", text):
        problems.append("split / consolidation without readable face values")
    rights = re.search(r"rights\s*([0-9]+)\s*:\s*([0-9]+)(.*)", text)
    if rights:
        a, b, rest = float(rights.group(1)), float(rights.group(2)), rights.group(3)
        premium = re.search(r"premium\s*(?:of\s*)?" + _MONEY, rest)
        price = re.search(r"@\s*" + _MONEY, rest)
        if premium and face_value is not None:
            found.append(("RIGHTS", a, b, face_value + float(premium.group(1))))
        elif price and not premium:
            found.append(("RIGHTS", a, b, float(price.group(1))))
        else:
            problems.append("rights without a readable issue price (premium needs the face value)")
    elif "rights" in text:
        problems.append("rights without a readable a:b ratio")
    if "dividend" in text:
        amounts = [float(m.group(1)) for m in re.finditer(r"dividend[^0-9%]*?" + _MONEY, text)]
        percents = [float(m.group(1)) for m in re.finditer(r"dividend[^0-9]*?([0-9]+(?:\.[0-9]+)?)\s*%", text)]
        if percents and face_value is not None:
            amounts += [p / 100 * face_value for p in percents]
        elif percents:
            problems.append("percentage dividend without a face value")
        if amounts:
            found.append(("DIVIDEND", None, None, round(sum(amounts), 4)))
        elif not percents:
            problems.append("dividend without a readable amount")
    for word in ("demerger", "scheme of arrangement", "capital reduction", "reduction of capital"):
        if word in text:
            problems.append(f"{word}: needs a factor entered by hand")
    return found, ("; ".join(problems) if problems else None)


def parse_csv(content: bytes, default_exchange: str = "NSE") -> Tuple[List[Action], LoadReport]:
    report = LoadReport()
    text = content.decode("utf-8-sig", errors="replace")
    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if not rows:
        report.unreadable.append("empty file")
        return [], report
    head = [_norm(h) for h in rows[0]]

    def col(*names: str) -> Optional[int]:
        return next((head.index(n) for n in names if n in head), None)

    i_sym, i_isin, i_ex = col("SYMBOL"), col("ISIN", "ISINNUMBER"), col("EXCHANGE")
    i_date, i_action = col("EXDATE"), col("ACTION")
    i_new, i_old, i_amount = col("RATIONEW"), col("RATIOOLD"), col("AMOUNT")
    i_purpose, i_fv = col("PURPOSE"), col("FACEVALUE")
    if i_date is None or (i_sym is None and i_isin is None) or (i_action is None and i_purpose is None):
        report.unreadable.append(f"unexpected layout: need ex_date, symbol or isin, and action or purpose (headers {rows[0]})")
        return [], report
    out: List[Action] = []
    for n, row in enumerate(rows[1:], start=2):
        report.rows += 1

        def cell(i: Optional[int]) -> str:
            return row[i].strip() if i is not None and i < len(row) else ""

        ex_date = parse_date(cell(i_date))
        symbol, isin = cell(i_sym).upper() or None, cell(i_isin).upper() or None
        exchange = (cell(i_ex) or default_exchange).upper()[:20]
        if ex_date is None or not (symbol or isin):
            report.unreadable.append(f"line {n}: no readable ex-date or symbol")
            continue
        if i_action is not None and cell(i_action):
            action = cell(i_action).upper()
            if action not in ACTIONS:
                report.unreadable.append(f"line {n}: unknown action {action!r}")
                continue
            new, old, amount = _float(cell(i_new)), _float(cell(i_old)), _float(cell(i_amount))
            if action in ("SPLIT", "BONUS") and not (new and old and new > 0 and old > 0):
                report.unreadable.append(f"line {n}: {action} needs ratio_new and ratio_old")
                continue
            if action == "RIGHTS" and not (new and old and amount is not None):
                report.unreadable.append(f"line {n}: RIGHTS needs ratio_new, ratio_old and amount (issue price)")
                continue
            if action == "DIVIDEND" and not amount:
                report.unreadable.append(f"line {n}: DIVIDEND needs amount")
                continue
            out.append(Action(symbol, isin, exchange, ex_date, action, new, old, amount))
            continue
        found, problem = parse_purpose(cell(i_purpose), _float(cell(i_fv)))
        if problem:
            report.unreadable.append(f"line {n} ({symbol or isin} {ex_date}): {problem} - purpose {cell(i_purpose)!r}"[:400])
        if not found and not problem:
            report.ignored += 1
        out += [Action(symbol, isin, exchange, ex_date, a, new, old, amount) for a, new, old, amount in found]
    out = _merge(out)
    report.actions = len(out)
    return out, report


def _merge(actions: List[Action]) -> List[Action]:
    """One action per (instrument, ex-date, kind): dividends on the same ex-date add up (interim + special on two
    lines), an exact repeat is dropped; two different share actions of one kind on one day stay apart (the later line
    becomes the next version when stored - the upload decides)."""
    out: List[Action] = []
    by_key: Dict[Tuple, int] = {}
    for a in actions:
        key = (a.symbol, a.isin, a.exchange, a.ex_date, a.action)
        if key in by_key and a.action == "DIVIDEND":
            i = by_key[key]
            out[i] = Action(a.symbol, a.isin, a.exchange, a.ex_date, a.action, None, None,
                            round((out[i].amount or 0) + (a.amount or 0), 4))
            continue
        if key in by_key and out[by_key[key]] == a:
            continue
        by_key[key] = len(out)
        out.append(a)
    return out


async def _symbol_for(session: AsyncSession, isin: str, exchange: str, day: date) -> Optional[str]:
    rows = list(await session.scalars(select(MdIsinSymbolRecord).where(MdIsinSymbolRecord.isin == isin,
                                                                       MdIsinSymbolRecord.exchange == exchange)))
    inside = [r for r in rows if r.first_date <= day <= r.last_date]
    if inside:
        return inside[0].symbol
    before = [r for r in rows if r.last_date < day]          # an ex-date after the last trade we saw: the latest symbol
    return max(before, key=lambda r: r.last_date).symbol if before else None


def _same(row: MdCorporateActionRecord, a: Action) -> bool:
    def eq(x, y) -> bool:
        return (x is None and y is None) or (x is not None and y is not None and abs(float(x) - float(y)) < 1e-9)
    return eq(row.ratio_new, a.ratio_new) and eq(row.ratio_old, a.ratio_old) and eq(row.amount, a.amount)


async def store_actions(session: AsyncSession, actions: List[Action], report: LoadReport, *, source: str = "ca_csv",
                        commit: bool = True) -> LoadReport:
    now = datetime.now(timezone.utc)
    for a in actions:
        symbol = a.symbol or (await _symbol_for(session, a.isin, a.exchange, a.ex_date) if a.isin else None)
        if not symbol:
            report.unreadable.append(f"{a.isin} {a.ex_date} {a.action}: no symbol known for this ISIN on the ex-date")
            continue
        latest = await session.scalar(select(MdCorporateActionRecord).where(
            MdCorporateActionRecord.symbol == symbol[:40], MdCorporateActionRecord.exchange == a.exchange,
            MdCorporateActionRecord.ex_date == a.ex_date, MdCorporateActionRecord.action == a.action)
            .order_by(MdCorporateActionRecord.version.desc()).limit(1))
        if latest is not None and _same(latest, a):
            report.unchanged += 1
            continue
        session.add(MdCorporateActionRecord(symbol=symbol[:40], exchange=a.exchange, ex_date=a.ex_date, action=a.action,
                                            ratio_new=a.ratio_new, ratio_old=a.ratio_old, amount=a.amount,
                                            version=(latest.version + 1) if latest is not None else 1, source=source[:30],
                                            ingested_at=now))
        await session.flush()
        if latest is None:
            report.inserted += 1
        else:
            report.corrected += 1
    if commit:
        await session.commit()
    return report


async def load_csv(session: AsyncSession, content: bytes, *, source: str = "ca_csv", commit: bool = True) -> LoadReport:
    actions, report = parse_csv(content)
    return await store_actions(session, actions, report, source=source, commit=commit)
