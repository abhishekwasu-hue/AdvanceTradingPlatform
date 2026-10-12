"""Z10-f (review H1): one canonical form of the user's question before anything reads it.

* NFKC: full-width and mathematical letters ("ｂｕｙ", "𝐛𝐮𝐲") become plain ones.
* Unicode format characters (category Cf: zero-width space / joiner, soft hyphen, bidi marks) are removed, so
  "sh<zero-width space>ould" reads "should".
* Latin look-alikes from Cyrillic and Greek ("ѕhould", "Ьuy") are mapped to the Latin letter they imitate.
* Runs of whitespace collapse to one space.

`compact()` is the same text in lower case with everything but ASCII letters and digits removed - the guard checks it
for spaced-out words ("s h o u l d I b u y").
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


def has_non_ascii_letters(text: str) -> bool:
    """True when `text` has a letter outside ASCII (another script, or an accent) - such a question never goes to a
    model, and a model answer with one is never shown."""
    return any(ch.isalpha() and not ch.isascii() for ch in text or "")


__all__ = ["compact", "has_non_ascii_letters", "normalize"]
