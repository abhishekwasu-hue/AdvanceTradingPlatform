"""S1a (ADR-0021 §1): the ScreenQL `screenql/1` parser - a hand-written recursive descent over a small token set.
Nothing in the text is ever executed; the parser only builds `nodes`, and only the validator decides what may run.

Grammar (keywords are case-insensitive; field and function names are case-sensitive registry names):

    screen     := or_expr EOF
    or_expr    := and_expr ("OR" and_expr)*
    and_expr   := not_expr ("AND" not_expr)*
    not_expr   := "NOT" not_expr | comparison
    comparison := additive [ CMP additive | "BETWEEN" additive "AND" additive | "IN" "(" literal ("," literal)* ")" ]
    additive   := term (("+" | "-") term)*
    term       := unary (("*" | "/") unary)*
    unary      := "-" unary | postfix                      ("-" NUMBER is the literal itself)
    postfix    := primary ["[" INT "]"] ["@" TIMEFRAME]    (offset/timeframe only on fields and calls)
    primary    := NUMBER | STRING | "TRUE" | "FALSE" | "$" NAME | NAME "(" [arg ("," arg)*] ")" | NAME | "(" or_expr ")"
    arg        := NAME "=" or_expr | or_expr

`ALL(a, b, ...)` / `ANY(a, b, ...)` are the builder's group forms of AND / OR and parse to the same `Logic` node.
Errors are `ScreenQLSyntaxError` with the character position; any other exception from `parse` is a bug (fuzz-tested).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from app.screener import nodes as n

MAX_TEXT = 4000
MAX_DEPTH = 60
KEYWORDS = {"AND", "OR", "NOT", "BETWEEN", "IN", "TRUE", "FALSE"}
_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<num>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
  | (?P<str>"(?:[^"\\]|\\.)*")
  | (?P<param>\$[A-Za-z_][A-Za-z0-9_]*)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<tf>@(?:1m|3m|5m|15m|30m|1h|1d|1w|1M)(?![A-Za-z0-9_]))
  | (?P<op>>=|<=|==|!=|[><+\-*/(),\[\]=])
""", re.X)


class ScreenQLSyntaxError(ValueError):
    def __init__(self, message: str, pos: int) -> None:
        super().__init__(f"{message} (at character {pos})")
        self.message, self.pos = message, pos


@dataclass
class Tok:
    kind: str           # num / str / param / name / kw / tf / op / eof
    text: str
    pos: int


def tokenize(text: str) -> List[Tok]:
    if len(text) > MAX_TEXT:
        raise ScreenQLSyntaxError(f"screen longer than {MAX_TEXT} characters", MAX_TEXT)
    out: List[Tok] = []
    i = 0
    while i < len(text):
        m = _TOKEN.match(text, i)
        if not m:
            if text[i] == "@":
                raise ScreenQLSyntaxError(f"unknown timeframe; use one of {', '.join('@' + t for t in n.TIMEFRAMES)}", i)
            raise ScreenQLSyntaxError(f"unexpected character {text[i]!r}", i)
        kind = m.lastgroup or ""
        if kind != "ws":
            value = m.group()
            if kind == "name" and value.upper() in KEYWORDS:
                kind, value = "kw", value.upper()
            out.append(Tok(kind, value, i))
        i = m.end()
    out.append(Tok("eof", "", len(text)))
    return out


