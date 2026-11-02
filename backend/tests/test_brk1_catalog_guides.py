"""BRK-1 C: every broker in the connect catalogue has a visible step-by-step guide (docs/brokers/EASIEST_LOGIN.md).

- at least two numbered steps, each one short line ending with a full stop and with balanced bold marks;
- every step on the broker's side links to an https page on that broker's own official domain;
- every field the panel asks for is produced by a step (the panel says "From step N"); no step names a field the panel
  does not ask for;
- a value to copy INTO the broker is only ATP's redirect address or its static IP;
- the same one SEBI info line on every broker; warnings are one line each, and none says "practice".
"""
from __future__ import annotations

import uuid
from urllib.parse import urlparse

from app.brokers import catalog as cat
from app.brokers.registry import available_brokers
from tests.test_auth_api import _register, client

# The brokers' own domains (official docs / consoles, docs/brokers/EASIEST_LOGIN.md).
OFFICIAL_DOMAINS = {
    "upstox": ("upstox.com",),
    "dhan": ("dhan.co", "dhanhq.co"),
    "zerodha": ("kite.trade", "zerodha.com"),
    "fyers": ("fyers.in",),
    "angel_one": ("angelone.in",),
    "shoonya": ("shoonya.com",),
    "kotak_neo": ("kotakneo.com",),
    "groww": ("groww.in",),
    "coindcx": ("coindcx.com",),
}


def _official(broker: str, url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in OFFICIAL_DOMAINS[broker])


def test_every_catalog_entry_has_a_guide_with_official_links_and_every_field_produced():
    rows = cat.catalog()
    assert {r["broker"] for r in rows} == set(OFFICIAL_DOMAINS) == set(available_brokers())
    infos = set()
    for row in rows:
        guide = row["guide"]
        assert set(guide) == {"warnings", "info", "steps"}, row["broker"]
        steps = guide["steps"]
        assert len(steps) >= 2, row["broker"]
        broker_side = [s for s in steps if s["where"] == "broker"]
        assert broker_side, row["broker"]
        for s in steps:
            assert set(s) == {"text", "url", "gives", "copy", "where"} and s["where"] in ("broker", "atp")
            assert s["text"].endswith(".") and s["text"].count("**") % 2 == 0 and len(cat.plain(s["text"])) <= 110, s
            if s["where"] == "broker":
                assert s["url"] and urlparse(s["url"]).scheme == "https" and _official(row["broker"], s["url"]), (row["broker"], s)
            else:
                assert s["url"] is None and s["copy"] is None, s
            assert s["copy"] in (None, cat.COPY_REDIRECT_URL, cat.COPY_STATIC_IP), s
        produced = [s["gives"] for s in steps if s["gives"]]
        keys = [f["key"] for f in row["fields"]]
        assert set(keys) <= set(produced), (row["broker"], set(keys) - set(produced))
        assert set(produced) <= set(keys), (row["broker"], set(produced) - set(keys))
        assert len(produced) == len(set(produced)), row["broker"]               # one step per field
        for w in guide["warnings"]:
            assert w.endswith(".") and len(cat.plain(w)) <= 110 and "practice" not in w.lower(), w
        assert "practice" not in " ".join(s["text"] for s in steps).lower()
        assert row["how_to"] == [cat.plain(s["text"]) for s in steps]
        infos.add(guide["info"])
    assert infos == {cat.SEBI_INFO}                                              # one identical line on every broker
    assert "static IP" in cat.SEBI_INFO and "market orders" in cat.SEBI_INFO


def test_the_guide_is_served_by_the_catalog_route():
    bearer = _register(f"brk1-guide-{uuid.uuid4().hex[:8]}@example.com")
    rows = client.get("/api/broker/catalog", headers={"Authorization": f"Bearer {bearer}"}).json()
    assert [r["guide"] for r in rows] == [r["guide"] for r in cat.catalog()]
    fyers = next(r for r in rows if r["broker"] == "fyers")
    assert any(s["copy"] == "redirect_url" for s in fyers["guide"]["steps"])
