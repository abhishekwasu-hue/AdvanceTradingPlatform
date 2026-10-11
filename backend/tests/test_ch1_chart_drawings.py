"""CH1 (ADR-0023): chart drawings - the engine-neutral `drawing/1` schema (anchors per kind, time/price only), per-user
storage with optimistic versions, locks, soft delete, the per-symbol cap, and all-or-nothing export/import."""
import pytest
from pydantic import ValidationError

from app.charts import drawings
from app.charts.drawings import ANCHORS, Drawing
from tests.test_auth_api import client
from tests.test_phase_l_ai import _owner

T1, T2, T3 = "2026-03-02T04:00:00Z", "2026-03-03T05:15:00Z", "2026-03-04T06:30:00Z"


def _trend(p1=100.0, p2=110.0):
    return {"kind": "trendline", "anchors": [{"t": T1, "p": p1}, {"t": T2, "p": p2}], "style": {"color": "#ff0000", "width": 2}}


def test_every_kind_has_an_anchor_rule_and_the_schema_enforces_it():
    samples = {
        "hline": [{"p": 101.5}], "vline": [{"t": T1}], "text": [{"t": T1, "p": 99.0}],
        "long_position": [{"t": T1, "p": 100.0}, {"p": 95.0}, {"p": 112.0}],
    }
    for kind, need in ANCHORS.items():
        anchors = samples.get(kind) or [{"t": t, "p": 100.0 + i} for i, t in enumerate((T1, T2, T3)[:len(need)])]
        d = Drawing.model_validate({"kind": kind, "anchors": anchors, **({"text": "note"} if kind == "text" else {})})
        assert d.to_json()["schema"] == "drawing/1"
    for bad, words in (({"kind": "trendline", "anchors": [{"t": T1, "p": 1.0}]}, "needs 2 anchor"),
                       ({"kind": "hline", "anchors": [{"t": T1}]}, "needs a price"),
                       ({"kind": "vline", "anchors": [{"p": 1.0}]}, "needs a time"),
                       ({"kind": "text", "anchors": [{"t": T1, "p": 1.0}], "text": "<script>"}, "may not contain"),
                       ({"kind": "trendline", "anchors": [{"t": T1, "p": 1.0}, {"t": T2, "p": 2.0}], "levels": [0.5]}, "Fibonacci"),
                       ({"kind": "trendline", "anchors": [{"t": T1, "p": 1.0}, {"t": T2, "p": 2.0}], "x": 1}, "Extra inputs"),
                       ({"kind": "rectangle", "anchors": [{"t": T1, "p": 1.0}, {"t": T2, "p": 2.0}], "style": {"color": "red"}}, "pattern"),
                       ({"kind": "pixel_box", "anchors": [{"t": T1, "p": 1.0}]}, "kind")):
        with pytest.raises(ValidationError, match=words):
            Drawing.model_validate(bad)
    naive = Drawing.model_validate({"kind": "vline", "anchors": [{"t": "2026-03-02T09:30:00"}]})
    assert naive.anchors[0].t.tzinfo is not None                                              # times are stored in UTC


def test_storage_is_per_user_versioned_lockable_and_soft_deleted():
    me, _ = _owner("ch1-me@example.com")
    other, _ = _owner("ch1-other@example.com")
    made = client.post("/api/charts/drawings", json={"symbol": "nifty 50", "drawing": _trend()}, headers=me)
    assert made.status_code == 201, made.text
    d = made.json()
    assert d["symbol"] == "NIFTY 50" and d["version"] == 1 and d["drawing"]["anchors"][0]["p"] == 100.0
    assert client.get("/api/charts/drawings?symbol=NIFTY%2050", headers=other).json() == []           # per user
    assert client.put(f"/api/charts/drawings/{d['id']}", json={"version": 1, "drawing": _trend()}, headers=other).status_code == 404
    v2 = client.put(f"/api/charts/drawings/{d['id']}", json={"version": 1, "drawing": _trend(101, 111)}, headers=me).json()
    assert v2["version"] == 2 and v2["drawing"]["anchors"][1]["p"] == 111.0
    stale = client.put(f"/api/charts/drawings/{d['id']}", json={"version": 1, "drawing": _trend(5, 6)}, headers=me)
    assert stale.status_code == 409 and stale.json()["detail"]["current"]["version"] == 2           # a stale tab never overwrites
    assert client.post(f"/api/charts/drawings/{d['id']}/lock", json={"locked": True}, headers=me).json()["locked"] is True
    assert client.put(f"/api/charts/drawings/{d['id']}", json={"version": 2, "drawing": _trend()}, headers=me).status_code == 423
    assert client.delete(f"/api/charts/drawings/{d['id']}?version=2", headers=me).status_code == 423
    client.post(f"/api/charts/drawings/{d['id']}/lock", json={"locked": False}, headers=me)
    assert client.delete(f"/api/charts/drawings/{d['id']}?version=1", headers=me).status_code == 409
    assert client.delete(f"/api/charts/drawings/{d['id']}?version=2", headers=me).json() == {"deleted": d["id"]}
    assert client.get("/api/charts/drawings?symbol=NIFTY 50", headers=me).json() == []
    assert client.post("/api/charts/drawings", json={"symbol": "NIFTY;DROP", "drawing": _trend()}, headers=me).status_code == 422
    assert client.get("/api/charts/drawings?symbol=NIFTY 50").status_code == 401


def test_export_import_round_trip_is_all_or_nothing(monkeypatch):
    me, _ = _owner("ch1-io@example.com")
    for p in (100.0, 120.0):
        client.post("/api/charts/drawings", json={"symbol": "BANKNIFTY", "drawing": _trend(p, p + 5)}, headers=me)
    client.post("/api/charts/drawings", json={"symbol": "BANKNIFTY", "drawing": {"kind": "hline", "anchors": [{"p": 50000.5}]}}, headers=me)
    doc = client.get("/api/charts/drawings/export?symbol=banknifty", headers=me).json()
    assert doc["format"] == "atp-drawings/1" and len(doc["drawings"]) == 3
    doc["symbol"] = "FINNIFTY"
    out = client.post("/api/charts/drawings/import", json=doc, headers=me)
    assert out.status_code == 201 and out.json()["imported"] == 3
    again = client.get("/api/charts/drawings/export?symbol=FINNIFTY", headers=me).json()
    assert again["drawings"] == doc["drawings"]                                                    # lossless round trip
    bad = {**doc, "symbol": "MIDCPNIFTY", "drawings": doc["drawings"] + [{"kind": "trendline", "anchors": [{"t": T1, "p": 1.0}]}]}
    assert client.post("/api/charts/drawings/import", json=bad, headers=me).status_code == 422
    assert client.get("/api/charts/drawings?symbol=MIDCPNIFTY", headers=me).json() == []           # nothing half-imported
    monkeypatch.setattr(drawings, "MAX_PER_SYMBOL", 4)
    assert client.post("/api/charts/drawings/import", json=doc, headers=me).status_code == 409     # FINNIFTY has 3 + 3 > 4
    assert client.post("/api/charts/drawings", json={"symbol": "FINNIFTY", "drawing": _trend()}, headers=me).status_code == 201
    assert client.post("/api/charts/drawings", json={"symbol": "FINNIFTY", "drawing": _trend()}, headers=me).status_code == 409
