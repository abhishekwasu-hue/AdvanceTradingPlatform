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


# --- UI2-4a1: the timeframe a drawing was made on; "Delete all" for a symbol ------------------------------------------

def test_timeframe_is_stored_and_every_timeframe_still_lists_the_drawing():
    me, _ = _owner("ui24a1-tf@example.com")
    made = client.post("/api/charts/drawings", json={"symbol": "NIFTY 50", "exchange": "NSE_INDEX", "timeframe": "15min",
                                                     "drawing": _trend()}, headers=me)
    assert made.status_code == 201, made.text
    assert made.json()["timeframe"] == "15min" and made.json()["exchange"] == "NSE_INDEX"
    legacy = client.post("/api/charts/drawings", json={"symbol": "NIFTY 50", "exchange": "NSE_INDEX", "drawing": _trend()}, headers=me)
    assert legacy.status_code == 201 and legacy.json()["timeframe"] is None                       # old clients send none
    listed = client.get("/api/charts/drawings?symbol=NIFTY 50&exchange=NSE_INDEX", headers=me).json()
    assert [d["timeframe"] for d in listed] == ["15min", None]                                    # not filtered by timeframe
    for tf in drawings.TIMEFRAMES:
        assert client.post("/api/charts/drawings", json={"symbol": "TCS", "timeframe": tf, "drawing": _trend()}, headers=me).status_code == 201
    for bad in ("1h", "D", "2min", ""):
        assert client.post("/api/charts/drawings", json={"symbol": "TCS", "timeframe": bad, "drawing": _trend()}, headers=me).status_code == 422
    v2 = client.put(f"/api/charts/drawings/{made.json()['id']}", json={"version": 1, "drawing": _trend(1, 2)}, headers=me).json()
    assert v2["timeframe"] == "15min"                                                              # an edit keeps it


def test_delete_all_is_scoped_to_the_user_symbol_and_exchange_and_needs_confirm():
    me, _ = _owner("ui24a1-all@example.com")
    other_org, _ = _owner("ui24a1-all-other@example.com")

    def make(headers, symbol="INFY", exchange="NSE", timeframe="5min"):
        r = client.post("/api/charts/drawings", json={"symbol": symbol, "exchange": exchange, "timeframe": timeframe, "drawing": _trend()},
                        headers=headers)
        assert r.status_code == 201, r.text
        return r.json()["id"]

    mine = [make(me), make(me, timeframe="day"), make(me, timeframe="1min")]
    locked = make(me)
    client.post(f"/api/charts/drawings/{locked}/lock", json={"locked": True}, headers=me)
    other_symbol, other_exchange = make(me, "TCS"), make(me, "INFY", "BSE")
    theirs = make(other_org)

    assert client.delete("/api/charts/drawings?symbol=INFY", headers=me).status_code == 400            # no confirm
    assert client.delete("/api/charts/drawings?symbol=INFY&confirm=false", headers=me).status_code == 400
    assert len(client.get("/api/charts/drawings?symbol=INFY", headers=me).json()) == 4
    assert client.delete("/api/charts/drawings?symbol=INFY&confirm=true").status_code == 401

    out = client.delete("/api/charts/drawings?symbol=infy&exchange=NSE&confirm=true", headers=me)
    assert out.status_code == 200, out.text
    assert out.json() == {"deleted": 3, "kept_locked": 1, "ids": mine}                                 # every timeframe
    assert [d["id"] for d in client.get("/api/charts/drawings?symbol=INFY", headers=me).json()] == [locked]
    assert [d["id"] for d in client.get("/api/charts/drawings?symbol=TCS", headers=me).json()] == [other_symbol]
    assert [d["id"] for d in client.get("/api/charts/drawings?symbol=INFY&exchange=BSE", headers=me).json()] == [other_exchange]
    assert [d["id"] for d in client.get("/api/charts/drawings?symbol=INFY", headers=other_org).json()] == [theirs]
    # another user's "delete all" on the same symbol leaves mine alone
    assert client.delete("/api/charts/drawings?symbol=INFY&confirm=true", headers=other_org).json()["deleted"] == 1
    assert [d["id"] for d in client.get("/api/charts/drawings?symbol=INFY", headers=me).json()] == [locked]


def test_delete_all_never_deletes_a_drawing_locked_just_before():
    """The soft-delete is one UPDATE that re-checks `locked`: a lock made on another device after this tab listed the
    drawings (here: written straight to the database) is honoured."""
    import asyncio

    from app.db.models import ChartDrawingRecord
    from tests.test_auth_api import _session_factory

    me, _ = _owner("ui24a1-race@example.com")
    ids = [client.post("/api/charts/drawings", json={"symbol": "SBIN", "drawing": _trend()}, headers=me).json()["id"] for _ in range(2)]

    async def lock_elsewhere():
        async with _session_factory() as session:
            (await session.get(ChartDrawingRecord, ids[1])).locked = True
            await session.commit()
    asyncio.run(lock_elsewhere())
    out = client.delete("/api/charts/drawings?symbol=SBIN&confirm=true", headers=me).json()
    assert out == {"deleted": 1, "kept_locked": 1, "ids": [ids[0]]}
    listed = client.get("/api/charts/drawings?symbol=SBIN", headers=me).json()
    assert [(d["id"], d["locked"]) for d in listed] == [(ids[1], True)]


def test_export_import_carries_the_timeframe():
    me, _ = _owner("ui24a1-io@example.com")
    for tf in ("5min", None, "week"):
        body = {"symbol": "HDFCBANK", "drawing": _trend(), **({"timeframe": tf} if tf else {})}
        assert client.post("/api/charts/drawings", json=body, headers=me).status_code == 201
    doc = client.get("/api/charts/drawings/export?symbol=HDFCBANK", headers=me).json()
    assert doc["timeframes"] == ["5min", None, "week"] and len(doc["drawings"]) == 3
    doc["symbol"] = "ICICIBANK"
    assert client.post("/api/charts/drawings/import", json=doc, headers=me).status_code == 201
    assert [d["timeframe"] for d in client.get("/api/charts/drawings?symbol=ICICIBANK", headers=me).json()] == ["5min", None, "week"]
    # a document without timeframes (made before UI2-4a1) imports as before
    old = {k: v for k, v in doc.items() if k != "timeframes"} | {"symbol": "AXISBANK"}
    assert client.post("/api/charts/drawings/import", json=old, headers=me).status_code == 201
    assert {d["timeframe"] for d in client.get("/api/charts/drawings?symbol=AXISBANK", headers=me).json()} == {None}
    # one timeframe per drawing, from the shared list
    assert client.post("/api/charts/drawings/import", json={**doc, "symbol": "KOTAKBANK", "timeframes": ["5min"]}, headers=me).status_code == 422
    assert client.post("/api/charts/drawings/import", json={**doc, "symbol": "KOTAKBANK", "timeframes": ["1h", None, None]},
                       headers=me).status_code == 422
    assert client.get("/api/charts/drawings?symbol=KOTAKBANK", headers=me).json() == []
