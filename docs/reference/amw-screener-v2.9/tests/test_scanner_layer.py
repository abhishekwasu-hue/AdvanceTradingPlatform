"""Universal scanner layer: envelope validation, templates, deterministic NL builder (incl. the master-prompt
example), save/version/duplicate, point-in-time replay (future bars cannot change the decision), version comparison,
tenant isolation. Fixture provider only (SIMULATED data)."""
import pandas as pd
import pytest

from src.data.fixture_provider import FixtureProvider
from src.scanner import nl, replay, spec, templates
from src.services.app_services import Services

SIMPLE = {"ir_version": 1, "long": {"type": "group", "logic": "AND", "children": [
    {"type": "rule", "name": "close > EMA20", "expr": {"t": "cmp", "op": ">", "a": {"t": "field", "name": "close"},
                                                     "b": {"t": "field", "name": "ema20"}}}]},
    "short": None, "stop": {"method": "atr", "mult": 1.5}, "target": {"r": 2.0}}


@pytest.fixture
def svc(tmp_path):
    return Services(db_url=f"sqlite:///{tmp_path}/sc.db", provider=FixtureProvider(end="2026-10-16"))


def test_envelope_validation():
    assert spec.build("x", SIMPLE)["errors"] == []
    assert any("HIGHER" in e for e in spec.build("x", SIMPLE, {"timeframe": "1W", "htf": ["1D"]})["errors"])
    assert any("at_time" in e for e in spec.build("x", SIMPLE, {"frequency": "daily"})["errors"])
    assert any("intraday" in e for e in spec.build("x", SIMPLE, {"session": ("09:30", "15:00")})["errors"])
    assert any("incomplete_candle_policy" in e for e in spec.build("x", SIMPLE, {"incomplete_candle_policy": "include"})["errors"])
    htf_ir = {**SIMPLE, "long": {"type": "group", "logic": "AND", "children": [
        {"type": "rule", "name": "w", "expr": {"t": "pred", "name": "htf_trend_is", "args": {"tf": "1W", "label": "Bullish"}}}]}}
    assert any("not in envelope.htf" in e for e in spec.build("x", htf_ir)["errors"])
    assert spec.build("x", htf_ir, {"htf": ["1W"]})["errors"] == []


def test_all_templates_are_valid_and_documented():
    lst = templates.listing()
    assert len(lst) >= 15 and {t["category"] for t in lst} == set(templates.CATEGORIES)
    for t in lst:
        assert t["valid"], (t["id"], t["errors"])
        assert t["limitations"] and "NOT VALIDATED" in t["evidence"]


def test_nl_builder_master_prompt_example():
    r = nl.parse("Find Indian stocks with a bullish daily trend, price above EMA 50, confirmed 15-minute higher low, "
                 "bullish RSI divergence, and relative volume above 1.5.")
    assert r["envelope"]["timeframe"] == "15m" and r["envelope"]["htf"] == ["1D"]
    names = [c["expr"]["name"] if c["expr"]["t"] == "pred" else c["expr"]["t"] for c in r["ir"]["long"]["children"]]
    assert names == ["htf_trend_is", "cmp", "last_swing_label", "rsi_divergence", "cmp"]
    assert any("RSI(14) divergence" in n and "assumption" in n for n in r["interpretations"])
    assert r["unsupported"] == [] and r["errors"] == [] and r["valid"]


def test_nl_builder_reports_unsupported_instead_of_guessing():
    r = nl.parse("daily stocks above ema 20 with elliott wave 3 and harmonic gartley")
    assert len(r["unsupported"]) == 2 and not r["valid"]
    assert [c["name"] for c in r["ir"]["long"]["children"]] == ["daily stocks above ema 20"]
    assert nl.parse("make me rich")["errors"]                                        # nothing recognised
    s = nl.parse("bearish weekly trend and a bearish engulfing on daily")
    assert s["ir"]["short"] and s["envelope"] == {"timeframe": "1D", "htf": ["1W"], "universe": "Nifty 50"}


def test_save_version_duplicate_and_isolation(svc):
    u = svc.local_user
    other, _ = svc.store.create_user("other")
    r = spec.save(svc, u, "EMA scan", SIMPLE, {"universe": "Nifty 50"})
    assert r["version"] == 1 and "close > EMA20" in r["readable"]
    r2 = spec.save(svc, u, "EMA scan", {**SIMPLE, "target": {"r": 3.0}}, sid=r["scanner_id"], note="3R")
    assert r2["version"] == 2 and len(svc.store.lab_versions(u, r["scanner_id"])) == 2
    assert svc.store.lab_versions(u, r["scanner_id"])[-1]["ir"]["target"] == {"r": 2.0}    # v1 immutable
    d = spec.duplicate(svc, u, r["scanner_id"], "EMA scan copy")
    assert svc.store.lab_get(u, d)["draft_spec"]["scanner"]["name"] == "EMA scan copy"
    with pytest.raises(KeyError):
        spec.duplicate(svc, other, r["scanner_id"], "steal")
    with pytest.raises(ValueError):
        spec.save(svc, u, "bad", {"ir_version": 1, "long": None, "short": None})


