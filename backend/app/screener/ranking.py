"""N3 S7a (spec §2.5): ranking systems - Portfolio123-style weighted factor trees that turn a universe into a 0-100 rank
(flag `screener_rankings`, off by default).

A tree is a list of nodes. Each node is one of:
- a **leaf**: `{"name", "factor": "<ScreenQL number of one symbol's daily bars>", "weight", "higher_is_better",
  "neutral": null | "sector"}`;
- a **group**: `{"name", "weight", "children": [...]}`.
Weights are percentages that add up to 100 at every level. Depth is at most 3 and there are at most 30 leaves.

Scoring at one moment, over one universe:
1. Each leaf's factor value for every symbol, read on that symbol's bars (the last closed bar, or the bar of the date
   in a history run).
2. A leaf's score is the value's percentile across the universe (0-100, ties averaged), reversed when lower is better,
   and taken within the sector when the leaf is sector-neutral (symbols without a sector form their own group).
3. A group's score is the weighted mean of its children's scores. A symbol missing a child (no value: too little
   history, a gap) is scored on the children it has, re-weighted (provisional S7-2). Missing them all, it has no score.
4. The rank is the composite's percentile across the symbols that have one (0-100; 100 = best).

Every symbol's components (each leaf's value and score, each group's score) come with its rank: no black box.

Use it as a column or sort key (`POST /api/screener/rankings/run`, the S6b backtest's `rank_system`), or as a filter in a
screen with `RankScore("<name>") > 80`. The runtime reads the systems of the current run from `ACTIVE`; the routes set
it after loading them, so the runtime stays free of the database.
"""
from __future__ import annotations

import contextvars
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

FLAG = "screener_rankings"
MAX_DEPTH = 3
MAX_LEAVES = 30
NEUTRALS = (None, "sector")
NO_SECTOR = "__none__"


class RankingError(ValueError):
    """A ranking system the platform does not accept (shown as is)."""


@dataclass
class Node:
    name: str
    weight: float
    factor: Optional[str] = None
    higher_is_better: bool = True
    neutral: Optional[str] = None
    children: List["Node"] = field(default_factory=list)
    ast: Any = None                    # the compiled factor (a leaf)
    validated: Any = None              # its plan (lookback)
    lookback: int = 1                  # daily bars the factor needs

    @property
    def leaf(self) -> bool:
        return self.factor is not None

    def as_dict(self) -> Dict[str, Any]:
        if self.leaf:
            return {"name": self.name, "weight": self.weight, "factor": self.factor, "higher_is_better": self.higher_is_better,
                    "neutral": self.neutral}
        return {"name": self.name, "weight": self.weight, "children": [c.as_dict() for c in self.children]}


@dataclass
class System:
    name: str
    nodes: List[Node]
    description: str = ""

    def leaves(self) -> List[Node]:
        out: List[Node] = []

        def walk(ns: Sequence[Node]) -> None:
            for x in ns:
                if x.leaf:
                    out.append(x)
                else:
                    walk(x.children)
        walk(self.nodes)
        return out

    @property
    def lookback(self) -> int:
        return max((leaf.lookback for leaf in self.leaves()), default=1)

    @property
    def sector_neutral(self) -> bool:
        return any(leaf.neutral == "sector" for leaf in self.leaves())

    def tree(self) -> Dict[str, Any]:
        return {"nodes": [x.as_dict() for x in self.nodes]}


# --- parsing and validation -------------------------------------------------------------------------------------------------

