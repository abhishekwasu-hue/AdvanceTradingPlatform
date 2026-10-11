"""Screener (part S, ADR-0021). S1a: the ScreenQL language - `parser` (text -> AST), `nodes` (the AST, its JSON
form for the visual builder and the canonical text), `registry` (what a screen may use) and `validator` (types,
units, timeframes, look-ahead, cost). The runtime (S1b) and the scanner migration (S1c) build on these."""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple, Union

from app.screener import nodes
from app.screener.parser import ScreenQLSyntaxError, parse
from app.screener.validator import Problem, Validated, validate


def compile_screen(source: Union[str, Dict[str, Any]], *, base_tf: str = "1d", params: Optional[Dict[str, Any]] = None,
                   cost_cap: Optional[float] = None) -> Tuple[Optional[nodes.Node], Validated]:
    """Query text or a builder tree -> (AST, validation). A syntax error comes back as a failed validation with its
    position, so callers handle one shape."""
    try:
        ast = parse(source) if isinstance(source, str) else nodes.from_json(source)
    except ScreenQLSyntaxError as exc:
        return None, Validated(False, [Problem(exc.message, exc.pos)])
    except ValueError as exc:
        return None, Validated(False, [Problem(str(exc))])
    kwargs: Dict[str, Any] = {"base_tf": base_tf, "params": params}
    if cost_cap is not None:
        kwargs["cost_cap"] = cost_cap
    return ast, validate(ast, **kwargs)


__all__ = ["compile_screen", "parse", "validate", "nodes", "ScreenQLSyntaxError", "Validated", "Problem"]
