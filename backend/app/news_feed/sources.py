"""Phase BB: the feed registry and the RSS/Atom reader.

Every source here is a public syndication feed its publisher offers for exactly this purpose;
nothing is scraped from a web page and no site's terms are worked around. Only the headline, the
link and the publication time are stored (`FeedItem`); a feed's own summary text is used for the
keyword classification in memory and then dropped. Sources whose terms restrict automated access
(the exchanges' corporate-announcement feeds) are registered but **off** by default and stay off
until the operator turns them on after reading `docs/DATA_SOURCES.md`. A paid provider would plug
in behind the same `FeedItem` shape (`NewsProvider` seam, not implemented).

The XML reader is the standard library's; the response is capped in size and refused when it
declares entities, which closes the classic expansion attacks without a new dependency.
"""
from __future__ import annotations

import hashlib
import logging
import re
import defusedxml.ElementTree as ET  # P0.7: refuses entity expansion and external references outright
from xml.etree.ElementTree import Element  # the parsed nodes are stdlib Elements; defusedxml exports no Element
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, Iterable, List, Optional, Tuple

import httpx

from app.news_events.models import NewsEventCategory

logger = logging.getLogger(__name__)

MAX_BYTES = 512 * 1024
TIMEOUT_SECONDS = 10.0
MAX_ITEMS_PER_FETCH = 60
HEADERS = {"User-Agent": "Mozilla/5.0 (AMW Algorithmic Trading; news feed reader)", "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml,*/*"}


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    url: str
    category: NewsEventCategory
    default_on: bool
    publisher: str
    terms: str                       # the one-line ToS note shown in Settings and docs/DATA_SOURCES.md
    official: bool = True            # the publisher is the primary authority for what it reports
    store_summary: bool = False      # never for market RSS: headline + link only


SOURCES: Tuple[Source, ...] = (
    Source("rbi_press", "RBI press releases", "https://www.rbi.org.in/pressreleases_rss.xml", NewsEventCategory.RBI_POLICY, True, "Reserve Bank of India",
           "Official RSS feed published by RBI for syndication; headline + link stored."),
    Source("sebi_press", "SEBI press releases and circulars", "https://www.sebi.gov.in/sebirss.xml", NewsEventCategory.GOVT_POLICY, True, "Securities and Exchange Board of India",
           "Official RSS feed published by SEBI; headline + link stored."),
    Source("nse_announcements", "NSE corporate announcements", "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml", NewsEventCategory.CORPORATE, False,
           "National Stock Exchange of India", "NSE's terms restrict automated access and redistribution of site content; OFF until the operator confirms the feed may be used."),
    Source("bse_announcements", "BSE corporate announcements", "https://www.bseindia.com/data/xml/announcements.xml", NewsEventCategory.CORPORATE, False,
           "BSE Ltd", "BSE's terms restrict automated access and redistribution; OFF until the operator confirms the feed may be used."),
    Source("et_markets", "Economic Times - Markets", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", NewsEventCategory.OTHER, False,
           "The Economic Times", "Publisher RSS for personal, non-commercial syndication; headline + link only, no body stored; OFF by default.", official=False),
    Source("moneycontrol_markets", "Moneycontrol - Market reports", "https://www.moneycontrol.com/rss/marketreports.xml", NewsEventCategory.OTHER, False,
           "Moneycontrol", "Publisher RSS; headline + link only, no body stored; OFF by default.", official=False),
)
BY_ID: Dict[str, Source] = {s.id: s for s in SOURCES}


@dataclass
class FeedItem:
    source_id: str
    title: str
    link: str
    published_at: Optional[datetime]
    guid: str
    summary: str = ""                 # used in memory for classification only, never stored
    symbols: List[str] = field(default_factory=list)

    @property
    def dedupe_hash(self) -> str:
        key = f"{self.source_id}|{self.guid or self.link or _norm(self.title)}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def _text(node: Optional[Element]) -> str:
    return re.sub(r"<[^>]+>", "", (node.text or "")).strip() if node is not None and node.text else ""


def _when(raw: str) -> Optional[datetime]:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_feed(source_id: str, body: bytes) -> List[FeedItem]:
    """RSS 2.0 or Atom -> items. Refuses oversized bodies and any document that declares entities."""
    if len(body) > MAX_BYTES:
        raise ValueError(f"feed body over {MAX_BYTES} bytes")
    if b"<!ENTITY" in body or b"<!DOCTYPE" in body:
        raise ValueError("feed declares a DOCTYPE/ENTITY - refused")
    root = ET.fromstring(body)
    items: List[FeedItem] = []
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for node in root.iter("item"):                                     # RSS 2.0
        title = _text(node.find("title"))
        if not title:
            continue
        items.append(FeedItem(source_id, title[:500], _text(node.find("link"))[:1000], _when(_text(node.find("pubDate"))),
                              _text(node.find("guid")) or _text(node.find("link")), _text(node.find("description"))[:1000]))
    if not items:
        for node in root.findall("atom:entry", ns):                      # Atom
            title = _text(node.find("atom:title", ns))
            if not title:
                continue
            link_node = node.find("atom:link", ns)
            link = (link_node.get("href") if link_node is not None else "") or ""
            when = _text(node.find("atom:published", ns)) or _text(node.find("atom:updated", ns))
            items.append(FeedItem(source_id, title[:500], link[:1000], _when(when), _text(node.find("atom:id", ns)) or link, _text(node.find("atom:summary", ns))[:1000]))
    return items[:MAX_ITEMS_PER_FETCH]


async def fetch_source(client: httpx.AsyncClient, source: Source) -> List[FeedItem]:
    response = await client.get(source.url, headers=HEADERS, timeout=TIMEOUT_SECONDS, follow_redirects=True)
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}")
    return parse_feed(source.id, response.content[:MAX_BYTES + 1])


async def fetch_many(client: Optional[httpx.AsyncClient], sources: Iterable[Source]) -> Tuple[List[FeedItem], List[str]]:
    """Every enabled source once; a failing source is an error line, never an exception."""
    items: List[FeedItem] = []
    errors: List[str] = []
    own = client is None
    client = client or httpx.AsyncClient()
    try:
        for source in sources:
            try:
                items.extend(await fetch_source(client, source))
            except Exception as exc:  # noqa: BLE001 - one feed down must not stop the others
                errors.append(f"{source.id}: {type(exc).__name__}: {str(exc)[:120]}")
    finally:
        if own:
            await client.aclose()
    return items, errors
