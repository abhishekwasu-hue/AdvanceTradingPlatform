"""Z10-f: the three legal documents (Privacy notice, Terms of use, Risk disclosure) as versioned, editable content.

The text lives in `app/legal/content/<slug>.md`: a small front matter (`slug`, `title`, `version`) and a Markdown body
that opens with the "Draft - to be reviewed by a lawyer before launch" banner. Editing a document means editing its
file and bumping its `version` (a date); the acceptance check also compares the SHA-256 of the source text, so an edit
without a version bump still asks everyone again (the same rule as the AI acknowledgements, `ai.compliance_terms`).

Facts the platform already holds as configuration are not copied into the text: `{{retention.<field>}}` reads the DPDP
retention policy (`app.retention.policy.load_policy`), `{{compliance.<param>}}` the go-live checklist rule of the
Indian rule set (`in_sebi.json`, `IN-SEBI.golive.checklist`: breach notice hours, log retention). They are filled in
when the page is served; the acceptance hash is of the source text, so changing a retention setting does not ask
everyone to accept again. Company facts that do not exist yet (company name, address, grievance officer) stay as
`[SQUARE BRACKET]` placeholders, listed in `open_placeholders` until the company is registered.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Tuple

CONTENT_DIR = Path(__file__).resolve().parent / "content"
SLUGS: Tuple[str, ...] = ("terms", "privacy", "risk")
DRAFT_BANNER = "Draft — to be reviewed by a lawyer before launch"
_FIELD = re.compile(r"\{\{\s*([a-z_]+)\.([a-z_]+)\s*\}\}")
_PLACEHOLDER = re.compile(r"\[([A-Z][A-Z ,—-]{2,})\]")
_VERSION = re.compile(r"^\d{4}-\d{2}-\d{2}(?:\.\d+)?$")


class LegalContentError(ValueError):
    """A content file that cannot be served (missing front matter, bad version, unknown field)."""


@dataclass(frozen=True)
class LegalDocument:
    slug: str
    title: str
    version: str
    source: str          # the Markdown body as written, with its {{fields}} unfilled
    sha256: str          # of the front matter version + the source body: what an acceptance is bound to

    @property
    def kind(self) -> str:
        """The `ai_acknowledgements.kind` an acceptance of this document is stored under."""
        return f"legal_{self.slug}"

    @property
    def draft(self) -> bool:
        return DRAFT_BANNER in self.source

    def open_placeholders(self) -> List[str]:
        return sorted({m.group(1) for m in _PLACEHOLDER.finditer(self.source)} - {"SQUARE BRACKETS"})

    def summary(self) -> dict:
        return {"slug": self.slug, "title": self.title, "version": self.version, "sha256": self.sha256, "draft": self.draft,
                "path": f"/legal/{self.slug}"}


def _parse(slug: str, raw: str) -> LegalDocument:
    if not raw.startswith("---\n"):
        raise LegalContentError(f"{slug}.md: no front matter")
    head, sep, body = raw[4:].partition("\n---\n")
    if not sep:
        raise LegalContentError(f"{slug}.md: front matter not closed")
    meta: Dict[str, str] = {}
    for line in head.splitlines():
        key, _, value = line.partition(":")
        if key.strip():
            meta[key.strip()] = value.strip()
    if meta.get("slug") != slug:
        raise LegalContentError(f"{slug}.md: front matter slug is {meta.get('slug')!r}")
    version, title = meta.get("version", ""), meta.get("title", "")
    if not _VERSION.fullmatch(version) or len(version) > 24:
        raise LegalContentError(f"{slug}.md: version must be a date (YYYY-MM-DD, optionally .N), got {version!r}")
    if not title:
        raise LegalContentError(f"{slug}.md: no title")
    source = body.strip() + "\n"
    digest = hashlib.sha256(f"legal|{slug}|{version}|{source}".encode("utf-8")).hexdigest()
    return LegalDocument(slug=slug, title=title, version=version, source=source, sha256=digest)


@lru_cache(maxsize=1)
def documents() -> Dict[str, LegalDocument]:
    """Every legal document by slug (read once per process; a content change ships with a deploy)."""
    return {slug: _parse(slug, (CONTENT_DIR / f"{slug}.md").read_text(encoding="utf-8")) for slug in SLUGS}


def get(slug: str) -> LegalDocument:
    return documents()[slug]


def facts() -> Dict[str, Dict[str, object]]:
    """The configuration values a document may quote, read at serve time."""
    from app.compliance import golive, rules
    from app.retention.policy import load_policy
    policy = load_policy().as_dict()
    ruleset = rules.load()
    return {
        "retention": {k: v for k, v in policy.items() if isinstance(v, int) and not isinstance(v, bool)},
        "compliance": {"breach_notify_hours": ruleset.param(golive.RULE, "dpdp_breach_notify_hours"),
                       "log_retention_days": ruleset.param(golive.RULE, "log_retention_days")},
    }


def render(doc: LegalDocument, values: Dict[str, Dict[str, object]] | None = None) -> str:
    """The body with every `{{group.field}}` filled in; an unknown field is a content error, never a blank."""
    values = values if values is not None else facts()

    def fill(m: re.Match) -> str:
        group, name = m.group(1), m.group(2)
        value = (values.get(group) or {}).get(name)
        if value is None:
            raise LegalContentError(f"{doc.slug}.md: unknown field {{{{{group}.{name}}}}}")
        return str(value)

    return _FIELD.sub(fill, doc.source)


def sections(doc: LegalDocument, values: Dict[str, Dict[str, object]] | None = None) -> List[Tuple[str, str]]:
    """(heading, text) for every `## ` section of the rendered body - what the support bot searches."""
    out: List[Tuple[str, str]] = []
    heading = doc.title
    lines: List[str] = []
    for line in render(doc, values).splitlines():
        if line.startswith("## "):
            if any(x.strip() for x in lines):
                out.append((heading, "\n".join(lines).strip()))
            heading, lines = line[3:].strip(), []
        elif not line.startswith("# ") and not line.startswith(">"):
            lines.append(line)
    if any(x.strip() for x in lines):
        out.append((heading, "\n".join(lines).strip()))
    return out


__all__ = ["DRAFT_BANNER", "SLUGS", "LegalContentError", "LegalDocument", "documents", "facts", "get", "render", "sections"]