class _Parser:
    def __init__(self, text: str) -> None:
        self.toks = tokenize(text)
        self.i = 0
        self.depth = 0

    @property
    def tok(self) -> Tok:
        return self.toks[self.i]

    def _next(self) -> Tok:
        t = self.toks[self.i]
        self.i += 1
        return t

    def _is(self, kind: str, text: Optional[str] = None) -> bool:
        return self.tok.kind == kind and (text is None or self.tok.text == text)

    def _expect(self, kind: str, text: Optional[str] = None, what: Optional[str] = None) -> Tok:
        if not self._is(kind, text):
            found = self.tok.text or "end of screen"
            raise ScreenQLSyntaxError(f"expected {what or text or kind}, found {found!r}", self.tok.pos)
        return self._next()

    def _enter(self) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise ScreenQLSyntaxError("screen nested too deeply", self.tok.pos)

    def screen(self) -> n.Node:
        node = self.or_expr()
        if not self._is("eof"):
            raise ScreenQLSyntaxError(f"unexpected {self.tok.text!r}", self.tok.pos)
        return node

    def or_expr(self) -> n.Node:
        self._enter()
        pos = self.tok.pos
        items = [self.and_expr()]
        while self._is("kw", "OR"):
            self._next()
            items.append(self.and_expr())
        self.depth -= 1
        return items[0] if len(items) == 1 else n.Logic("ANY", items, pos=pos)

    def and_expr(self) -> n.Node:
        pos = self.tok.pos
        items = [self.not_expr()]
        while self._is("kw", "AND"):
            self._next()
            items.append(self.not_expr())
        return items[0] if len(items) == 1 else n.Logic("ALL", items, pos=pos)

    def not_expr(self) -> n.Node:
        if self._is("kw", "NOT"):
            pos = self._next().pos
            self._enter()
            item = self.not_expr()
            self.depth -= 1
            return n.Not(item, pos=pos)
        return self.comparison()

    def comparison(self) -> n.Node:
        pos = self.tok.pos
        left = self.additive()
        if self.tok.kind == "op" and self.tok.text in n.COMPARE_OPS:
            op = self._next().text
            return n.Compare(op, left, self.additive(), pos=pos)
        if self._is("kw", "BETWEEN"):
            self._next()
            low = self.additive()
            self._expect("kw", "AND", "AND (in BETWEEN low AND high)")
            return n.Between(left, low, self.additive(), pos=pos)
        if self._is("kw", "IN"):
            self._next()
            self._expect("op", "(")
            items = [self.literal()]
            while self._is("op", ","):
                self._next()
                items.append(self.literal())
            self._expect("op", ")")
            return n.In(left, items, pos=pos)
        return left

    def literal(self) -> n.Node:
        t = self.tok
        if t.kind == "op" and t.text == "-" and self.toks[self.i + 1].kind == "num":
            self._next()
            return n.Num(-float(self._next().text), pos=t.pos)
        if t.kind in ("num", "str", "param") or (t.kind == "kw" and t.text in ("TRUE", "FALSE")):
            return self.primary()
        raise ScreenQLSyntaxError(f"IN takes a list of literals, found {t.text!r}", t.pos)

    def additive(self) -> n.Node:
        node = self.term()
        while self.tok.kind == "op" and self.tok.text in ("+", "-"):
            t = self._next()
            node = n.Binary(t.text, node, self.term(), pos=t.pos)
        return node

    def term(self) -> n.Node:
        node = self.unary()
        while self.tok.kind == "op" and self.tok.text in ("*", "/"):
            t = self._next()
            node = n.Binary(t.text, node, self.unary(), pos=t.pos)
        return node

    def unary(self) -> n.Node:
        if self._is("op", "-"):
            t = self._next()
            if self.tok.kind == "num":
                return n.Num(-float(self._next().text), pos=t.pos)
            self._enter()
            operand = self.unary()
            self.depth -= 1
            return n.Unary("-", operand, pos=t.pos)
        return self.postfix()

    def postfix(self) -> n.Node:
        node = self.primary()
        if isinstance(node, (n.Field, n.Call)):
            if self._is("op", "["):
                bracket = self._next()
                if self._is("op", "-"):
                    raise ScreenQLSyntaxError("a negative offset would read a future bar (look-ahead); offsets count bars back: [1], [2]...", bracket.pos)
                num = self._expect("num", what="a whole number of bars back")
                if not num.text.isdigit() or len(num.text) > 4:
                    raise ScreenQLSyntaxError("an offset is a whole number of bars back, at most 4 digits", num.pos)
                node.offset = int(num.text)
                self._expect("op", "]")
            if self._is("tf"):
                node.tf = self._next().text[1:]
        elif self._is("op", "[") or self._is("tf"):
            raise ScreenQLSyntaxError("offsets and timeframes apply to fields and functions only", self.tok.pos)
        return node

    def primary(self) -> n.Node:
        t = self.tok
        if t.kind == "num":
            self._next()
            return n.Num(float(t.text), pos=t.pos)
        if t.kind == "str":
            self._next()
            return n.Str(re.sub(r"\\(.)", r"\1", t.text[1:-1]), pos=t.pos)
        if t.kind == "kw" and t.text in ("TRUE", "FALSE"):
            self._next()
            return n.Bool(t.text == "TRUE", pos=t.pos)
        if t.kind == "param":
            self._next()
            return n.Param(t.text[1:], pos=t.pos)
        if t.kind == "name":
            self._next()
            if not self._is("op", "("):
                return n.Field(t.text, pos=t.pos)
            self._next()
            self._enter()
            args: List[n.Node] = []
            kwargs = {}
            if not self._is("op", ")"):
                while True:
                    if self.tok.kind == "name" and self.toks[self.i + 1].kind == "op" and self.toks[self.i + 1].text == "=":
                        key = self._next().text
                        self._next()
                        if key in kwargs:
                            raise ScreenQLSyntaxError(f"argument {key!r} given twice", t.pos)
                        kwargs[key] = self.or_expr()
                    else:
                        if kwargs:
                            raise ScreenQLSyntaxError("positional argument after a named one", self.tok.pos)
                        args.append(self.or_expr())
                    if not self._is("op", ","):
                        break
                    self._next()
            self._expect("op", ")")
            self.depth -= 1
            if t.text.upper() in ("ALL", "ANY") and not kwargs:
                if not args:
                    raise ScreenQLSyntaxError(f"{t.text.upper()}() needs at least one condition", t.pos)
                return args[0] if len(args) == 1 else n.Logic(t.text.upper(), args, pos=t.pos)
            return n.Call(t.text, args, kwargs, pos=t.pos)
        if t.kind == "op" and t.text == "(":
            self._next()
            node = self.or_expr()
            self._expect("op", ")")
            return node
        raise ScreenQLSyntaxError(f"expected a value, field or function, found {t.text or 'end of screen'!r}", t.pos)


def parse(text: str) -> n.Node:
    """ScreenQL text -> AST. Raises ScreenQLSyntaxError (with the position) and nothing else."""
    if not isinstance(text, str) or not text.strip():
        raise ScreenQLSyntaxError("empty screen", 0)
    return _Parser(text).screen()


__all__ = ["parse", "tokenize", "ScreenQLSyntaxError", "MAX_TEXT"]
