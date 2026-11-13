#!/usr/bin/env python3
"""M68 TS-7 (owner order M68): no new hard-coded trading value in execution / risk / strategy / ticket code.

The rule (M68, binding): ATP never pre-chooses, caps or silently changes a trading choice. Every user choice lives in
the ONE settings registry (backend/app/me/trade_settings.py, Settings > Trade settings), "Not set" until the user saves
it. Fixed in code are only the exchange / broker facts (lot multiple, freeze quantity, market hours, tick size, product
codes, order-type support, statutory charges...) - the TS-1 audit rows TS1-084 to TS1-096 (docs/design/M68_TS1_AUDIT.md).

This check reads the execution / risk / strategy / ticket code (SCOPE below) and fails the build on a TRADING LITERAL
that is not on the allow-list: a literal value given to a name that reads as a trading choice. A trading name is one
that holds a word of TRADING_NAME (lots, quantity, risk, stop / SL, target, trail, square-off, cut-off, product, order
type, slippage, brokerage, capital, reward:risk, daily loss, cool-down, spread width, strike, expiry, hedge, re-entry,
VIX, margin, approval, band, protection, holding, entry mode...). A literal is a number other than 0, 1 and -1 (0.0
only for a float: 1.0 is a 1 % band), one of the order words of ORDER_WORDS (MIS, NRML, MARKET, SL-M, ATM...), or a
clock time ("15:15", time(15, 15)). Where:
* Python (ast): `NAME = literal` (module, class, dataclass / pydantic field, `Field(default=...)`), a parameter default,
  a call keyword (`order_type="MARKET"`), a dict key (`{"lots": 2}`), a comparison with a number (`lots > 500`), and a
  clock time `time(h, m)` anywhere.
* TypeScript (line regex, comments left out): `name = literal`, `name: literal` (object literal / default), and
  `const [name, setName] = useState(literal)`.

The allow-list (`ALLOWED`) is explicit: each entry is one (file, name, value) with the reason it is not a pre-chosen
trading value - an exchange / broker fact (TS1-084 to TS1-096), an exit-only fallback so an exit is never blocked
(ADR-0004), the order's own action (a margin query, a close of his own position), an operational value, a display
size, or a strategy's own suggestion (TS-4). An allow-list entry that matches nothing fails too, so the list stays
exact; the entry is by value, so a changed value is a new literal. Adding an entry needs a reason that names its TS-1
row or its rule; a new user choice goes into the registry instead.

    python3 scripts/check_trading_literals.py            # exit 0 = clean, 1 = findings
    python3 scripts/check_trading_literals.py --root DIR # check another tree (the tests use this)
    python3 scripts/check_trading_literals.py --list     # print every literal found (allowed or not)
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

# Execution / risk / strategy / ticket code. Python packages (whole), frontend directories (whole) and ticket files.
SCOPE_PY: Tuple[str, ...] = (
    "backend/app/execution",
    "backend/app/risk_engine",
    "backend/app/strategy_engine",
    "backend/app/paper",
    "backend/app/trading",
    "backend/app/deployments",
)
SCOPE_TS: Tuple[str, ...] = (
    "frontend/src/fno",
    "frontend/src/trading",
    "frontend/src/risk",
    "frontend/src/copilot/workspace",
    "frontend/src/components/paperTicket.ts",
    "frontend/src/components/PaperTicketCard.tsx",
)
# The settings registry itself is where a user choice is declared (never pre-filled): not scanned (it is outside SCOPE).
REGISTRY = "backend/app/me/trade_settings.py"

# A name is read in snake case (lotSize -> lot_size, MAX_LOTS -> max_lots); a word counts from the start of a part.
# Whole parts only for the short words (so "slot" or "atmosphere" is not "lot" / "atm").
_SHORT_WORDS = r"lots?|qty|sl|rr|loss|atm|otm|itm|vix|band|charges?|hedge|expiry|strikes?"
_WORD_STARTS = (r"quantit|risk|stop|target|trail|break_?even|square|cut_?off|product|order_?type|slippage|brokerage|"
                r"capital|reward|max_trades|max_positions|max_open|daily_loss|cool_?down|spread_width|wing_width|"
                r"re_?entry|margin|approval|protection|holding|entry_mode|size_mult|size_pct|position_size|lot_size|"
                r"trading_window|entry_window|square_off|time_exit")
TRADING_NAME = re.compile(rf"(?:^|_)(?:(?:{_SHORT_WORDS})(?=_|$)|(?:{_WORD_STARTS}))")
ORDER_WORDS = frozenset({"MIS", "NRML", "CNC", "MARKET", "LIMIT", "SL", "SL-M", "SLM", "INTRADAY", "CARRYFORWARD",
                         "DELIVERY", "SWING", "ATM", "OTM", "ITM", "AUTO", "APPROVAL", "PROTECTED_LIMIT"})
CLOCK = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")
TIME_CALLS = frozenset({"time", "dtime", "dt_time", "time_"})


def trading_name(name: str) -> bool:
    """Whether `name` reads as a trading choice (see TRADING_NAME)."""
    return bool(TRADING_NAME.search(re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower()))

# (file, name, value) -> reason. `value` is the literal as written (strings without quotes, time(h, m) as written).
_EX = "backend/app/execution/"
_RISK = "backend/app/risk_engine/"
_STRAT = "backend/app/strategy_engine/"
_PAPER = "backend/app/paper/"
_TRADING = "backend/app/trading/"
_TS1_072 = ("a built-in strategy's own parameter (its stop / target-ratio definition), shown in the Library as Suggested; "
            "nothing is applied until he saves it (TS-4, TS1-072, open owner item M68Q-PR8-1)")
_MARGIN_PROBE = ("a broker margin query (the probe order is never placed): the broker's margin for one unit - a broker fact "
                 "(TS1-095, M64 section C)")
ALLOWED: Dict[Tuple[str, str, str], str] = {
    # --- exchange / broker facts (TS1-084 to TS1-096) ---------------------------------------------------------------------
    (_EX + "resting_book.py", "time", "time(15, 30)"): "fact: a waiting entry rests until the session close - DAY order validity (TS1-085, TS1-084)",
    (_EX + "marketable_limit.py", "order_type", "LIMIT"): "fact: a venue that takes no API market order gets a marketable limit (TS1-094)",
    (_EX + "contract_execution.py", "order_type", "MARKET"): _MARGIN_PROBE,
    (_EX + "prechecks.py", "order_type", "MARKET"): _MARGIN_PROBE,
    (_PAPER + "fno_ticket.py", "order_type", "MARKET"): _MARGIN_PROBE + "; the ticket shows it as information",
    (_EX + "multileg.py", "order_type", "MARKET"): _MARGIN_PROBE + " (written-option and basket margin); the LIVE structure's legs "
                                                  "and their unwind go as market orders wings first (P0.5 / T3) - the structure's own "
                                                  "execution, as the basket (M68Q-7); an unwind is an exit (ADR-0004)",
    (_EX + "contract_execution.py", "MARGIN_SAFETY", "1.0"): "fact: the whole available margin - the broker's margin check (TS1-055 closed by PR-5; his own margin share is a Risk limits switch)",
    ("backend/app/deployments/routes.py", "stop_credit_pct", "100"): "fact: a debit structure cannot lose more than its debit (100 % of it)",
    # --- exits: never blocked (ADR-0004); used only to close or protect HIS open position ---------------------------------
    (_EX + "order_defaults.py", "EXIT_CLOSE_BAND_PCT", "0.5"): "exit-only fallback band where the venue takes no market order and he saved no band (M68Q-6, ADR-0004); never an entry",
    (_EX + "order_defaults.py", "EXIT_STOP_LIMIT_BAND_PCT", "1.0"): "exit-only stop-limit gap where the exchange takes no SL-M and he saved none (M68Q-6, TS1-092, ADR-0004); never an entry",
    (_EX + "marketable_limit.py", "DEFAULT_PROTECTION_PCT", "0.5"): "the broker adapters' exit-only protection fallback (== EXIT_CLOSE_BAND_PCT; M68 PR-1 TS1-011, ADR-0004)",
    (_EX + "router.py", "DEFAULT_MARKET_PROTECTION_PCT", "0.5"): "exit-only fallback (== marketable_limit.DEFAULT_PROTECTION_PCT); an entry's band is his own (M68 PR-1 TS1-011)",
    (_EX + "trigger_plans.py", "order_type", "LIMIT"): "his own target on the filled position, armed as a TARGET limit at HIS level (an exit, ADR-0004)",
    (_TRADING + "position_monitor.py", "order_type", "MARKET"): "a stop / target / time exit of his open position closes at market with the entry's own product (ADR-0004)",
    (_TRADING + "stop_guard.py", "product", "MIS"): "re-arming the stop of his own open position: a non-carry-forward position's product is MIS (broker product code TS1-091; ADR-0004)",
    (_PAPER + "fno_position_actions.py", "order_type", "MARKET"): "Reverse: the other side of his own position at market with the position's own product (his action, as its confirm says)",
    # --- his own choice carried through (not a pre-choice) -------------------------------------------------------------------
    (_EX + "router.py", "order_type", "MARKET"): "the entry request's base type for HIS order style: MARKET as he chose, PROTECTED_LIMIT turns it into a LIMIT below; Not set = no order (M68 PR-1)",
    (_EX + "router.py", "order_type", "LIMIT"): "his PROTECTED_LIMIT order style at his own protection band (M68 PR-1)",
    (_PAPER + "fno_basket.py", "order_type", "MARKET"): "\"Execute basket\" sends every leg at market - the basket's own action, as its confirm step says (M68Q-7); a limit per leg is the single ticket's",
    # --- not a trading value: labels, record sizes, display -----------------------------------------------------------------
    (_EX + "approval.py", "ORIGIN_APPROVAL", "approval"): "a label of where an order came from (the approved-proposal path), not a value",
    (_EX + "tagging.py", "LEG_STOP", "SL"): "the broker order-tag code of a stop leg (a label)",
    (_EX + "resting_book.py", "MAX_TRAIL", "50"): "the number of order-trail log lines kept on a resting order (a record size)",
    (_PAPER + "fno_journal.py", "MAX_TRADES", "60"): "how many closed legs the journal drafts read at most (a list size)",
    ("frontend/src/trading/positionStops.ts", "PLACEHOLDER_STOP_MAX", "0.1"): "display only: the one-tick placeholder stop of a bought option or future without a stop reads \"no stop\" (owner fix T1-6; moved from model.ts, owner bug report 13 Nov)",
    ("frontend/src/fno/ChainActionSheetV2.tsx", "cooldown_minutes", "60"): "a price alert's repeat gap: the alert only notifies, places nothing (not a trading cool-down)",
    ("frontend/src/fno/positionInsights.ts", "cooldown_minutes", "60"): "a price alert's repeat gap: the alert only notifies, places nothing (not a trading cool-down)",
    # --- size multipliers of 1.0 = no change ---------------------------------------------------------------------------------
    (_RISK + "guardian.py", "size_multiplier", "1.0"): "1.0 = the size unchanged (no cut); a cut comes only from his own drawdown / event settings (TS1-054, TS1-058)",
    (_RISK + "vix_gate.py", "size_multiplier", "1.0"): "1.0 = the size unchanged (no cut); a cut comes only from his own VIX gate settings (TS1-056)",
    (_RISK + "vix_gate.py", "time", "time(9, 20)"): "the fallback of the operator's VIX_GATE_CHECK_AT (when the day's India VIX reading is read); the gate is his own switch, OFF unless he sets it (TS1-056)",
    # --- bounds: "a real number of the right sign" (M68 PR-5, TS1-049) -------------------------------------------------------
    (_RISK + "by_default.py", "risk_per_trade_pct", "(0.0, 100.0)"): "bounds of his own value: a % at most 100 (TS1-049), no ATP cap",
    (_RISK + "by_default.py", "daily_loss_pct", "(0.0, 100.0)"): "bounds of his own value: a % at most 100 (TS1-049), no ATP cap",
    (_RISK + "by_default.py", "max_correlated_risk_pct", "(0.0, 50.0)"): "OPEN (found by TS-7, not in the TS-1 table): the N1 F2 correlated-risk cap's own top of 50 %; logged as M68Q-PR9-2",
    # --- read-only legacy markers: a stored old built-in reads "Not set" (TT12-1 rule; never applied) -----------------------
    (_RISK + "by_default.py", "risk_per_trade_pct", "1.0"): "LEGACY_DEFAULTS: read-only marker of the pre-TT-12 built-in (TS1-025); never applied",
    (_RISK + "by_default.py", "daily_loss_pct", "2.0"): "LEGACY_DEFAULTS: read-only marker of the pre-TT-12 built-in (TS1-025); never applied",
    (_RISK + "by_default.py", "max_trades_per_day", "10"): "LEGACY_DEFAULTS: read-only marker of the pre-TT-12 built-in (TS1-025); never applied",
    (_RISK + "by_default.py", "min_rr", "1.5"): "LEGACY_DEFAULTS: read-only marker of the pre-TT-12 built-in (TS1-025); never applied",
    (_RISK + "routes.py", "LEGACY_ORG_MIN_RR", "1.2"): "read-only marker: the organisation's stored old built-in 1.2 reads as Not set (TT12-4); never applied",
    (_STRAT + "declarative.py", "LEGACY_MIN_RR", "1.2"): "read-only marker: a stored old built-in 1.2 reads as Not set (TT12-1 rule); never applied",
    # --- the anonymous console demo: no account, places and stores nothing ---------------------------------------------------
    (_STRAT + "console_routes.py", "product", "MIS"): "the anonymous demo's simulated fill (no account, no order, nothing stored; M68 PR-1)",
    # --- open: found by TS-7, not in the TS-1 table (logged with a default) ---------------------------------------------------
    (_TRADING + "persistence.py", "holding", "INTRADAY"): "OPEN (found by TS-7): the trade record's holding when a caller passes none (the 'Complete the rest' path); logged as M68Q-PR9-1",
    # --- built-in strategies: suggestions only (TS-4) -----------------------------------------------------------------------
    (_STRAT + "base.py", "target_rr", "(1.5, 2.5)"): _TS1_072,
    (_STRAT + "combo_strategies.py", "atr_mult_sl", "1.2"): _TS1_072,
    (_STRAT + "combo_strategies.py", "target_rr", "(1.5, 2.5)"): _TS1_072,
    (_STRAT + "combo_strategies.py", "target_rr", "(1.2, 2.0)"): _TS1_072,
    (_STRAT + "indicator_strategies.py", "atr_mult_sl", "1.0"): _TS1_072,
    (_STRAT + "indicator_strategies.py", "atr_mult_sl", "0.3"): _TS1_072,
    (_STRAT + "indicator_strategies.py", "target_rr", "(1.5, 2.0)"): _TS1_072,
    (_STRAT + "indicator_strategies.py", "target_rr", "(1.5, 2.5)"): _TS1_072,
    (_STRAT + "mtf_strategy.py", "atr_mult_sl", "1.2"): _TS1_072,
    (_STRAT + "mtf_strategy.py", "target_rr", "(1.5, 2.5)"): _TS1_072,
    (_STRAT + "swing_strategies.py", "target_rr", "(2.0, 3.0)"): _TS1_072,
    (_STRAT + "swing_strategies.py", "target_rr", "(2.0, 3.5)"): _TS1_072,
    (_STRAT + "swing_strategies.py", "min_stop_atr", "1.0"): _TS1_072,
    (_STRAT + "swing_strategies.py", "max_stop_atr", "3.0"): _TS1_072,
    (_STRAT + "swing_strategies.py", "stop_atr_inside", "1.0"): _TS1_072,
}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    name: str
    value: str

    @property
    def key(self) -> Tuple[str, str, str]:
        return (self.path, self.name, self.value)

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.name} = {self.value}"


# --- Python -------------------------------------------------------------------------------------------------------------

def _py_literal(node: ast.AST) -> Optional[str]:
    """The literal's text when `node` is a trading literal (see the module docstring), else None."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
        inner = _py_literal(node.operand)
        return None if inner is None else f"-{inner}"
    if isinstance(node, (ast.Tuple, ast.List)) and node.elts and all(_number_node(e) for e in node.elts):
        # target_rr=(1.5, 2.5). A tuple of words (ORDER_TYPES = ("MARKET", "LIMIT")) lists the choices, it chooses none.
        return ast.unparse(node) if any(_py_literal(e) is not None for e in node.elts) else None
    if not isinstance(node, ast.Constant) or isinstance(node.value, bool):
        return None
    v = node.value
    if isinstance(v, int):
        return None if v in (0, 1) else repr(v)
    if isinstance(v, float):
        return None if v == 0.0 else repr(v)
    if isinstance(v, str) and (v.upper() in ORDER_WORDS or CLOCK.match(v)):
        return v
    return None


