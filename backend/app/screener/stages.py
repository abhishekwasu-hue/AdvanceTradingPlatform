"""U2 remaining blocks (SCREENER_SPEC §3 builder, SC-16; Phase 0 scope: the blocks the shipped screens use): a screen's
text -> the funnel's stages, so a pre-built Legends or breakout screen - offsets (`[1]`), arithmetic
(`0.9 * Max(high, 250)`), a range of a bar (`high - low`) - opens in the builder. Behind `screener_builder_u2` (off).

`split()` returns the top-level conjuncts the run path already counts as funnel stages (`runtime.stage_survivors`), each
as its canonical text and its AST (JSON). The builder maps a conjunct onto one of its block forms when the block writes
back exactly the same text, and keeps every other conjunct as a **formula** block - ScreenQL text edited in place and
checked by `/validate` like the rest of the screen. Parameters are inlined (a formula block holds literal values).
Nothing runs here: parse, validate, split.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.screener import nodes as n

FLAG = "screener_builder_u2"
AND_PREC = n._PREC["AND"] + 1          # how a conjunct prints inside a top-level AND (parenthesised when it is an OR / AND)


def inline(ast: n.Node, params: Dict[str, Any]) -> n.Node:
    """The screen with its $parameters replaced by their values."""
    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if node.get("k") == "Param" and node.get("name") in params:
                value = params[node["name"]]
                if isinstance(value, bool):
                    return {"k": "Bool", "v": value}
                if isinstance(value, str):
                    return {"k": "Str", "v": value}
                return {"k": "Num", "v": float(value)}
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node
    return n.from_json(walk(n.to_json(ast)))


def split(ast: n.Node) -> List[Dict[str, Any]]:
    """The top-level conjuncts: {text (canonical), wrapped (as it prints inside the AND), ast (JSON)}."""
    items = list(ast.items) if isinstance(ast, n.Logic) and ast.op == "ALL" else [ast]
    return [{"text": n.to_text(i), "wrapped": n._wrap(i, AND_PREC), "ast": n.to_json(i)} for i in items]


def library_source(screen_id: str) -> Tuple[Optional[Tuple[str, str, Dict[str, Any], str]], Optional[str]]:
    """A pre-built Legends or breakout screen -> ((text, base_tf, params, name), None) or (None, why)."""
    if screen_id.startswith("legends."):
        from app.screener import legends as lg
        card = lg.load().card(screen_id)
        if card is None:
            return None, "no such Legends card"
        return (card.source, card.base_tf, card.defaults(), card.name), None
    if screen_id.startswith("breakouts."):
        from app.screener.breakouts.templates import BY_ID, screenql, template_params
        template = BY_ID.get(screen_id.split(".", 1)[1])
        if template is None:
            return None, "no such breakout template"
        text, why = screenql(template.screen, template.signal_tf, template_params(template))
        if text is None:
            return None, why
        return (text, template.signal_tf, {}, template.name), None
    return None, "only Legends and breakout screens are opened from the server (the starters are built in the page)"


__all__ = ["FLAG", "inline", "split", "library_source"]
