"""Z10-f (review H1): one canonical form of the user's question before anything reads it.

* NFKC: full-width and mathematical letters ("ｂｕｙ", "𝐛𝐮𝐲") become plain ones.
* Unicode format characters (category Cf: zero-width space / joiner, soft hyphen, bidi marks) are removed, so
  "sh<zero-width space>ould" reads "should".
* Latin look-alikes from Cyrillic and Greek ("ѕhould", "Ьuy") are mapped to the Latin letter they imitate.
* Runs of whitespace collapse to one space.

`compact()` is the same text in lower case with everything but ASCII letters and digits removed - the guard checks it
for spaced-out words ("s h o u l d I b u y"). `deobfuscated()` gives the readings with spaced-out single letters joined
and digit look-alikes mapped back ("b u y", "8uy", "s e l l") - the guard checks those too.
"""
from __future__ import annotations

import re
import unicodedata

# Cyrillic and Greek letters that render like Latin ones (the common homoglyphs; NFKC leaves them alone).
_CONFUSABLES = str.maketrans({
    "а": "a", "в": "b", "Ь": "b", "ь": "b", "с": "c", "е": "e", "ё": "e", "һ": "h", "і": "i", "ї": "i", "ј": "j",
    "к": "k", "м": "m", "н": "h", "о": "o", "р": "p", "ԛ": "q", "г": "r", "ѕ": "s", "т": "t", "у": "y", "ѡ": "w", "х": "x",
    "ԁ": "d", "ɡ": "g", "ӏ": "l", "А": "A", "В": "B", "С": "C", "Е": "E", "Н": "H", "І": "I", "Ј": "J", "К": "K",
    "М": "M", "О": "O", "Р": "P", "Ѕ": "S", "Т": "T", "Х": "X", "У": "Y",
    "α": "a", "β": "b", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ο": "o", "ρ": "p", "τ": "t", "υ": "u", "χ": "x",
    "Α": "A", "Β": "B", "Ε": "E", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T",
    "Υ": "Y", "Χ": "X", "Ζ": "Z",
})
_SPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "")
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Cf")
    t = t.translate(_CONFUSABLES)
    return _SPACE.sub(" ", t).strip()


def compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", normalize(text).lower())


# Digits and symbols that stand in for a letter inside a word ("8uy", "5e11", "$ell"). "1" reads as "i" or "l", so both
# readings are tried.
_LEET = {"0": "o", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b", "9": "g", "@": "a", "$": "s"}
_LEET_CHARS = set(_LEET) | {"1"}
# A run of two or more single characters, letters or digits, set apart by spaces or . _ * - ("b u y", "s.e.l.l", "8 u y").
_SINGLES = re.compile(r"(?<![^\W_])(?:[^\W_][\s._*-]+){1,}[^\W_](?![^\W_])")


def _join_singles(text: str, keep_first: bool) -> str:
    def join(m: "re.Match[str]") -> str:
        chars = re.sub(r"[\s._*-]+", "", m.group(0))
        # "should I b u y": the first single letter may be a word of its own ("I", "a")
        return chars[0] + " " + chars[1:] if keep_first and len(chars) > 2 else chars
    return _SINGLES.sub(join, text)


def _unleet(text: str, one: str) -> str:
    def word(m: "re.Match[str]") -> str:
        w = m.group(0)
        if not any(c.isalpha() for c in w) or not any(c in _LEET_CHARS for c in w):
            return w                      # a plain number ("50", "15") or a plain word stays as it is
        return "".join(one if c == "1" else _LEET.get(c, c) for c in w)
    return re.sub(r"[\w@$]+", word, text)


def deobfuscated(text: str) -> list:
    """The readings of an already normalised `text` with spaced-out single letters joined ("b u y" -> "buy") and digit /
    symbol look-alikes inside a word mapped back ("8uy" -> "buy", "5e11" -> "sell"). Only readings that differ from
    `text` are returned - an empty list means nothing was disguised."""
    out = []
    for keep_first in (False, True):
        joined = _join_singles(text, keep_first)
        for one in ("i", "l"):
            v = _unleet(joined, one)
            if v != text and v not in out:
                out.append(v)
    return out


def has_non_ascii_letters(text: str) -> bool:
    """True when `text` has a letter outside ASCII (another script, or an accent) - such a question never goes to a
    model, and a model answer with one is never shown."""
    return any(ch.isalpha() and not ch.isascii() for ch in text or "")


__all__ = ["compact", "deobfuscated", "has_non_ascii_letters", "normalize"]
