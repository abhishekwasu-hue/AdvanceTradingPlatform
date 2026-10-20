"""Analyze a chart: fetch the image behind a chart link, safely (the SSRF design is docs/design/CHART_ANALYZE.md §6).

Which links are fetched:
- **TradingView snapshot links** `https://www.tradingview.com/x/<id>/` (any `*.tradingview.com` host): the snapshot
  page is never fetched or parsed; the image is read from TradingView's snapshot image address,
  `https://s3.tradingview.com/snapshots/<first letter of the id, lower case>/<id>.png`.
- **Direct image links** (`https://...` answering `image/png` or `image/jpeg`).
Anything else - a TradingView chart, symbol or idea page (a live page), a login wall, an HTML page, an error status, a
timeout, something that is not a PNG or JPEG, an image over the size limit - is `LinkUnreadable`, and the user is told
"Please upload a screenshot instead" (never a silent failure).

The safety rules, on the first request AND on every redirect hop:
- `https` only (a plain-http image could be swapped in transit and is not needed: TradingView and image hosts serve
  https), port 443, no user name or password in the address, an address of at most 2,048 characters;
- the host name is resolved here and EVERY address it resolves to must be public: loopback, private, link-local,
  multicast, reserved, unspecified, CGNAT (100.64.0.0/10), IPv6 unique-local and site-local, NAT64 and 6to4 /
  Teredo (which can carry a private IPv4 inside) are refused; an IP literal is checked the same way;
- the connection is then PINNED to the checked address: the request goes to that IP, with the real host name in the
  Host header and as the TLS SNI / certificate name, so a second DNS answer (DNS rebinding) is never used;
- redirects are followed by hand, at most `link_max_redirects`, each one checked as above;
- connect, read and total timeouts; no proxy from the environment (`trust_env=False`); no compressed transfer
  (`Accept-Encoding: identity`, a compressed answer is refused), so the size limit counts real bytes;
- the size is enforced while streaming (and from Content-Length first); the content type must be image/png or
  image/jpeg AND the first bytes must be the PNG or JPEG signature. The image then takes the same safe decode path as
  an upload (`imaging.sanitize`).
Internationalised and full-width host names are turned into their IDNA / ASCII form before anything else; any other
error while fetching is an unreadable link too, never a server error.

Not `EGRESS_ALLOWED_HOSTS`: that operator allow-list is for destinations an organisation configures once (webhooks, SMS
gateways); here users paste images from any host, which no list could name. The protection is the public-address check
on every hop plus the pinned connection, which an allow-list would not add to.
Tests pass a fake resolver and an `httpx.MockTransport`; nothing reaches the network.
"""
import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional
from urllib.parse import urljoin, urlsplit

import httpx

from app.chart_analyze import imaging

SCREENSHOT_MESSAGE = "Please upload a screenshot instead"
MAX_URL_LENGTH = 2048
TV_SUFFIX = "tradingview.com"
TV_SNAPSHOT_PATH = re.compile(r"^/x/([A-Za-z0-9]{4,32})/?$")
TV_IMAGE = "https://s3.tradingview.com/snapshots/{first}/{snapshot}.png"
IMAGE_TYPES = ("image/png", "image/jpeg", "image/jpg")
REDIRECTS = (301, 302, 303, 307, 308)
USER_AGENT = "ATP-chart-analyze/1 (+image fetch for the user's own analysis)"

# Networks Python does not already mark non-global, or that can carry another address inside.
_EXTRA_BLOCKED = tuple(ipaddress.ip_network(n) for n in (
    "100.64.0.0/10",       # CGNAT (shared address space)
    "192.0.0.0/24",        # IETF protocol assignments
    "198.18.0.0/15",       # benchmarking
    "64:ff9b::/96",        # NAT64 (embeds an IPv4 address)
    "64:ff9b:1::/48",      # local-use NAT64
    "2002::/16",           # 6to4 (embeds an IPv4 address)
    "2001::/32",           # Teredo
    "fec0::/10",           # IPv6 site-local (deprecated, still private)
))