def _number(raw: Any, what: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not np.isfinite(raw):
        raise RankingError(f"{what} must be a number")
    return float(raw)


def _node(raw: Any, depth: int, path: str) -> Node:
    if not isinstance(raw, Mapping):
        raise RankingError(f"{path}: a node is an object")
    name = str(raw.get("name") or "").strip()
    if not name or len(name) > 60:
        raise RankingError(f"{path}: a node needs a name (at most 60 characters)")
    weight = _number(raw.get("weight"), f"{path} ({name}): weight")
    if not 0 < weight <= 100:
        raise RankingError(f"{path} ({name}): weight is a percentage above 0 and at most 100")
    factor, children = raw.get("factor"), raw.get("children")
    if (factor is None) == (children is None):
        raise RankingError(f"{path} ({name}): a node has a factor (a leaf) or children (a group), not both")
    if factor is not None:
        neutral = raw.get("neutral")
        if neutral not in NEUTRALS:
            raise RankingError(f"{path} ({name}): neutral is null or \"sector\"")
        hib = raw.get("higher_is_better", True)
        if not isinstance(hib, bool):
            raise RankingError(f"{path} ({name}): higher_is_better is true or false")
        if not isinstance(factor, str) or not factor.strip() or len(factor) > 500:
            raise RankingError(f"{path} ({name}): the factor is a ScreenQL expression (at most 500 characters)")
        return Node(name=name, weight=weight, factor=factor.strip(), higher_is_better=hib, neutral=neutral)
    if depth >= MAX_DEPTH:
        raise RankingError(f"{path} ({name}): at most {MAX_DEPTH} levels")
    return Node(name=name, weight=weight, children=_level(children, depth + 1, f"{path}/{name}"))


def _level(raw: Any, depth: int, path: str) -> List[Node]:
    if not isinstance(raw, list) or not raw:
        raise RankingError(f"{path}: a level is a non-empty list of nodes")
    nodes = [_node(x, depth, f"{path}[{i}]") for i, x in enumerate(raw)]
    total = sum(x.weight for x in nodes)
    if abs(total - 100.0) > 1e-6:
        raise RankingError(f"{path}: the weights of a level add up to 100 (they add up to {total:g})")
    names = [x.name for x in nodes]
    if len(set(names)) != len(names):
        raise RankingError(f"{path}: node names repeat within a level")
    return nodes


def parse(name: str, tree: Mapping[str, Any], description: str = "") -> System:
    """A tree -> a System with every leaf's factor compiled (a daily number of one symbol's bars; no Rank /
    PercentileRank / RankScore inside, no other timeframe). Raises RankingError with the reason."""
    from app.screener import compile_screen
    from app.screener.registry import NUM
    from app.screener.validator import NOT_A_CONDITION, kind_of
    clean = str(name or "").strip()
    if not clean or len(clean) > 60:
        raise RankingError("a ranking system needs a name (at most 60 characters)")
    if not isinstance(tree, Mapping):
        raise RankingError("the tree is an object with a list of nodes")
    system = System(name=clean, nodes=_level(tree.get("nodes"), 1, "nodes"), description=str(description or "")[:300])
    leaves = system.leaves()
    if len(leaves) > MAX_LEAVES:
        raise RankingError(f"at most {MAX_LEAVES} factors")
    for leaf in leaves:
        ast, validated = compile_screen(str(leaf.factor), base_tf="1d")
        problems = [p.message for p in validated.problems if p.message != NOT_A_CONDITION]
        if ast is None or problems:
            raise RankingError(f"{leaf.name}: {'; '.join(problems) or 'not a ScreenQL expression'}")
        if kind_of(ast, base_tf="1d") != NUM:
            raise RankingError(f"{leaf.name}: a factor is a number (not a condition or a category)")
        if validated.cross_sectional:
            raise RankingError(f"{leaf.name}: a factor is one symbol's number (no Rank / PercentileRank / RankScore inside)")
        if any(tf != "1d" for tf in (validated.lookback or {})):
            raise RankingError(f"{leaf.name}: a factor reads daily bars only")
        leaf.ast, leaf.validated = ast, validated
        leaf.lookback = int((validated.lookback or {}).get("1d", 1) or 1)
    return system


# --- scoring (pure) ---------------------------------------------------------------------------------------------------------

def percentile(values: pd.Series, groups: Optional[pd.Series] = None, higher_is_better: bool = True) -> pd.Series:
    """0-100 percentile of each value (ties averaged; NaN stays NaN), within each group when given."""
    x = values.astype(float) if higher_is_better else -values.astype(float)
    if groups is None:
        return x.rank(pct=True, method="average") * 100.0
    key = groups.reindex(x.index).fillna(NO_SECTOR)
    return x.groupby(key).rank(pct=True, method="average") * 100.0


def score(system: System, values: Dict[str, pd.Series], sectors: Optional[Mapping[str, Optional[str]]] = None
          ) -> Tuple[pd.Series, Dict[str, pd.Series]]:
    """(rank 0-100 per symbol, every node's score per symbol keyed by its path). `values`: each leaf's raw factor values
    keyed by the leaf's path, indexed by symbol."""
    symbols = sorted({s for v in values.values() for s in v.index})
    index = pd.Index(symbols)
    groups = pd.Series({s: (sectors or {}).get(s) for s in symbols}, dtype=object) if sectors is not None else None
    parts: Dict[str, pd.Series] = {}

    def node_score(node: Node, path: str) -> pd.Series:
        if node.leaf:
            raw = values.get(path, pd.Series(dtype=float)).reindex(index).astype(float)
            s = percentile(raw, groups if node.neutral == "sector" else None, node.higher_is_better)
        else:
            s = level_score(node.children, path)
        parts[path] = s
        return s

    def level_score(nodes: Sequence[Node], prefix: str) -> pd.Series:
        num = pd.Series(0.0, index=index)
        den = pd.Series(0.0, index=index)
        for x in nodes:
            s = node_score(x, f"{prefix}/{x.name}")
            ok = s.notna()
            num[ok] += s[ok] * x.weight
            den[ok] += x.weight
        out = num / den
        out[den == 0] = np.nan
        return out
    composite = level_score(system.nodes, "")
    parts["composite"] = composite
    rank = percentile(composite)
    return rank, parts


def leaf_paths(system: System) -> List[Tuple[str, Node]]:
    out: List[Tuple[str, Node]] = []

    def walk(nodes: Sequence[Node], prefix: str) -> None:
        for x in nodes:
            path = f"{prefix}/{x.name}"
            if x.leaf:
                out.append((path, x))
            else:
                walk(x.children, path)
    walk(system.nodes, "")
    return out


def components(system: System, values: Dict[str, pd.Series], parts: Dict[str, pd.Series], symbol: str) -> List[Dict[str, Any]]:
    """One symbol's explanation: every node's value (a leaf) and score."""
    out: List[Dict[str, Any]] = []

    def num(v: Any) -> Optional[float]:
        return None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), 4)

    def walk(nodes: Sequence[Node], prefix: str, depth: int) -> None:
        for x in nodes:
            path = f"{prefix}/{x.name}"
            row: Dict[str, Any] = {"path": path, "name": x.name, "depth": depth, "weight": x.weight,
                                   "score": num(parts.get(path, pd.Series(dtype=float)).get(symbol))}
            if x.leaf:
                row.update(factor=x.factor, value=num(values.get(path, pd.Series(dtype=float)).get(symbol)),
                           higher_is_better=x.higher_is_better, neutral=x.neutral)
            out.append(row)
            if not x.leaf:
                walk(x.children, path, depth + 1)
    walk(system.nodes, "", 1)
    return out


