"""Analyze a chart: the SSRF-safe link fetch (app/chart_analyze/linkfetch.py). No real network: every test passes a fake
DNS resolver and an httpx.MockTransport, and records which requests went out."""
import asyncio
from typing import Dict, List

import httpx
import pytest

from app.chart_analyze import linkfetch, settings as settings_mod
from tests.chart_analyze_fakes import synthetic_chart

SETTINGS = settings_mod.merge({})
PNG = synthetic_chart()
JPEG = synthetic_chart("JPEG")
# Public addresses (the fetch refuses the documentation ranges, which are not global); the resolver below is fake and
# the transport is a mock, so nothing is ever contacted.
PUBLIC = "8.8.8.8"
PUBLIC_2 = "1.1.1.1"


def _resolver(table: Dict[str, List[str]]):
    async def resolve(host: str) -> List[str]:
        return list(table.get(host, []))
    return resolve


def _run(url: str, handler, table: Dict[str, List[str]], **over):
    sent: List[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return handler(request)

    settings = {**SETTINGS, **over}
    coro = linkfetch.fetch_image(url, settings, resolver=_resolver(table), transport=httpx.MockTransport(record))
    return asyncio.run(coro), sent


def _refused(url: str, handler, table: Dict[str, List[str]], **over) -> List[httpx.Request]:
    sent: List[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return handler(request)

    with pytest.raises(linkfetch.LinkUnreadable):
        asyncio.run(linkfetch.fetch_image(url, {**SETTINGS, **over}, resolver=_resolver(table), transport=httpx.MockTransport(record)))
    return sent


def _body(*parts: bytes):
    """A streamed body, as a real connection delivers it (an in-memory body would be read before the code streams it)."""
    async def chunks():
        for part in parts:
            yield part
    return chunks()


def _image(data: bytes = PNG, ctype: str = "image/png", **headers: str):
    return lambda request: httpx.Response(200, content=_body(data), headers={"content-type": ctype, **headers})


def never(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"no request may be sent, got {request.url}")


# --- what each link means --------------------------------------------------------------------------------------------

def test_tradingview_snapshot_link_reads_the_snapshot_image_pinned_to_the_checked_address():
    fetched, sent = _run("https://www.tradingview.com/x/AbCd1234/", _image(), {"s3.tradingview.com": [PUBLIC]})
    assert fetched.data == PNG and fetched.target.kind == "tradingview" and fetched.target.source_host == "www.tradingview.com"
    assert len(sent) == 1
    request = sent[0]
    assert request.url.host == PUBLIC                                  # connected to the address that was checked
    assert request.url.path == "/snapshots/a/AbCd1234.png"
    assert request.headers["host"] == "s3.tradingview.com"             # the real name in Host ...
    assert request.extensions.get("sni_hostname") == "s3.tradingview.com"  # ... and as the TLS name
    assert request.headers["accept-encoding"] == "identity"


def test_direct_image_link_png_and_jpeg():
    fetched, _ = _run("https://img.example.com/chart.png?x=1", _image(), {"img.example.com": [PUBLIC]})
    assert fetched.data == PNG and fetched.target.kind == "direct"
    fetched, _ = _run("https://img.example.com/chart.jpg", _image(JPEG, "image/jpeg"), {"img.example.com": [PUBLIC]})
    assert fetched.data == JPEG


def test_a_live_tradingview_chart_page_is_refused_without_a_request():
    for url in ("https://www.tradingview.com/chart/AbCd1234/", "https://in.tradingview.com/symbols/NSE-NIFTY/",
                "https://www.tradingview.com/", "https://www.tradingview.com/x/"):
        assert _refused(url, never, {"www.tradingview.com": [PUBLIC], "in.tradingview.com": [PUBLIC]}) == []


@pytest.mark.parametrize("url", ["http://img.example.com/chart.png", "ftp://img.example.com/c.png", "https://user:pw@img.example.com/c.png",
                                 "https://img.example.com:8443/c.png", "javascript:alert(1)", "https://", "not a link",
                                 "https://img.example.com/" + "a" * 3000])
def test_bad_addresses_are_refused_without_a_request(url):
    assert _refused(url, never, {"img.example.com": [PUBLIC]}) == []


# --- private addresses: the initial host and every redirect hop --------------------------------------------------------

@pytest.mark.parametrize("address", ["127.0.0.1", "10.1.2.3", "172.16.0.9", "192.168.1.10", "169.254.169.254", "100.64.1.1", "0.0.0.0",
                                     "224.0.0.1", "240.0.0.1", "198.18.0.1", "::1", "fd00::1", "fe80::1", "fec0::1", "::ffff:10.0.0.1",
                                     "64:ff9b::a00:1", "2002:a00:1::1", "::"])
def test_a_host_resolving_to_a_non_public_address_is_refused_before_any_request(address):
    assert _refused("https://img.example.com/c.png", never, {"img.example.com": [address]}) == []


def test_one_bad_address_among_several_is_enough_to_refuse():
    assert _refused("https://img.example.com/c.png", never, {"img.example.com": [PUBLIC, "10.0.0.1"]}) == []


@pytest.mark.parametrize("literal", ["https://127.0.0.1/c.png", "https://[::1]/c.png", "https://169.254.169.254/latest/meta-data",
                                     "https://10.0.0.1/c.png"])
def test_private_ip_literals_are_refused(literal):
    assert _refused(literal, never, {}) == []


def test_unresolvable_host_is_refused():
    assert _refused("https://nowhere.example.com/c.png", never, {}) == []


def test_a_redirect_to_a_private_address_is_refused_and_never_requested():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["host"] == "img.example.com", "the private hop must never be requested"
        return httpx.Response(302, headers={"location": "https://internal.example.com/secret.png"})

    sent = _refused("https://img.example.com/c.png", handler, {"img.example.com": [PUBLIC], "internal.example.com": ["192.168.1.10"]})
    assert len(sent) == 1


def test_a_redirect_to_http_is_refused():
    sent = _refused("https://img.example.com/c.png", lambda r: httpx.Response(301, headers={"location": "http://img.example.com/c.png"}),
                    {"img.example.com": [PUBLIC]})
    assert len(sent) == 1


def test_a_safe_redirect_is_followed_and_pinned_again():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["host"] == "img.example.com":
            return httpx.Response(302, headers={"location": "https://cdn.example.net/real.png"})
        assert request.url.host == PUBLIC_2 and request.extensions.get("sni_hostname") == "cdn.example.net"
        return _image()(request)

    fetched, sent = _run("https://img.example.com/c.png", handler, {"img.example.com": [PUBLIC], "cdn.example.net": [PUBLIC_2]})
    assert fetched.data == PNG and len(sent) == 2


def test_redirects_are_capped():
    loop = lambda r: httpx.Response(302, headers={"location": "https://img.example.com/again.png"})  # noqa: E731
    sent = _refused("https://img.example.com/c.png", loop, {"img.example.com": [PUBLIC]}, link_max_redirects=2)
    assert len(sent) == 3


# --- what comes back -------------------------------------------------------------------------------------------------

def test_login_wall_html_page_error_and_non_image_are_refused():
    table = {"img.example.com": [PUBLIC], "login.example.com": [PUBLIC_2]}

    def login_wall(request: httpx.Request) -> httpx.Response:
        if request.headers["host"] == "img.example.com":
            return httpx.Response(302, headers={"location": "https://login.example.com/signin"})
        return httpx.Response(200, content=_body(b"<html><form>Sign in</form></html>"), headers={"content-type": "text/html"})

    _refused("https://img.example.com/c.png", login_wall, table)
    _refused("https://img.example.com/c.png", lambda r: httpx.Response(403, content=b"no"), table)
    _refused("https://img.example.com/c.png", lambda r: httpx.Response(401, content=b"no"), table)
    _refused("https://img.example.com/c.png", lambda r: httpx.Response(404, content=b"gone"), table)
    _refused("https://img.example.com/c.png", lambda r: httpx.Response(500, content=b"oops"), table)
    _refused("https://img.example.com/c.png", _image(b"<html>not an image</html>", "text/html"), table)
    _refused("https://img.example.com/c.png", _image(b"GIF89a....", "image/gif"), table)
    _refused("https://img.example.com/c.png", _image(b"<html>looks like png</html>", "image/png"), table)   # type says png, bytes do not
    _refused("https://img.example.com/c.png", _image(PNG, "image/png", **{"content-encoding": "gzip"}), table)


def test_the_specific_message_is_the_screenshot_one():
    assert linkfetch.SCREENSHOT_MESSAGE == "Please upload a screenshot instead"


def test_too_big_by_header_and_while_streaming():
    small = {"max_image_bytes": 1024}
    _refused("https://img.example.com/c.png", _image(PNG, **{"content-length": str(10 ** 9)}), {"img.example.com": [PUBLIC]}, **small)

    def streamed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_body(PNG[:8], *([b"\0" * 512] * 100)), headers={"content-type": "image/png"})

    _refused("https://img.example.com/c.png", streamed, {"img.example.com": [PUBLIC]}, **small)


def test_timeouts_are_refused():
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    _refused("https://img.example.com/c.png", slow, {"img.example.com": [PUBLIC]})

    def connect_fails(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    _refused("https://img.example.com/c.png", connect_fails, {"img.example.com": [PUBLIC]})


def test_the_total_time_limit_applies():
    async def slow_resolver(host: str) -> List[str]:
        await asyncio.sleep(5)
        return [PUBLIC]

    with pytest.raises(linkfetch.LinkUnreadable):
        asyncio.run(linkfetch.fetch_image("https://img.example.com/c.png", {**SETTINGS, "link_total_timeout_seconds": 0.2},
                                          resolver=slow_resolver, transport=httpx.MockTransport(never)))


def test_address_allowed_accepts_public_addresses_only():
    assert linkfetch.address_allowed(PUBLIC) and linkfetch.address_allowed(PUBLIC_2) and linkfetch.address_allowed("2606:4700:4700::1111")
    for bad in ("127.0.0.1", "10.0.0.1", "100.100.100.100", "192.0.2.1", "::1", "fd12::1", "2001:db8::1", "not-an-ip"):
        assert not linkfetch.address_allowed(bad)


def test_the_client_ignores_proxy_settings_from_the_environment(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    client = linkfetch.make_client(SETTINGS)
    try:
        assert client._trust_env is False and client.follow_redirects is False
    finally:
        asyncio.run(client.aclose())