Resolver = Callable[[str], Awaitable[List[str]]]


class LinkUnreadable(Exception):
    """The link could not be turned into an image. `reason` is short and safe to show; the user always also reads
    SCREENSHOT_MESSAGE."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Target:
    url: str          # the image address that is fetched
    kind: str         # "tradingview" or "direct"
    source_host: str  # the host the user pasted (stored; the full address is not)


@dataclass(frozen=True)
class Fetched:
    data: bytes
    target: Target


def address_allowed(address: str) -> bool:
    """True only for a public unicast address (IPv4 or IPv6)."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0].strip("[]"))
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified
            or not ip.is_global):
        return False
    return not any(ip in net for net in _EXTRA_BLOCKED if net.version == ip.version)


def _checked_parts(url: str) -> Any:
    if not isinstance(url, str) or len(url) > MAX_URL_LENGTH:
        raise LinkUnreadable("the link is too long")
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        raise LinkUnreadable("the link is not a valid web address") from None
    if parts.scheme.lower() != "https":
        raise LinkUnreadable("only https links can be opened")
    if parts.username is not None or parts.password is not None:
        raise LinkUnreadable("links with a user name or password are not opened")
    if not parts.hostname:
        raise LinkUnreadable("the link has no host")
    if port not in (None, 443):
        raise LinkUnreadable("only the standard https port is allowed")
    return parts


def ascii_host(host: str) -> str:
    """The host as the network sees it: an internationalised name (`bücher.de`) or full-width letters (`ｅxample.com`)
    become their IDNA / plain-ASCII form (`xn--bcher-kva.de`, `example.com`); that form is resolved, checked, put in the
    Host header and used as the TLS name. A name that cannot be encoded is refused."""
    host = (host or "").strip().rstrip(".").lower()
    try:
        ipaddress.ip_address(host.strip("[]"))
        return host.strip("[]")
    except ValueError:
        pass
    try:
        encoded = host.encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        raise LinkUnreadable("the link's host name is not valid") from None
    if not encoded or not re.fullmatch(r"[a-z0-9.-]{1,253}", encoded) or ".." in encoded:
        raise LinkUnreadable("the link's host name is not valid")
    return encoded


def resolve_link(raw: str) -> Target:
    """Which image a pasted link names. A TradingView page that is not a snapshot (a live chart, a symbol page, an
    idea) is refused here: it is a live page, not an image."""
    parts = _checked_parts((raw or "").strip())
    host = ascii_host(parts.hostname)
    if host == TV_SUFFIX or host.endswith("." + TV_SUFFIX):
        match = TV_SNAPSHOT_PATH.match(parts.path or "/")
        if host != "s3." + TV_SUFFIX and match:
            snapshot = match.group(1)
            return Target(url=TV_IMAGE.format(first=snapshot[0].lower(), snapshot=snapshot), kind="tradingview", source_host=host)
        if host == "s3." + TV_SUFFIX and (parts.path or "").lower().endswith(".png"):
            return Target(url=raw.strip(), kind="tradingview", source_host=host)
        raise LinkUnreadable("this TradingView link is a live page, not a snapshot - use the camera icon's 'Copy link to the chart image'")
    return Target(url=raw.strip(), kind="direct", source_host=host)


