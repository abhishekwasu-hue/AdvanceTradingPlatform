"""P0.2 / S5: where the platform may send HTTP on a tenant's behalf (alert webhooks, SMS gateways, push
services). A tenant-supplied URL is a classic SSRF vector: `http://169.254.169.254/` (cloud metadata),
`http://postgres:5432/`, a hostname that resolves to 127.0.0.1 after the save-time check (DNS rebinding).

Two checks, both broker-neutral:

* `check_url_literal(url)` at configuration time - the scheme, a literal private/loopback address, and the
  optional `EGRESS_ALLOWED_HOSTS` allowlist. No DNS, so a save never depends on the resolver.
* `check_url_resolved(url)` right before every request - resolves the host and refuses any address that is
  not public (IPv4 and IPv6). Enforced in production/staging; a dev/test box keeps talking to localhost.
"""
import asyncio
import ipaddress
import socket
from typing import Iterable, List, Optional
from urllib.parse import urlsplit

from app.core.config import EGRESS_ALLOWED_HOSTS, ENVIRONMENT, HARDENED_ENVIRONMENTS

LOCAL_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost"}


class EgressBlocked(ValueError):
    pass


def strict(environment: Optional[str] = None) -> bool:
    return (environment or ENVIRONMENT) in HARDENED_ENVIRONMENTS


def is_public_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified)


def host_allowed(host: str, allowlist: Optional[Iterable[str]] = None) -> bool:
    """True when no allowlist is set, or the host is on it (exact, or under a `.suffix` entry)."""
    entries = list(EGRESS_ALLOWED_HOSTS if allowlist is None else allowlist)
    if not entries:
        return True
    host = host.lower().rstrip(".")
    return any(host == e or (e.startswith(".") and host.endswith(e)) or host.endswith("." + e) for e in entries)


def check_url_literal(url: str, *, environment: Optional[str] = None, allow_local_http: bool = True) -> None:
    """Raise EgressBlocked for a destination that is wrong on its face."""
    parts = urlsplit(str(url))
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        raise EgressBlocked("the URL must be http(s) with a host")
    local = host in LOCAL_NAMES or (not is_public_address(host) and _looks_like_ip(host))
    if local:
        if strict(environment) or not allow_local_http:
            raise EgressBlocked("a private, loopback or link-local destination is not allowed")
    elif parts.scheme != "https":
        raise EgressBlocked("the URL must use https")
    if not host_allowed(host):
        raise EgressBlocked(f"{host} is not on EGRESS_ALLOWED_HOSTS")


def _looks_like_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def resolve(host: str) -> List[str]:
    """Every address the host resolves to (blocking; callers use `check_url_resolved` from async code)."""
    if _looks_like_ip(host):
        return [host.strip("[]")]
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise EgressBlocked(f"{host} does not resolve ({exc.errno})") from exc
    return sorted({info[4][0] for info in infos})


async def check_url_resolved(url: str, *, environment: Optional[str] = None, resolver=None) -> None:
    """Resolve and refuse a non-public destination. Outside the hardened environments only the literal
    checks apply (a developer's webhook on localhost keeps working)."""
    check_url_literal(url, environment=environment)
    if not strict(environment):
        return
    host = (urlsplit(str(url)).hostname or "").lower()
    addresses = await asyncio.to_thread(resolver or resolve, host)
    bad = [a for a in addresses if not is_public_address(a)]
    if bad or not addresses:
        raise EgressBlocked(f"{host} resolves to a non-public address")
