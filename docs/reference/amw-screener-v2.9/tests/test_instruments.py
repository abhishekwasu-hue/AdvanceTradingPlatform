"""Instrument master: parsed only from official-format NSE CSVs (ISIN validated), merge across lists keeps the first
ISIN and records every source, resolve by ticker / symbol / ISIN / unique company-name prefix, ambiguity not guessed,
universe loads keep the master in sync, API search/resolve."""
import pandas as pd

from src.data import instruments as I
from src.storage.db import Store

EQUITY_L = """SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE
RELIANCE,Reliance Industries Limited,EQ,29-NOV-1995,10,1,INE002A01018,10
TCS,Tata Consultancy Services Limited,EQ,25-AUG-2004,1,1,INE467B01029,1
TATASTEEL,Tata Steel Limited,EQ,01-JAN-1990,1,1,INE081A01020,1
M&M,Mahindra & Mahindra Limited,EQ,01-JAN-1990,5,1,BADISIN,5
"""


def test_parse_merge_and_resolve(tmp_path):
    s = Store(f"sqlite:///{tmp_path}/i.db")
    rows = I.from_csv(EQUITY_L)
    assert len(rows) == 4 and rows[3]["isin"] is None                              # invalid ISIN not stored
    assert s.instruments_upsert(rows, "EQUITY_L") == 4
    uni = pd.DataFrame({"symbol": ["RELIANCE"], "ticker": ["RELIANCE.NS"], "company": ["Reliance Industries"],
                        "industry": ["Oil Gas"]})
    assert I.upsert_universe(s, uni, "Nifty 50") == 0                               # existing row enriched, not duplicated
    rel = I.resolve(s, "reliance")
    assert rel["ticker"] == "RELIANCE.NS" and rel["candidates"][0]["industry"] == "Oil Gas"
    assert set(rel["candidates"][0]["sources"].split("|")) == {"EQUITY_L", "Nifty 50"}
    assert rel["candidates"][0]["isin"] == "INE002A01018"
    assert I.resolve(s, "INE467B01029")["ticker"] == "TCS.NS"
    assert I.resolve(s, "Tata Consultancy")["ticker"] == "TCS.NS"
    amb = I.resolve(s, "Tata")
    assert amb["ticker"] is None and amb["match"] == "ambiguous" and len(amb["candidates"]) == 2
    assert I.resolve(s, "XYZNOTREAL")["match"] == "not found"
    assert {r["ticker"] for r in I.search(s, "mahindra")} == {"M&M.NS"}


def test_universe_load_syncs_master_and_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from src.api.main import create_app
    from src.data.fixture_provider import FixtureProvider
    from src.services.app_services import Services
    import src.services.app_services as AS
    from src.data.universe import Universe
    svc = Services(db_url=f"sqlite:///{tmp_path}/u.db", provider=FixtureProvider(end="2026-10-16"))
    df = pd.DataFrame({"symbol": ["INFY", "SBIN"], "ticker": ["INFY.NS", "SBIN.NS"], "company": ["Infosys Ltd", "State Bank"],
                       "industry": ["IT", "Banks"]})
    monkeypatch.setattr(AS, "load_universe", lambda *a, **k: Universe("Nifty 50", df, "Local file test.csv", "now", False, []))
    svc.universe("Nifty 50")
    assert svc.store.instruments_count() == 2
    fb = Universe("Nifty 50", df.assign(ticker=["X.NS", "Y.NS"]), "FALLBACK", "now", True, [])
    monkeypatch.setattr(AS, "load_universe", lambda *a, **k: fb)
    svc.universe("Nifty 50")
    assert svc.store.instruments_count() == 2                                        # fallback lists are not imported
    monkeypatch.setenv("AMW_AUTH_MODE", "local")
    c = TestClient(create_app(svc))
    assert c.get("/api/v1/instruments/resolve?text=infosys").json()["ticker"] == "INFY.NS"
    assert [r["ticker"] for r in c.get("/api/v1/instruments?q=bank").json()] == ["SBIN.NS"]