async def system_resolver(host: str) -> List[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        return []
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


async def _pinned_address(host: str, resolver: Resolver) -> str:
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
        addresses = [str(literal)]
    except ValueError:
        addresses = await resolver(host)
    if not addresses:
        raise LinkUnreadable("the link's host could not be found")
    if not all(address_allowed(a) for a in addresses):
        raise LinkUnreadable("the link points to an address that is not allowed")
    return addresses[0]


def _pinned_request(client: httpx.AsyncClient, parts: Any, host: str, address: str) -> httpx.Request:
    """The request to the checked address; the URL is rebuilt from the parsed parts with the IP as its host (never
    parsed again from the user's text), the ASCII name in Host and as the TLS name."""
    path = parts.path or "/"
    pinned = httpx.URL(scheme="https", host=address, path=path, query=parts.query.encode("utf-8") if parts.query else None)
    return client.build_request("GET", pinned, headers={"Host": host}, extensions={"sni_hostname": host})


def make_client(settings: Dict[str, Any], transport: Optional[httpx.AsyncBaseTransport] = None) -> httpx.AsyncClient:
    connect = float(settings["link_connect_timeout_seconds"])
    read = float(settings["link_read_timeout_seconds"])
    return httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False,
                             timeout=httpx.Timeout(connect=connect, read=read, write=read, pool=connect),
                             headers={"User-Agent": USER_AGENT, "Accept": "image/png,image/jpeg", "Accept-Encoding": "identity"})


async def _fetch(target: Target, settings: Dict[str, Any], resolver: Resolver, transport: Optional[httpx.AsyncBaseTransport]) -> bytes:
    max_bytes = int(settings["max_image_bytes"])
    url = target.url
    async with make_client(settings, transport) as client:
        for _hop in range(int(settings["link_max_redirects"]) + 1):
            parts = _checked_parts(url)
            host = ascii_host(parts.hostname)
            address = await _pinned_address(host, resolver)
            response = await client.send(_pinned_request(client, parts, host, address), stream=True)
            try:
                if response.status_code in REDIRECTS:
                    location = response.headers.get("location")
                    if not location:
                        raise LinkUnreadable("the link redirected nowhere")
                    url = urljoin(url, location)
                    continue
                if response.status_code in (401, 403):
                    raise LinkUnreadable("the link needs a login")
                if response.status_code != 200:
                    raise LinkUnreadable(f"the link answered with an error (HTTP {response.status_code})")
                ctype = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if ctype not in IMAGE_TYPES:
                    raise LinkUnreadable("the link opens a web page, not an image (a login page or a live chart)")
                if response.headers.get("content-encoding", "identity").strip().lower() not in ("", "identity"):
                    raise LinkUnreadable("the image was sent compressed and is not read")
                declared = response.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > max_bytes:
                    raise LinkUnreadable(f"the image at the link is larger than {max_bytes // 1024} KB")
                body = bytearray()
                async for chunk in response.aiter_raw():
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise LinkUnreadable(f"the image at the link is larger than {max_bytes // 1024} KB")
                data = bytes(body)
                if not imaging.sniff(data):
                    raise LinkUnreadable("the link did not return a PNG or JPEG image")
                return data
            finally:
                await response.aclose()
    raise LinkUnreadable("the link redirected too many times")


async def fetch_image(raw_url: str, settings: Dict[str, Any], *, resolver: Optional[Resolver] = None,
                      transport: Optional[httpx.AsyncBaseTransport] = None) -> Fetched:
    """The image bytes behind a link (not yet decoded - the caller runs `imaging.sanitize`), or LinkUnreadable."""
    try:
        target = resolve_link(raw_url)
    except LinkUnreadable:
        raise
    except Exception:  # noqa: BLE001 - see below
        raise LinkUnreadable("the link is not a valid web address") from None
    try:
        data = await asyncio.wait_for(_fetch(target, settings, resolver or system_resolver, transport),
                                      timeout=float(settings["link_total_timeout_seconds"]))
    except LinkUnreadable:
        raise
    except (asyncio.TimeoutError, httpx.TimeoutException):
        raise LinkUnreadable("the link did not answer in time") from None
    except httpx.HTTPError:
        raise LinkUnreadable("the link could not be opened") from None
    except Exception:  # noqa: BLE001 - anything else (a malformed address, an encoding error) is an unreadable link, never a 500
        raise LinkUnreadable("the link could not be opened") from None
    return Fetched(data=data, target=target)
