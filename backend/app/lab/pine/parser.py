"""AMW-20 / handover 11c-1: the Pine Script subset parser - recursive descent over `tokens.tokenize`, building a small
Pine syntax tree. Nothing is evaluated here or anywhere else in `app.lab.pine`: the translator walks this tree and
builds ScreenQL nodes from a fixed table.

Statements (one per logical line; a block body is indented by four spaces or a tab):

    statement := "if" expr NEWLINE block                     (the body may hold only signal calls and nested ifs)
               | [qualifier] [type] NAME "=" expr            (a declaration: `len = input.int(14)`, `float x = ...`)
               | "[" NAME ("," NAME)* "]" "=" expr           (a tuple: `[macd, signal, hist] = ta.macd(close, 12, 26, 9)`)
               | expr                                        (a call: `alertcondition(...)`, `plotshape(...)`, `strategy.entry(...)`)

Anything else Pine has (`var`, `:=`, `for`, `while`, `switch`, functions, `else`, imports, types, methods) is parsed only
far enough to report it, with its line, and its indented body is skipped.

Expressions follow Pine's precedence (lowest first): `?:`, `or`, `and`, `== !=`, `< > <= >=`, `+ -`, `* / %`, unary
`- + not`, then `x[n]` and calls. The nesting depth is capped (MAX_DEPTH), so deep input is refused, never recursed into.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union

from app.lab.pine.tokens import PineSyntaxError, Scan, Tok, tokenize

MAX_DEPTH = 40
MAX_OPERATORS = 200          # operators in one statement (a long flat chain is a deep tree too)
QUALIFIERS = {"series", "simple", "const", "input"}
TYPES = {"int", "float", "bool", "string", "color", "line", "label", "box", "table", "linefill", "polyline"}
KEYWORDS = {"and", "or", "not", "if", "else", "for", "to", "by", "in", "while", "switch", "var", "varip", "true", "false",
            "import", "export", "method", "type", "continue", "break", "enum"}
REASSIGN_OPS = {":=", "+=", "-=", "*=", "/=", "%="}


# --- the Pine syntax tree ------------------------------------------------------------------------------------------------

@dataclass
class PNum:
    value: float
    is_int: bool
    line: int
    col: int


@dataclass
class PStr:
    value: str
    line: int
    col: int


@dataclass
class PBool:
    value: bool
    line: int
    col: int


@dataclass
class PColor:
    value: str
    line: int
    col: int


@dataclass
class PName:
    name: str                 # dotted as written: `close`, `ta.ema`, `strategy.long`
    line: int
    col: int


@dataclass
class PCall:
    name: str
    args: List["PExpr"]
    kwargs: Dict[str, "PExpr"]
    line: int
    col: int
    kwarg_order: List[str] = field(default_factory=list)


@dataclass
class PIndex:
    target: "PExpr"
    index: "PExpr"
    line: int
    col: int


@dataclass
class PUnary:
    op: str                   # - + not
    operand: "PExpr"
    line: int
    col: int


@dataclass
class PBinary:
    op: str
    left: "PExpr"
    right: "PExpr"
    line: int
    col: int


@dataclass
class PTernary:
    cond: "PExpr"
    a: "PExpr"
    b: "PExpr"
    line: int
    col: int


@dataclass
class PList:
    items: List["PExpr"]
    line: int
    col: int


PExpr = Union[PNum, PStr, PBool, PColor, PName, PCall, PIndex, PUnary, PBinary, PTernary, PList]


@dataclass
class SAssign:
    names: List[str]
    expr: PExpr
    line: int
    col: int
    is_tuple: bool = False


@dataclass
class SExpr:
    expr: PExpr
    line: int
    col: int


@dataclass
class SIf:
    cond: PExpr
    body: List["Stmt"]
    line: int
    col: int


@dataclass
class SUnsupported:
    code: str                 # a fixed construct code (see translate.UNSUPPORTED_STATEMENTS)
    message: str
    alternative: str
    line: int
    col: int


Stmt = Union[SAssign, SExpr, SIf, SUnsupported]


@dataclass
class Program:
    statements: List[Stmt]
    version: Optional[str]
    version_line: int


def walk(node: PExpr):
    """Every node of a Pine expression, depth first (an explicit stack: no recursion on deep trees)."""
    stack: List[PExpr] = [node]
    while stack:
        cur = stack.pop()
        yield cur
        if isinstance(cur, PCall):
            stack.extend(cur.kwargs.values())
            stack.extend(reversed(cur.args))
        elif isinstance(cur, PIndex):
            stack.extend((cur.index, cur.target))
        elif isinstance(cur, PUnary):
            stack.append(cur.operand)
        elif isinstance(cur, PBinary):
            stack.extend((cur.right, cur.left))
        elif isinstance(cur, PTernary):
            stack.extend((cur.b, cur.a, cur.cond))
        elif isinstance(cur, PList):
            stack.extend(reversed(cur.items))


# --- expressions ---------------------------------------------------------------------------------------------------------

class _Expr:
    def __init__(self, toks: List[Tok]) -> None:
        self.toks = toks + [Tok("eof", "", toks[-1].line if toks else 1, (toks[-1].col + 1) if toks else 1)]
        self.i = 0
        self.depth = 0
        self.operators = 0

    @property
    def tok(self) -> Tok:
        return self.toks[self.i]

    def peek(self, k: int = 1) -> Tok:
        return self.toks[min(self.i + k, len(self.toks) - 1)]

    def next(self) -> Tok:
        t = self.toks[self.i]
        self.i = min(self.i + 1, len(self.toks) - 1)
        return t

    def is_op(self, *texts: str) -> bool:
        return self.tok.kind == "op" and self.tok.text in texts

    def is_kw(self, *words: str) -> bool:
        return self.tok.kind == "name" and self.tok.text in words

    def expect_op(self, text: str) -> Tok:
        if not self.is_op(text):
            found = self.tok.text or "the end of the line"
            raise PineSyntaxError(f"expected {text!r}, found {found!r}", self.tok.line, self.tok.col)
        return self.next()

    def enter(self) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise PineSyntaxError(f"the expression is nested more than {MAX_DEPTH} levels deep", self.tok.line, self.tok.col, code="limit")

    def leave(self) -> None:
        self.depth -= 1

    def operator(self) -> Tok:
        self.operators += 1
        if self.operators > MAX_OPERATORS:
            raise PineSyntaxError(f"the statement has more than {MAX_OPERATORS} operators; split it into named parts",
                                  self.tok.line, self.tok.col, code="limit")
        return self.next()

    def done(self) -> None:
        if self.tok.kind != "eof":
            raise PineSyntaxError(f"unexpected {self.tok.text!r}", self.tok.line, self.tok.col)

    # precedence levels, lowest first
    def expr(self) -> PExpr:
        self.enter()
        cond = self.or_expr()
        if self.is_op("?"):
            t = self.operator()
            a = self.expr()
            self.expect_op(":")
            b = self.expr()
            cond = PTernary(cond, a, b, t.line, t.col)
        self.leave()
        return cond

    def or_expr(self) -> PExpr:
        node = self.and_expr()
        while self.is_kw("or"):
            t = self.operator()
            node = PBinary("or", node, self.and_expr(), t.line, t.col)
        return node

    def and_expr(self) -> PExpr:
        node = self.eq_expr()
        while self.is_kw("and"):
            t = self.operator()
            node = PBinary("and", node, self.eq_expr(), t.line, t.col)
        return node

    def eq_expr(self) -> PExpr:
        node = self.rel_expr()
        while self.is_op("==", "!="):
            t = self.operator()
            node = PBinary(t.text, node, self.rel_expr(), t.line, t.col)
        return node

    def rel_expr(self) -> PExpr:
        node = self.add_expr()
        while self.is_op("<", ">", "<=", ">="):
            t = self.operator()
            node = PBinary(t.text, node, self.add_expr(), t.line, t.col)
        return node

    def add_expr(self) -> PExpr:
        node = self.mul_expr()
        while self.is_op("+", "-"):
            t = self.operator()
            node = PBinary(t.text, node, self.mul_expr(), t.line, t.col)
        return node

    def mul_expr(self) -> PExpr:
        node = self.unary()
        while self.is_op("*", "/", "%"):
            t = self.operator()
            node = PBinary(t.text, node, self.unary(), t.line, t.col)
        return node

    def unary(self) -> PExpr:
        if self.is_op("-", "+") or self.is_kw("not"):
            t = self.operator()
            self.enter()
            operand = self.unary()
            self.leave()
            return PUnary(t.text, operand, t.line, t.col)
        return self.postfix()

    def postfix(self) -> PExpr:
        node = self.primary()
        steps = 0
        while self.is_op("["):
            steps += 1
            if steps > MAX_DEPTH:
                raise PineSyntaxError("too many history references in a row", self.tok.line, self.tok.col, code="limit")
            t = self.next()
            index = self.expr()
            self.expect_op("]")
            node = PIndex(node, index, t.line, t.col)
        return node

    def call_args(self, name_tok: Tok) -> PCall:
        self.expect_op("(")
        self.enter()
        args: List[PExpr] = []
        kwargs: Dict[str, PExpr] = {}
        order: List[str] = []
        if not self.is_op(")"):
            while True:
                if self.tok.kind == "name" and "." not in self.tok.text and self.peek().kind == "op" and self.peek().text == "=":
                    key = self.next().text
                    self.next()
                    if key in kwargs:
                        raise PineSyntaxError(f"argument {key!r} is given twice", self.tok.line, self.tok.col)
                    kwargs[key] = self.expr()
                    order.append(key)
                else:
                    if kwargs:
                        raise PineSyntaxError("a positional argument follows a named one", self.tok.line, self.tok.col)
                    args.append(self.expr())
                if not self.is_op(","):
                    break
                self.next()
        self.expect_op(")")
        self.leave()
        return PCall(name_tok.text, args, kwargs, name_tok.line, name_tok.col, order)

    def primary(self) -> PExpr:
        t = self.tok
        if t.kind == "num":
            self.next()
            is_int = not any(c in t.text for c in ".eE")
            try:
                value = float(t.text)
            except ValueError:
                raise PineSyntaxError(f"{t.text!r} is not a number", t.line, t.col) from None
            return PNum(value, is_int, t.line, t.col)
        if t.kind == "str":
            self.next()
            return PStr(t.text, t.line, t.col)
        if t.kind == "color":
            self.next()
            return PColor(t.text, t.line, t.col)
        if t.kind == "name":
            if t.text in ("true", "false"):
                self.next()
                return PBool(t.text == "true", t.line, t.col)
            if t.text in KEYWORDS:
                raise PineSyntaxError(f"{t.text!r} cannot start a value here", t.line, t.col)
            self.next()
            if self.is_op("("):
                return self.call_args(t)
            return PName(t.text, t.line, t.col)
        if t.kind == "op" and t.text == "(":
            self.next()
            node = self.expr()
            self.expect_op(")")
            return node
        if t.kind == "op" and t.text == "[":
            self.next()
            self.enter()
            items: List[PExpr] = []
            if not self.is_op("]"):
                while True:
                    items.append(self.expr())
                    if not self.is_op(","):
                        break
                    self.next()
            self.expect_op("]")
            self.leave()
            return PList(items, t.line, t.col)
        found = t.text or "the end of the line"
        raise PineSyntaxError(f"expected a value, a name or a call, found {found!r}", t.line, t.col)


def parse_expression(toks: List[Tok]) -> PExpr:
    p = _Expr(toks)
    node = p.expr()
    p.done()
    return node


# --- statements and blocks -----------------------------------------------------------------------------------------------

_BLOCK_ALTERNATIVE = ("write the condition as one expression and use it in alertcondition(), plotshape() or an "
                      "`if cond` that holds only strategy.entry / strategy.close")
_STATE_ALTERNATIVE = ("ATP evaluates every bar from the bars alone, so a value carried from bar to bar is not translated; "
                      "use a history reference (x[1]) or ta.highest / ta.lowest over a window")


def _lines(scan: Scan) -> List[List[Tok]]:
    out: List[List[Tok]] = []
    cur: List[Tok] = []
    for t in scan.tokens:
        if t.kind == "eof":
            break
        if t.kind == "newline":
            if cur:
                out.append(cur)
            cur = []
            continue
        cur.append(t)
    if cur:
        out.append(cur)
    return out


def _top_level_op(toks: List[Tok], ops: set) -> Optional[Tok]:
    depth = 0
    for t in toks:
        if t.kind == "op" and t.text in "([":
            depth += 1
        elif t.kind == "op" and t.text in ")]":
            depth -= 1
        elif depth == 0 and t.kind == "op" and t.text in ops:
            return t
    return None


class _Block:
    def __init__(self, lines: List[List[Tok]]) -> None:
        self.lines = lines
        self.i = 0

    def skip_body(self, level: int) -> None:
        while self.i < len(self.lines) and self.lines[self.i][0].indent > level:
            self.i += 1

    def block(self, level: int) -> List[Stmt]:
        out: List[Stmt] = []
        while self.i < len(self.lines):
            toks = self.lines[self.i]
            first = toks[0]
            if first.indent < level:
                break
            if first.indent > level:
                out.append(SUnsupported("indent", "this line is indented, but the line before it does not open a block",
                                        "remove the indent, or join it to the line before", first.line, first.col))
                self.skip_body(level)
                continue
            self.i += 1
            out.extend(self.statement(toks, level))
        return out

    def statement(self, toks: List[Tok], level: int) -> List[Stmt]:  # noqa: C901 - one switch over the statement forms
        first = toks[0]
        word = first.text if first.kind == "name" else ""
        if word == "if":
            try:
                cond = parse_expression(toks[1:]) if len(toks) > 1 else None
            except PineSyntaxError as exc:
                self.skip_body(level)
                return [SUnsupported(exc.code, exc.message, "check the condition after `if`", exc.line, exc.col)]
            if cond is None:
                self.skip_body(level)
                return [SUnsupported("syntax", "`if` needs a condition", "write `if cond` and indent its body", first.line, first.col)]
            body = self.block(level + 1)
            out: List[Stmt] = [SIf(cond, body, first.line, first.col)]
            while self.i < len(self.lines) and self.lines[self.i][0].indent == level and self.lines[self.i][0].text == "else":
                e = self.lines[self.i][0]
                self.i += 1
                self.skip_body(level)
                out.append(SUnsupported("else", "`else` branches are not in the Pine subset",
                                        "write the other case as its own `if not cond` block", e.line, e.col))
            return out
        if word == "else":
            self.skip_body(level)
            return [SUnsupported("else", "`else` without an `if` before it", "remove it", first.line, first.col)]
        if word in ("for", "while"):
            self.skip_body(level)
            return [SUnsupported("loop", f"`{word}` loops are not in the Pine subset", _STATE_ALTERNATIVE, first.line, first.col)]
        has_switch = any(t.kind == "name" and t.text == "switch" for t in toks)
        if has_switch or _top_level_op(toks, {"=>"}) is not None:
            is_switch = has_switch
            self.skip_body(level)
            if is_switch:
                return [SUnsupported("switch", "`switch` is not in the Pine subset", _BLOCK_ALTERNATIVE, first.line, first.col)]
            return [SUnsupported("function", "user-defined functions are not in the Pine subset",
                                 "write the function's body inline where it is used", first.line, first.col)]
        if word in ("var", "varip"):
            self.skip_body(level)
            return [SUnsupported("var", f"`{word}` keeps a value from bar to bar; that is not in the Pine subset",
                                 _STATE_ALTERNATIVE, first.line, first.col)]
        if word in ("import", "export", "method", "type", "library", "enum"):
            self.skip_body(level)
            return [SUnsupported(word, f"`{word}` is not in the Pine subset", "keep the script self-contained (indicator or strategy)",
                                 first.line, first.col)]
        if word in ("continue", "break"):
            return [SUnsupported("loop", f"`{word}` is not in the Pine subset", _STATE_ALTERNATIVE, first.line, first.col)]
        reassign = _top_level_op(toks, REASSIGN_OPS)
        if reassign is not None:
            self.skip_body(level)
            return [SUnsupported("reassign", f"`{reassign.text}` changes a variable after it is declared; that is not in the Pine subset",
                                 "declare each value once with `=` under its own name", reassign.line, reassign.col)]
        try:
            stmt = self._simple(toks)
        except PineSyntaxError as exc:
            self.skip_body(level)
            return [SUnsupported(exc.code, exc.message, "check the line against the supported subset", exc.line, exc.col)]
        if self.i < len(self.lines) and self.lines[self.i][0].indent > level:
            nxt = self.lines[self.i][0]
            self.skip_body(level)
            return [stmt, SUnsupported("indent", "this line is indented, but the line before it does not open a block",
                                       "remove the indent, or join it to the line before", nxt.line, nxt.col)]
        return [stmt]

    def _simple(self, toks: List[Tok]) -> Stmt:
        first = toks[0]
        # [a, b, c] = expr
        if first.kind == "op" and first.text == "[":
            close = next((k for k, t in enumerate(toks) if t.kind == "op" and t.text == "]"), -1)
            if close > 0 and close + 1 < len(toks) and toks[close + 1].kind == "op" and toks[close + 1].text == "=":
                names: List[str] = []
                inner = toks[1:close]
                for k, t in enumerate(inner):
                    if k % 2 == 0:
                        if t.kind != "name" or "." in t.text or t.text in KEYWORDS:
                            raise PineSyntaxError("a tuple on the left holds plain names: [a, b, c]", t.line, t.col)
                        names.append(t.text)
                    elif not (t.kind == "op" and t.text == ","):
                        raise PineSyntaxError("names in a tuple are separated by commas", t.line, t.col)
                if not names or len(inner) % 2 == 0:
                    raise PineSyntaxError("a tuple on the left holds plain names: [a, b, c]", first.line, first.col)
                rest = toks[close + 2:]
                if not rest:
                    raise PineSyntaxError("nothing after `=`", toks[close + 1].line, toks[close + 1].col)
                return SAssign(names, parse_expression(rest), first.line, first.col, is_tuple=True)
        # [qualifier] [type] NAME = expr
        k = 0
        while k < len(toks) and toks[k].kind == "name" and toks[k].text in QUALIFIERS and k + 1 < len(toks) and toks[k + 1].kind == "name":
            k += 1
        if k < len(toks) and toks[k].kind == "name" and toks[k].text in TYPES and k + 1 < len(toks) and toks[k + 1].kind == "name":
            k += 1
        if k + 1 < len(toks) and toks[k].kind == "name" and toks[k + 1].kind == "op" and toks[k + 1].text == "=":
            name = toks[k]
            if "." in name.text or name.text in KEYWORDS:
                raise PineSyntaxError(f"{name.text!r} cannot be declared", name.line, name.col)
            rest = toks[k + 2:]
            if not rest:
                raise PineSyntaxError("nothing after `=`", toks[k + 1].line, toks[k + 1].col)
            return SAssign([name.text], parse_expression(rest), name.line, name.col)
        return SExpr(parse_expression(toks), first.line, first.col)


def parse(text: str) -> Program:
    """Pine text -> Program. A problem that stops the scan (size limits, a malformed token, an unclosed bracket) raises
    PineSyntaxError with its line; a problem inside one statement becomes an SUnsupported statement and parsing goes on
    with the next line. Any other exception is a bug (fuzz-tested)."""
    scan = tokenize(text)
    lines = _lines(scan)
    block = _Block(lines)
    statements = block.block(0)
    return Program(statements, scan.version, scan.version_line)


__all__ = ["parse", "parse_expression", "walk", "Program", "PineSyntaxError", "MAX_DEPTH", "PExpr", "Stmt",
           "PNum", "PStr", "PBool", "PColor", "PName", "PCall", "PIndex", "PUnary", "PBinary", "PTernary", "PList",
           "SAssign", "SExpr", "SIf", "SUnsupported"]