def table(system: System, values: Dict[str, pd.DataFrame], dates: Sequence[Any], sectors: Optional[Mapping[Any, Mapping[str, Optional[str]]]] = None,
          eligible: Optional[Dict[Any, set]] = None) -> pd.DataFrame:
    """[dates x symbols] the rank at each date from each leaf's [dates x symbols] values (NaN where none). Each date is
    scored on its own cross-section: the symbols with a value that date (and eligible then, for an index universe),
    grouped by their sector AS OF that date when a leaf is sector-neutral."""
    symbols = sorted({s for v in values.values() for s in v.columns})
    out = pd.DataFrame(np.nan, index=pd.Index(list(dates)), columns=symbols, dtype=float)
    for d in dates:
        keep = [s for s in symbols if eligible is None or s in eligible.get(d, set())]
        row = {path: v.loc[d, [s for s in keep if s in v.columns]].astype(float) for path, v in values.items()}
        if not keep:
            continue
        rank, _ = score(system, row, (sectors or {}).get(d, {}) if system.sector_neutral else None)
        for sym, r in rank.items():
            out.at[d, sym] = r
    return out


def rank_at_dates(system: System, frames: Dict[str, pd.DataFrame], dates: Sequence[Any],
                  sectors: Optional[Mapping[Any, Mapping[str, Optional[str]]]] = None, eligible: Optional[Dict[Any, set]] = None) -> pd.DataFrame:
    """The S6 history read of a system: each leaf's causal value at each date on bars closed by then (S6a
    `series_at_dates`, NaN before its lookback), scored date by date. Equal to running the system on bars cut at each
    date (tested), so a backtest ranking on it never sees a later bar."""
    from app.screener import history as H
    values = {path: H.series_at_dates(leaf.ast, leaf.validated, frames, dates).apply(pd.to_numeric, errors="coerce").astype(float)
              for path, leaf in leaf_paths(system)}
    return table(system, values, dates, sectors, eligible)


