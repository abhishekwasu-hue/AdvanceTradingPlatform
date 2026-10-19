"""Host-side SQL validation (sqlglot) - the worker additionally enforces a SQLite authorizer, step and row limits.

Allowed: exactly ONE SELECT (CTEs allowed) reading only the approved views `bars` and `latest`;
functions from an allowlist; named parameters (:name) bound from strategy parameters.
Blocked: DDL/DML/PRAGMA/ATTACH/transactions, multiple statements, other tables, unknown functions.
"""
from __future__ import annotations

import sqlglot
from sqlglot import exp

APPROVED_VIEWS = {"bars", "latest"}
FUNCS = {"abs", "min", "max", "avg", "sum", "count", "round", "coalesce", "ifnull", "nullif", "lag", "lead", "row_number",
         "first_value", "last_value", "upper", "lower", "length", "substr", "substring", "iif", "if"}


def check_sql(sql: str) -> list[str]:
    try:
        stmts = [s for s in sqlglot.parse(sql, read="sqlite") if s is not None]
    except sqlglot.errors.ParseError as e:
        return [f"SQL parse error: {str(e).splitlines()[0][:200]}"]
    if len(stmts) != 1:
        return [f"exactly one statement allowed (got {len(stmts)})"]
    st = stmts[0]
    errs = []
    if not isinstance(st, (exp.Select, exp.Union, exp.With)) and not (isinstance(st, exp.Subquery)):
        return [f"only SELECT is allowed (got {st.key.upper()})"]
    for bad in (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Alter, exp.Command, exp.Pragma, exp.Attach,
                exp.Transaction):
        if st.find(bad):
            errs.append(f"{bad.__name__.upper()} is not allowed")
    ctes = {c.alias_or_name.lower() for c in st.find_all(exp.CTE)}
    for t in st.find_all(exp.Table):
        name = t.name.lower()
        if name not in APPROVED_VIEWS and name not in ctes:
            errs.append(f"table '{t.name}' is not an approved view {sorted(APPROVED_VIEWS)}")
        if t.args.get("db"):
            errs.append("schema-qualified tables are not allowed")
    operators = tuple(c for c in (getattr(exp, n, None) for n in ("Binary", "Connector", "Unary", "Between", "In", "Is", "Like",
                                                                  "Paren", "Case", "Predicate")) if c is not None)
    for f in st.find_all(exp.Func):
        if isinstance(f, operators):
            continue
        name = (f.sql_name() if not isinstance(f, exp.Anonymous) else f.name).lower()
        if isinstance(f, (exp.Count, exp.Avg, exp.Sum, exp.Min, exp.Max, exp.Abs, exp.Round, exp.Coalesce, exp.Nullif,
                          exp.Lag, exp.Lead, exp.RowNumber, exp.FirstValue, exp.LastValue, exp.Upper, exp.Lower,
                          exp.Length, exp.Substring, exp.If, exp.Window, exp.Cast)):
            continue
        if name not in FUNCS:
            errs.append(f"function '{name}' is not allowed")
    if "symbol" not in {s.alias_or_name.lower() for s in (st.selects if hasattr(st, "selects") else [])} and "*" not in st.sql():
        errs.append("the query must select a 'symbol' column")
    return errs


TEMPLATES = {
    "Trend filter": "SELECT symbol FROM latest\nWHERE close > ema20 AND ema20 > ema50 AND ema50 > ema200 AND adx >= :min_adx",
    "Momentum (RSI band)": "SELECT symbol, 'long' AS bias FROM latest\nWHERE rsi14 BETWEEN :rsi_lo AND :rsi_hi AND macd_hist > 0",
    "Volume expansion": "SELECT symbol, 'RVOL ' || ROUND(rvol, 2) AS note FROM latest\nWHERE rvol >= :min_rvol AND close > open",
    "Price level (20-bar high breakout)": (
        "WITH hi AS (SELECT MAX(high) AS h FROM bars\n"
        "            WHERE bar_index BETWEEN (SELECT MAX(bar_index) FROM bars) - 20 AND (SELECT MAX(bar_index) FROM bars) - 1)\n"
        "SELECT l.symbol, 'long' AS bias FROM latest l, hi WHERE l.close > hi.h"),
}
