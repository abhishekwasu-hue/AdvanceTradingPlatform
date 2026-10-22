"""AMW-20 / handover 11c-1: the Pine Script tokenizer - a hand-written, single-pass scanner over untrusted text.

Nothing is ever evaluated: the scanner only cuts the text into tokens with their line and column. It is linear in the
input (no backtracking regular expressions), and the limits below are checked before and during the scan, so a large or
hostile paste is refused quickly instead of being worked through (threat model: the translator is a parser of
untrusted text).

Pine's line rules, as far as the subset needs them:
- `//` starts a comment to the end of the line; `//@version=N` is read as the version header.
- A statement ends at the end of its line, unless a `(` or `[` is still open, or the next line is indented by a number
  of spaces that is not a multiple of four (Pine's line wrapping). A line indented by a multiple of four is a block
  body (of an `if`, a `for`, a function...), and its indent level (tabs count as four spaces) is kept on the token.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

MAX_SOURCE = 50_000          # characters
MAX_LINES = 2_000
MAX_TOKENS = 20_000

_OPS2 = (":=", "+=", "-=", "*=", "/=", "%=", "==", "!=", "<=", ">=", "=>")
_OPS1 = "+-*/%<>=?:()[],"
_HEX = set("0123456789abcdefABCDEF")


class PineSyntaxError(ValueError):
    """A problem that stops the scan: the text is too large, or a token is malformed. Carries the line and column."""

    def __init__(self, message: str, line: int, col: int = 1, code: str = "syntax") -> None:
        super().__init__(f"line {line}: {message}")
        self.message, self.line, self.col, self.code = message, line, col, code


@dataclass
class Tok:
    kind: str                # num / str / name / color / op / newline / eof
    text: str
    line: int
    col: int
    indent: int = 0          # on the first token of a statement: its block level (indent // 4)


@dataclass
class Scan:
    tokens: List[Tok]
    version: Optional[str]           # the N of //@version=N, as written
    version_line: int


def _ident_start(c: str) -> bool:
    return c.isascii() and (c.isalpha() or c == "_")


def _ident_char(c: str) -> bool:
    return c.isascii() and (c.isalnum() or c == "_")


def tokenize(text: str) -> Scan:  # noqa: C901 - one scanner loop
    if not isinstance(text, str):
        raise PineSyntaxError("the script must be text", 1)
    if len(text) > MAX_SOURCE:
        raise PineSyntaxError(f"the script is longer than {MAX_SOURCE:,} characters", 1, code="limit")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if text.count("\n") + 1 > MAX_LINES:
        raise PineSyntaxError(f"the script has more than {MAX_LINES:,} lines", MAX_LINES, code="limit")
    out: List[Tok] = []
    version: Optional[str] = None
    version_line = 0
    i, line, col = 0, 1, 1
    depth = 0                        # open ( and [
    at_line_start = True
    pending_newline = False          # a statement has tokens and its line ended
    size = len(text)

    def add(kind: str, value: str, ln: int, cl: int) -> None:
        if len(out) >= MAX_TOKENS:
            raise PineSyntaxError(f"the script has more than {MAX_TOKENS:,} tokens", ln, cl, code="limit")
        out.append(Tok(kind, value, ln, cl))

    while i < size:
        if at_line_start:
            # measure the indent; a blank or comment-only line changes nothing
            j, width = i, 0
            while j < size and text[j] in " \t":
                width += 4 if text[j] == "\t" else 1
                j += 1
            rest_is_blank = j >= size or text[j] == "\n" or text.startswith("//", j)
            if not rest_is_blank:
                continuation = depth > 0 or (pending_newline and width % 4 != 0)
                if not continuation:
                    if pending_newline:
                        add("newline", "", line, 1)
                        pending_newline = False
                    indent_level = width // 4
                    col += j - i
                    i = j
                    at_line_start = False
                    # the next token is the first of a statement: it carries the block level
                    start_indent: Optional[int] = indent_level
                else:
                    col += j - i
                    i = j
                    at_line_start = False
                    start_indent = None
            else:
                col += j - i
                i = j
                at_line_start = False
                start_indent = None
            if i >= size:
                break
            first_index = len(out)
        else:
            first_index = -1
        c = text[i]
        if c == "\n":
            if len(out) and out[-1].kind != "newline":
                pending_newline = True
            i += 1
            line += 1
            col = 1
            at_line_start = True
            continue
        if c in " \t":
            i += 1
            col += 1
            continue
        if text.startswith("//", i):
            end = text.find("\n", i)
            end = size if end < 0 else end
            comment = text[i:end]
            if comment.startswith("//@version="):
                if version is None:
                    version, version_line = comment[len("//@version="):].strip(), line
            col += end - i
            i = end
            continue
        start_line, start_col = line, col
        if c.isascii() and c.isdigit() or (c == "." and i + 1 < size and text[i + 1].isascii() and text[i + 1].isdigit()):
            j = i
            while j < size and text[j].isascii() and text[j].isdigit():
                j += 1
            if j < size and text[j] == ".":
                j += 1
                while j < size and text[j].isascii() and text[j].isdigit():
                    j += 1
            if j < size and text[j] in "eE":
                k = j + 1
                if k < size and text[k] in "+-":
                    k += 1
                if k < size and text[k].isascii() and text[k].isdigit():
                    while k < size and text[k].isascii() and text[k].isdigit():
                        k += 1
                    j = k
            if j - i > 40:
                raise PineSyntaxError("a number is too long", start_line, start_col)
            add("num", text[i:j], start_line, start_col)
            col += j - i
            i = j
        elif c in "\"'":
            j = i + 1
            buf: List[str] = []
            while True:
                if j >= size or text[j] == "\n":
                    raise PineSyntaxError("a text in quotes is not closed on its line", start_line, start_col)
                ch = text[j]
                if ch == "\\" and j + 1 < size and text[j + 1] != "\n":
                    buf.append(text[j + 1])
                    j += 2
                    continue
                if ch == c:
                    j += 1
                    break
                buf.append(ch)
                j += 1
            add("str", "".join(buf), start_line, start_col)
            col += j - i
            i = j
        elif c == "#":
            j = i + 1
            while j < size and text[j] in _HEX:
                j += 1
            if j - i - 1 not in (3, 6, 8):
                raise PineSyntaxError("a colour is written #RRGGBB or #RRGGBBAA", start_line, start_col)
            add("color", text[i:j], start_line, start_col)
            col += j - i
            i = j
        elif _ident_start(c):
            j = i
            while True:
                while j < size and _ident_char(text[j]):
                    j += 1
                if j + 1 < size and text[j] == "." and _ident_start(text[j + 1]):
                    j += 1
                    continue
                break
            if j - i > 120:
                raise PineSyntaxError("a name is too long", start_line, start_col)
            add("name", text[i:j], start_line, start_col)
            col += j - i
            i = j
        else:
            two = text[i:i + 2]
            if two in _OPS2:
                add("op", two, start_line, start_col)
                i += 2
                col += 2
            elif c in _OPS1:
                if c in "([":
                    depth += 1
                elif c in ")]":
                    depth = max(0, depth - 1)
                add("op", c, start_line, start_col)
                i += 1
                col += 1
            else:
                shown = c if c.isprintable() else f"U+{ord(c):04X}"
                raise PineSyntaxError(f"unexpected character {shown!r}", start_line, start_col)
        if first_index >= 0 and start_indent is not None and len(out) > first_index:
            out[first_index].indent = start_indent
    if depth > 0:
        raise PineSyntaxError("a bracket is not closed before the end of the script", line, col)
    if out and out[-1].kind != "newline":
        add("newline", "", line, col)
    out.append(Tok("eof", "", line, col))
    return Scan(out, version, version_line)


__all__ = ["tokenize", "Tok", "Scan", "PineSyntaxError", "MAX_SOURCE", "MAX_LINES", "MAX_TOKENS"]