def sector_on(ranges: Sequence[Tuple[Any, Optional[Any], Optional[str]]], day: Any) -> Optional[str]:
    """The sector of the (valid_from, valid_to, sector) range alive on `day` (valid_to exclusive; None = still open)."""
    for start, end, sector in ranges:
        if start <= day and (end is None or end > day):
            return sector
    return None


def live_values(system: System, universe: Sequence[Any], base_tf: str = "1d", params: Optional[Dict[str, Any]] = None
                ) -> Dict[str, pd.Series]:
    """Each leaf's value per symbol on its LAST bar (the bars passed: closed bars only). NaN for a symbol with fewer bars
    than the leaf's lookback - the S6 history read does the same, so a live run and a history date agree."""
    from app.screener.runtime import ScreenRuntimeError, _Eval, _last
    paths = leaf_paths(system)
    values: Dict[str, Dict[str, float]] = {path: {} for path, _ in paths}
    for d in universe:
        frame = d.frames.get(base_tf)
        ev = _Eval(d, base_tf, dict(params or {}), {}) if frame is not None and not frame.empty else None
        for path, leaf in paths:
            v = float("nan")
            if ev is not None and frame is not None and len(frame) >= leaf.lookback:
                try:
                    v = float(_last(ev.ev(leaf.ast, base_tf)))
                except (ScreenRuntimeError, TypeError, ValueError):
                    v = float("nan")
            values[path][d.symbol] = v
    return {p: pd.Series(v, dtype=float) for p, v in values.items()}


# --- platform systems (documented formulas, read-only) ------------------------------------------------------------------------

PLATFORM: Dict[str, Dict[str, Any]] = {
    "Momentum": {"description": "Price momentum: 12-month and 6-month returns, nearness to the 52-week high, and the 3-month trend.",
                 "tree": {"nodes": [
                     {"name": "12m return", "factor": "PctChange(close, 250)", "weight": 35},
                     {"name": "6m return", "factor": "PctChange(close, 125)", "weight": 25},
                     {"name": "Near the 52w high", "factor": "close / Max(high, 250)", "weight": 25},
                     {"name": "Above the 63-day average", "factor": "close / SMA(close, 63)", "weight": 15}]}},
    "Low volatility": {"description": "Calm stocks: a low average daily range and a narrow 3-month range, both as a share of price and lower-is-better.",
                       "tree": {"nodes": [
                           {"name": "ATR % of price", "factor": "ATR(14) / close", "weight": 50, "higher_is_better": False},
                           {"name": "3-month range % of price", "factor": "(Max(high, 63) - Min(low, 63)) / close", "weight": 50,
                            "higher_is_better": False}]}},
    "Trend quality": {"description": "A trend with strength: ADX, the distance above the 200-day average, and a positive 50-day slope.",
                      "tree": {"nodes": [
                          {"name": "ADX", "factor": "ADX(14)", "weight": 40},
                          {"name": "Above the 200-day average", "factor": "close / SMA(close, 200)", "weight": 30},
                          {"name": "50-day slope", "factor": "PctChange(SMA(close, 50), 20)", "weight": 30}]}},
}
PLATFORM_PREFIX = "platform:"


def platform_system(name: str) -> System:
    spec = PLATFORM[name]
    return parse(f"{PLATFORM_PREFIX}{name}", spec["tree"], spec["description"])


# --- the systems of the current run (read by the runtime's RankScore) -----------------------------------------------------

ACTIVE: contextvars.ContextVar[Dict[str, System]] = contextvars.ContextVar("screener_ranking_systems", default={})


def system_name(arg: Any, params: Optional[Mapping[str, Any]] = None) -> Optional[str]:
    """The system a RankScore argument names: a string literal, or a $parameter holding one."""
    from app.screener import nodes as n
    if isinstance(arg, n.Str):
        return arg.value
    if isinstance(arg, n.Param) and isinstance((params or {}).get(arg.name), str):
        return str((params or {})[arg.name])
    return None