def _number_node(node: ast.AST) -> bool:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        node = node.operand
    return isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool)


def _number(node: ast.AST) -> bool:
    return _number_node(node) and _py_literal(node) is not None


def _target_name(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _call_name(node: ast.AST) -> Optional[str]:
    return _target_name(node.func) if isinstance(node, ast.Call) else None


def _py_pairs(tree: ast.AST) -> Iterator[Tuple[str, ast.AST]]:
    """(name, value node) of every place a value is given to a name."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            for target in targets:
                name = _target_name(target)
                if name is None or value is None:
                    continue
                yield name, value
                if isinstance(value, ast.Call):                    # Field(default=...), mapped_column(default=...)
                    for kw in value.keywords:
                        if kw.arg in ("default", "server_default"):
                            yield name, kw.value
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            args = node.args
            positional = args.posonlyargs + args.args
            for arg, default in zip(positional[len(positional) - len(args.defaults):], args.defaults):
                yield arg.arg, default
            for arg, kw_default in zip(args.kwonlyargs, args.kw_defaults):
                if kw_default is not None:
                    yield arg.arg, kw_default
        elif isinstance(node, ast.keyword) and node.arg:
            yield node.arg, node.value
        elif isinstance(node, ast.Call) and _call_name(node) in ("get", "getattr", "setdefault", "pop") and len(node.args) >= 2:
            key = node.args[-2]                                    # params.get("target_rr", 2.0), getattr(x, "lots", 2)
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                yield key.value, node.args[-1]
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    yield key.value, value


def scan_python(rel: str, text: str) -> List[Finding]:
    tree = ast.parse(text, filename=rel)
    out: List[Finding] = []
    for name, value in _py_pairs(tree):
        literal = _py_literal(value)
        if literal is not None and trading_name(name):
            out.append(Finding(rel, getattr(value, "lineno", 0), name, literal))
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):                          # lots > 500: a cap on a trading value
            sides = [node.left, *node.comparators]
            names = [n for n in (_target_name(s) for s in sides) if n and trading_name(n)]
            for side in sides:
                if names and _number(side):
                    out.append(Finding(rel, node.lineno, names[0], str(_py_literal(side))))
        elif _call_name(node) in TIME_CALLS and isinstance(node, ast.Call) and node.args \
                and all(isinstance(a, ast.Constant) and isinstance(a.value, int) for a in node.args) \
                and any(isinstance(a, ast.Constant) and a.value for a in node.args):          # time(0, 0): a day start
            hm = ", ".join(str(a.value) for a in node.args if isinstance(a, ast.Constant))
            out.append(Finding(rel, node.lineno, "time", f"time({hm})"))
    return out


# --- TypeScript ---------------------------------------------------------------------------------------------------------

_WORDS = "|".join(sorted((re.escape(w) for w in ORDER_WORDS), key=len, reverse=True))
_TS_LITERAL = rf"""(-?\d[\d_]*(?:\.\d+)?|["'`](?:{_WORDS}|(?:[01]?\d|2[0-3]):[0-5]\d)["'`])"""
_TS_NAME = r"[A-Za-z_$][\w$]*"
_TS_ASSIGN = re.compile(
    rf"""(?:^|[\s,{{(])["']?({_TS_NAME})["']?\s*(?:\??:\s*[\w<>\[\]|."' ]+?\s*)?[:=]\s*{_TS_LITERAL}\s*(?=[,;)}}\]]|$|\?\?|//)""")
_TS_STATE = re.compile(rf"""const\s*\[\s*({_TS_NAME})\s*,\s*[\w$]+\s*\]\s*=\s*useState(?:<[^>]*>)?\(\s*{_TS_LITERAL}\s*\)""")
_TS_SKIP_FILE = re.compile(r"\.(test|spec|stories|fixture)\.tsx?$")


def _ts_value(raw: str) -> Optional[str]:
    if raw[0] in "\"'`":
        return raw[1:-1]
    number = float(raw.replace("_", ""))
    if "." in raw:                                   # as in Python: only 0.0 is "none" for a float (1.0 is a 1 % band)
        return None if number == 0 else raw
    return None if number in (0, 1, -1) else raw


def _strip_ts_comment(line: str) -> str:
    """The code of a line without a trailing // comment (a // inside a string, as in a URL, is kept)."""
    out, quote = [], ""
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == "\\":
                out.append(line[i:i + 2])
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'`":
            quote = ch
        elif line.startswith("//", i):
            break
        out.append(ch)
        i += 1
    return "".join(out)


def scan_typescript(rel: str, text: str) -> List[Finding]:
    out: List[Finding] = []
    in_block = False
    for number, line in enumerate(text.splitlines(), 1):
        code = line
        if in_block:
            if "*/" not in code:
                continue
            code, in_block = code.split("*/", 1)[1], False
        while "/*" in code:
            head, _, rest = code.partition("/*")
            if "*/" in rest:
                code = head + rest.split("*/", 1)[1]
            else:
                code, in_block = head, True
        code = _strip_ts_comment(code)
        seen: set = set()
        for rx in (_TS_STATE, _TS_ASSIGN):
            for m in rx.finditer(code):
                name, value = m.group(1), _ts_value(m.group(2))
                if value is None or not trading_name(name) or (name, value) in seen:
                    continue
                seen.add((name, value))
                out.append(Finding(rel, number, name, value))
    return out


# --- the check ----------------------------------------------------------------------------------------------------------

_SKIP_DIRS = {"__pycache__", "node_modules", "dist", "build", "coverage"}


def _files(root: Path, scope: Iterable[str], suffixes: Tuple[str, ...]) -> Iterator[Tuple[str, Path]]:
    for entry in scope:
        base = root / entry
        if base.is_file():
            yield entry, base
            continue
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file() and path.suffix in suffixes and not (_SKIP_DIRS & set(path.relative_to(root).parts)):
                yield path.relative_to(root).as_posix(), path


def scan(root: Path) -> List[Finding]:
    found: List[Finding] = []
    for rel, path in _files(root, SCOPE_PY, (".py",)):
        found.extend(scan_python(rel, path.read_text(encoding="utf-8")))
    for rel, path in _files(root, SCOPE_TS, (".ts", ".tsx")):
        if not _TS_SKIP_FILE.search(path.name):
            found.extend(scan_typescript(rel, path.read_text(encoding="utf-8")))
    return sorted(found, key=lambda f: (f.path, f.line, f.name, f.value))


def check(root: Path, allowed: Optional[Dict[Tuple[str, str, str], str]] = None) -> List[str]:
    """The problems: every trading literal not on the allow-list, and every allow-list entry that matches nothing."""
    allowed = ALLOWED if allowed is None else allowed
    problems: List[str] = []
    used: set = set()
    for f in scan(root):
        if f.key in allowed:
            used.add(f.key)
            continue
        problems.append(f"{f}: a trading value in code - make it a user setting in the Trade settings registry "
                        f"({REGISTRY}), or, if the exchange / broker enforces it, add it to ALLOWED with its reason")
    for key in sorted(set(allowed) - used):
        problems.append(f"{key[0]}: allow-list entry {key[1]} = {key[2]} matches nothing any more - remove it from ALLOWED")
    return problems


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--list", action="store_true", help="print every trading literal found, allowed or not")
    args = parser.parse_args(list(argv) if argv is not None else None)
    root = args.root.resolve()
    if args.list:
        for f in scan(root):
            print(f)
        return 0
    problems = check(root)
    for p in problems:
        print(p)
    if problems:
        print(f"check_trading_literals: {len(problems)} finding(s) (M68 TS-7)")
        return 1
    print("check_trading_literals: clean (M68 TS-7)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