def test_replay_is_point_in_time(svc, monkeypatch):
    u = svc.local_user
    sid = spec.save(svc, u, "EMA", SIMPLE)["scanner_id"]
    as_of = "2026-09-15 16:00"
    a = replay.replay(svc, u, sid, as_of, ["A.NS", "B.NS", "C.NS"])
    assert a["evaluated"] == 3 and all(x["signal_bar"] <= "2026-09-15 23:59" for x in a["results"] if "signal_bar" in x)
    assert all(c["outcome"] in ("PASS", "FAIL", "UNKNOWN") for x in a["results"] for c in x["conditions"])
    # mutate every bar AFTER as_of: the original decision must not change (no leakage)
    real = svc._provider.fetch_many

    def tampered(tickers, tf):
        out = real(tickers, tf)
        for k, df in out.items():
            df = df.copy()
            after = df.index > pd.Timestamp("2026-09-16")
            df.loc[after, ["Open", "High", "Low", "Close"]] = df.loc[after, ["Open", "High", "Low", "Close"]] * 3
            out[k] = df
        return out
    monkeypatch.setattr(svc._provider, "fetch_many", tampered)
    b = replay.replay(svc, u, sid, as_of, ["A.NS", "B.NS", "C.NS"])
    strip = lambda r: [(x["ticker"], x["status"], x.get("stop"), [c["outcome"] for c in x["conditions"]]) for x in r["results"]]  # noqa: E731
    assert strip(a) == strip(b)
    assert "NOT part of the decision" in a["method"]


def test_replay_owner_scoped_and_requires_published(svc):
    u = svc.local_user
    other, _ = svc.store.create_user("o")
    sid = spec.save(svc, u, "EMA", SIMPLE, publish=False)["scanner_id"]
    with pytest.raises(ValueError):
        replay.replay(svc, u, sid, "2026-09-15", ["A.NS"])                          # drafts never execute
    spec.save(svc, u, "EMA", SIMPLE, sid=sid)
    with pytest.raises(KeyError):
        replay.replay(svc, other, sid, "2026-09-15", ["A.NS"])


def test_version_comparison_same_data(svc):
    u = svc.local_user
    sid = spec.save(svc, u, "EMA", SIMPLE)["scanner_id"]
    tighter = {**SIMPLE, "long": {"type": "group", "logic": "AND", "children": SIMPLE["long"]["children"] + [
        {"type": "rule", "name": "RSI > 60", "expr": {"t": "cmp", "op": ">", "a": {"t": "field", "name": "rsi14"},
                                                    "b": {"t": "num", "v": 60}}}]}}
    spec.save(svc, u, "EMA", tighter, sid=sid)
    c = replay.compare_versions(svc, u, sid, 1, 2, ["A.NS", "B.NS"])
    assert set(c["versions"]) == {"v1", "v2"} and c["versions"]["v2"]["trades"] <= c["versions"]["v1"]["trades"]
    assert "No winner is declared" in c["verdict"] and c["symbols"] == ["A.NS", "B.NS"]
    with pytest.raises(ValueError):
        replay.compare_versions(svc, u, sid, 1, 1, ["A.NS"])


def test_scanner_api_flow_and_isolation(svc, monkeypatch):
    from fastapi.testclient import TestClient
    from src.api.main import create_app
    monkeypatch.setenv("AMW_AUTH_MODE", "token")
    ua, ta = svc.store.create_user("sa")
    ub, tb = svc.store.create_user("sb")
    c = TestClient(create_app(svc))
    A, B = {"Authorization": f"Bearer {ta}"}, {"Authorization": f"Bearer {tb}"}
    assert len(c.get("/api/v1/scanner/templates", headers=A).json()) >= 15
    p = c.post("/api/v1/scanner/nl", json={"text": "daily close above ema 20 and rsi above 50"}, headers=A).json()
    assert p["valid"] and p["envelope"]["timeframe"] == "1D"
    r = c.post("/api/v1/scanners", json={"name": "NL", "ir": p["ir"], "envelope": p["envelope"]}, headers=A).json()
    sid = r["scanner_id"]
    assert r["version"] == 1
    rp = c.post(f"/api/v1/scanners/{sid}/replay", json={"as_of": "2026-09-15", "symbols": ["a.ns"]}, headers=A).json()
    assert rp["evaluated"] == 1 and rp["results"][0]["ticker"] == "A.NS"
    assert c.post(f"/api/v1/scanners/{sid}/replay", json={"as_of": "2026-09-15", "symbols": ["A.NS"]}, headers=B).status_code == 404
    assert c.post(f"/api/v1/scanners/{sid}/duplicate?name=x", headers=B).status_code == 404
    bad = c.post("/api/v1/scanners", json={"name": "bad", "ir": {"ir_version": 1, "long": None, "short": None}}, headers=A)
    assert bad.status_code == 422


def test_share_sends_a_copy_of_rules_only(svc):
    from src.scanner import spec as S
    u = svc.local_user
    bob, _ = svc.store.create_user("bob")
    sid = S.save(svc, u, "EMA", SIMPLE, {"alerts": {"enabled": True}})["scanner_id"]
    new = S.share(svc, u, sid, "bob")
    got = svc.store.lab_get(bob, new)
    assert got["name"] == "[shared] EMA" and got["draft_ir"] == SIMPLE and svc.store.lab_versions(bob, new) == []
    assert got["draft_spec"]["scanner"]["alerts"]["enabled"] is False                 # recipient opts in themselves
    assert svc.store.lab_get(u, new) is None and svc.store.signal_history(bob) == []
    with pytest.raises(KeyError):
        S.share(svc, bob, sid, "local")                                                # cannot share a foreign scanner
    with pytest.raises(KeyError):
        S.share(svc, u, sid, "nobody")
    assert any(r["action"] == "scanner_share" for r in svc.store.audit_rows(20))