def names_in(ast: Any, params: Optional[Mapping[str, Any]] = None) -> List[str]:
    """The ranking systems a screen names through RankScore(...) (each once, in order)."""
    from app.screener import nodes as n
    out: List[str] = []
    if ast is None:
        return out
    for node in n.walk(ast):
        if isinstance(node, n.Call) and node.name == "RankScore" and node.args:
            name = system_name(node.args[0], params)
            if name is not None and name not in out:
                out.append(name)
    return out


def uses_rankscore(ast: Any) -> bool:
    from app.screener import nodes as n
    return ast is not None and any(isinstance(x, n.Call) and x.name == "RankScore" for x in n.walk(ast))


# --- the gate and the store (the routes; the runtime above never reads the database) -----------------------------------------

def refusal() -> str:
    return f"RankScore(): ranking systems are not enabled for this organisation (flag {FLAG})"


async def enabled(session: Any, tenant_id: Any) -> bool:
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, FLAG, tenant_id)


async def resolve(session: Any, tenant_id: int, names: Sequence[str]) -> Tuple[Dict[str, System], List[str]]:
    """(the systems by name, problems). A platform system ("platform:Momentum") or this organisation's own saved,
    unarchived one - never another organisation's."""
    from sqlalchemy import select

    from app.db.models import RankingSystemRecord
    systems: Dict[str, System] = {}
    problems: List[str] = []
    for name in names:
        if name.startswith(PLATFORM_PREFIX):
            key = name[len(PLATFORM_PREFIX):]
            if key in PLATFORM:
                systems[name] = platform_system(key)
            else:
                problems.append(f"RankScore: no platform ranking system {key!r} (one of {', '.join(PLATFORM)})")
            continue
        row = await session.scalar(select(RankingSystemRecord).where(RankingSystemRecord.tenant_id == tenant_id,
                                                                     RankingSystemRecord.name == name,
                                                                     RankingSystemRecord.archived.is_(False)))
        if row is None:
            problems.append(f"RankScore: no ranking system named {name!r} in this organisation")
            continue
        try:
            systems[name] = parse(row.name, json.loads(row.tree_json or "{}"), row.description)
        except (RankingError, ValueError) as exc:
            problems.append(f"RankScore: the ranking system {name!r} no longer validates ({exc})")
    return systems, problems


def within(systems: Dict[str, System], fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Calls fn with `ACTIVE` set to these systems (for a worker thread: asyncio.to_thread(within, systems, run_screen, ...))."""
    token = ACTIVE.set(systems)
    try:
        return fn(*args, **kwargs)
    finally:
        ACTIVE.reset(token)


async def sector_ranges(session: Any, symbols: Sequence[str], start: Any, end: Any) -> Dict[str, List[Tuple[Any, Optional[Any], Optional[str]]]]:
    """Each symbol's NSE sector ranges overlapping [start, end] (U1-b classifications, by the ISIN the symbol had at `end`;
    a symbol label that is itself an ISIN is used as is). Read once; `sector_on` answers each date - the sector AS OF
    that date, never a later reclassification."""
    from sqlalchemy import and_, or_, select

    from app.db.models import ClassificationRecord
    from app.universe.asof import isin_for
    isins: Dict[str, str] = {}
    for sym in symbols:
        isin = await isin_for(session, sym, end)
        if isin is None and len(sym) == 12 and sym[:2].isalpha():
            isin = sym
        if isin is not None:
            isins[sym] = isin
    out: Dict[str, List[Tuple[Any, Optional[Any], Optional[str]]]] = {s: [] for s in symbols}
    if not isins:
        return out
    rows = await session.scalars(select(ClassificationRecord).where(
        ClassificationRecord.isin.in_(sorted(set(isins.values()))), ClassificationRecord.scheme == "NSE",
        and_(ClassificationRecord.valid_from <= end, or_(ClassificationRecord.valid_to.is_(None), ClassificationRecord.valid_to > start))))
    by_isin: Dict[str, List[Tuple[Any, Optional[Any], Optional[str]]]] = {}
    for r in rows:
        by_isin.setdefault(r.isin, []).append((r.valid_from, r.valid_to, r.sector))
    for sym, isin in isins.items():
        out[sym] = sorted(by_isin.get(isin, []), key=lambda x: x[0])
    return out
