"""N3 S8c (spec §2.4 category I): one engine per venue with a field-availability matrix (flag screener_venues, off).

A screen run on a venue whose data lacks a field or a data family is refused with where it is available (flag on);
off, runs behave as before. GET /api/screener/venues gives the matrix."""
import json
import uuid

from app.platform.controls import DEFAULT_OFF_FLAGS
from app.screener import compile_screen
from app.screener import venues as V
from app.screener.registry import FIELDS, FUNCTIONS
from tests.test_auth_api import client
from tests.test_n1_e1_execution_core import _flag
from tests.test_trading_worker import _tenant


def _ast(text):
    ast, _ = compile_screen(text, base_tf="1d")
    assert ast is not None
    return ast


def test_the_flag_is_off_by_default():
    assert V.FLAG in DEFAULT_OFF_FLAGS


def test_what_a_venue_lacks_is_named_with_where_it_is():
    assert V.unavailable(_ast("close > SMA(close, 20) AND volume > 1000"), "NSE") == []
    [oi] = V.unavailable(_ast("oi > 0 AND oi > oi[1]"), "NSE")
    assert oi.startswith("oi: NSE cash equities data has no oi") and "NFO, BFO, MCX, CDS" in oi
    assert V.unavailable(_ast("oi > 0"), "MCX") == []
    [own] = V.unavailable(_ast('Holding("promoter") > 50'), "MCX")
    assert own.startswith("Holding(): MCX commodities data has no ownership") and "(it is on NSE)" in own
    assert V.unavailable(_ast('Holding("promoter") > 50'), "NSE") == []
    [ref] = V.unavailable(_ast('Sector() == "IT"'), "NFO")
    assert "no reference" in ref and "NSE, BSE" in ref
    assert V.unavailable(_ast("oi > 0"), "NOWHERE") == [] and V.unavailable(None, "NSE") == []


def test_the_matrix_covers_every_registry_entry_on_every_venue():
    out = V.matrix()
    assert {v["code"] for v in out["venues"]} == {"NSE", "BSE", "NFO", "BFO", "MCX", "CDS", "CRYPTO"}
    for v in out["venues"]:
        assert set(v["functions"]) | set(v["unavailable"]) == set(FUNCTIONS) and not set(v["functions"]) & set(v["unavailable"])
        assert set(v["fields"]) <= set(FIELDS)
    nse = next(v for v in out["venues"] if v["code"] == "NSE")
    mcx = next(v for v in out["venues"] if v["code"] == "MCX")
    assert "Holding" in nse["functions"] and "Holding" in mcx["unavailable"] and "oi" in mcx["fields"] and "oi" not in nse["fields"]
    assert next(v for v in out["venues"] if v["code"] == "CRYPTO")["runnable"] is False
    assert set(out["families"]) >= {"ownership", "reference", "option_chain", "zones", "rankings"}
    assert all(v.runnable for v in V.VENUES if v.code != "CRYPTO") and V.venue_of("bfo").code == "BFO"


def _org(tag, on=True):
    t = _tenant(f"n3s8c-{tag}-{uuid.uuid4().hex[:6]}@example.com", with_credentials=False)
    for name in ("screener_v2",) + ((V.FLAG,) if on else ()):
        _flag(name, t["tenant_id"])
    return t


def test_the_routes_follow_the_flag():
    off, on = _org("off", on=False), _org("on")
    r = client.get("/api/screener/venues", headers=off["headers"])
    assert r.status_code == 503 and r.headers.get("X-Feature-Disabled") == V.FLAG
    assert client.get("/api/screener/venues", headers=on["headers"]).json() == V.matrix()
    body = {"source": "oi > 0", "exchange": "NSE"}
    assert client.post("/api/screener/validate", headers=off["headers"], json=body).json()["ok"]          # off: as before
    checked = client.post("/api/screener/validate", headers=on["headers"], json=body).json()
    assert not checked["ok"] and "NSE cash equities data has no oi" in json.dumps(checked["problems"])
    assert client.post("/api/screener/validate", headers=on["headers"], json={**body, "exchange": "NFO"}).json()["ok"]
    assert client.post("/api/screener/validate", headers=on["headers"], json={"source": "oi > 0"}).json()["ok"]   # no venue named
    run = client.post("/api/screener/run", headers=on["headers"], json={"source": "oi > 0", "symbols": ["X"], "exchange": "NSE"})
    assert run.status_code == 422 and "has no oi" in run.text
