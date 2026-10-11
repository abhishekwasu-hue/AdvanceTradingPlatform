"""Z10-f: what the support bot knows - the help pages and the legal pages - and how it finds the passage for a question.

The corpus is two sources, both shipped with the backend:

* `content/help.md` - one how-to entry per `## ` heading (the question), with `keywords:` (other words people use for
  it), `page:` (where in the app it happens) and, for an entry that only makes sense with the legal pages published,
  `requires: legal_pages`;
* the `## ` sections of the Privacy notice, Terms of use and Risk disclosure (`app.legal.documents`), rendered with the
  live retention and breach-notice settings - only while the `legal_pages` flag is on (`include_legal`).

Retrieval is a plain keyword score, deterministic and explainable: each content word of the question scores its inverse
document frequency, three times in a passage title, twice in its keywords, once in its text. The **confidence** of a
match is the share of the question's content words (weighted by that same idf) the passage covers - a word the corpus
has never seen counts against it at full weight. Below the floor (`SUPPORT_BOT_MIN_CONFIDENCE`, default 0.5) the bot
does not guess: it hands the question to the feedback form.
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Set, Tuple

from app.legal import documents

HELP_FILE = Path(__file__).resolve().parent / "content" / "help.md"
DEFAULT_MIN_CONFIDENCE = 0.5
_WORD = re.compile(r"[a-z0-9]+(?:&[a-z0-9]+)?")
STOPWORDS = frozenset("""
a an the i me my mine we our us you your yours it its this that these those is are was were be been being am do does did
doing have has had how what where when why which who whom can could would should will shall may might must to of in on
at by for with from into about as if or and but not no so than then there here please pls get got also just any some
want need like know tell help me let s t""".split()) - {"help"}


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 5 and word.endswith("ing"):
        return word[:-3]
    if len(word) > 4 and word.endswith("ed") and not word.endswith("eed"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def tokens(text: str) -> List[str]:
    """Lower-cased, stemmed content words (stop words dropped), in order."""
    return [_stem(w) for w in _WORD.findall((text or "").lower().replace("'", "")) if w not in STOPWORDS]


@dataclass(frozen=True)
class Passage:
    id: str
    source: str            # "help" | "legal"
    title: str
    text: str
    url: str               # the app path that shows it
    keywords: str = ""
    requires_legal: bool = False
    weights: Dict[str, int] = field(default_factory=dict, compare=False, hash=False)

    def link(self) -> dict:
        return {"title": self.title, "url": self.url, "source": self.source}


def _weights(title: str, keywords: str, text: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for weight, part in ((1, text), (2, keywords), (3, title)):
        for t in tokens(part):
            out[t] = max(out.get(t, 0), weight)
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def parse_help(raw: str) -> List[Passage]:
    passages: List[Passage] = []
    for block in re.split(r"^## ", raw, flags=re.M)[1:]:
        heading, _, rest = block.partition("\n")
        meta: Dict[str, str] = {}
        body: List[str] = []
        for line in rest.strip().splitlines():
            key, sep, value = line.partition(":")
            if sep and key in ("keywords", "page", "requires") and not body:
                meta[key] = value.strip()
            else:
                body.append(line)
        text = "\n".join(body).strip()
        title = heading.strip()
        if not title or not text:
            raise ValueError(f"help entry {title!r} has no text")
        page = meta.get("page", "/")
        if not page.startswith("/"):
            raise ValueError(f"help entry {title!r}: page must be an app path, got {page!r}")
        requires = meta.get("requires", "")
        if requires not in ("", "legal_pages"):
            raise ValueError(f"help entry {title!r}: requires must be legal_pages, got {requires!r}")
        if page.startswith("/legal/") and requires != "legal_pages":
            raise ValueError(f"help entry {title!r}: a /legal/ page needs 'requires: legal_pages'")
        passages.append(Passage(id=f"help:{_slug(title)}", source="help", title=title, text=text, url=page,
                                keywords=meta.get("keywords", ""), requires_legal=requires == "legal_pages", weights=_weights(title, meta.get("keywords", ""), text)))
    return passages


def legal_passages() -> List[Passage]:
    out: List[Passage] = []
    for slug in documents.SLUGS:
        doc = documents.get(slug)
        for heading, text in documents.sections(doc):
            title = f"{doc.title}: {heading}" if heading != doc.title else doc.title
            out.append(Passage(id=f"legal:{slug}:{_slug(heading)}", source="legal", title=title, text=text,
                               url=f"/legal/{slug}", requires_legal=True, weights=_weights(title, "", text)))
    return out


@dataclass(frozen=True)
class Index:
    passages: Tuple[Passage, ...]
    idf: Dict[str, float]
    max_idf: float


@lru_cache(maxsize=2)
def index(include_legal: bool = True) -> Index:
    """The searchable corpus; without `include_legal`, nothing from or about the legal pages (flag `legal_pages` off)."""
    passages = tuple(p for p in parse_help(HELP_FILE.read_text(encoding="utf-8")) + legal_passages()
                     if include_legal or not p.requires_legal)
    n = len(passages)
    df: Dict[str, int] = {}
    for p in passages:
        for t in p.weights:
            df[t] = df.get(t, 0) + 1
    idf = {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}
    return Index(passages=passages, idf=idf, max_idf=math.log((n + 1) / 0.5))


def min_confidence() -> float:
    try:
        value = float(os.environ.get("SUPPORT_BOT_MIN_CONFIDENCE", str(DEFAULT_MIN_CONFIDENCE)))
    except ValueError:
        value = DEFAULT_MIN_CONFIDENCE
    return min(0.95, max(0.1, value))


@dataclass(frozen=True)
class Match:
    passage: Passage
    score: float
    confidence: float
    matched: Tuple[str, ...]


def search(question: str, limit: int = 3, *, include_legal: bool = True) -> List[Match]:
    """The best passages for `question`, best first (only those that share at least one content word)."""
    idx = index(include_legal)
    words: Set[str] = set(tokens(question))
    if not words:
        return []
    total = sum(idx.idf.get(w, idx.max_idf) for w in words)
    found: List[Match] = []
    for p in idx.passages:
        hit = [w for w in words if w in p.weights]
        if not hit:
            continue
        score = sum(idx.idf[w] * p.weights[w] for w in hit)
        confidence = sum(idx.idf[w] for w in hit) / total
        found.append(Match(passage=p, score=score, confidence=round(confidence, 3), matched=tuple(sorted(hit))))
    found.sort(key=lambda m: (-m.score, -m.confidence, m.passage.id))
    return found[:limit]


__all__ = ["Match", "Passage", "index", "min_confidence", "parse_help", "search", "tokens"]
