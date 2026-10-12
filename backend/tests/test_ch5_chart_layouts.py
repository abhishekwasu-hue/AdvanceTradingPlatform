"""CH5: saved multi-chart workspace layouts - the `layout/1` schema (grid 1/2/4/6/8, one cell per chart, link groups
A-D with their symbol / interval / crosshair sync), per-user storage with optimistic versions and a cap, and the
`chart_workspace` flag (off by default -> 503)."""
import asyncio

import pytest
from pydantic import ValidationError

from app.auth.security import hash_password
from app.charts import layouts
from app.charts.layouts import Layout
from app.db.models import User
from app.platform import controls
from tests.test_auth_api import _session_factory, client
from tests.test_phase_l_ai import _owner


def _flag(on: bool, tenants=()):
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = current.get("flags") or {}
            flags[layouts.FLAG] = {"on": on, "tenants": list(tenants)}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    asyncio.run(go())


def _cell(symbol="NIFTY 50", tf="5min", group=None, chart_type="candles"):
    return {"symbol": symbol, "exchange": "NSE", "timeframe": tf, "chart_type": chart_type, "group": group}


def _layout(grid=2, **kw):
    cells = [_cell(group="A"), _cell("BANKNIFTY", group="A"), _cell("RELIANCE"), _cell("TCS", "day"), _cell("INFY"), _cell("SBIN"),
             _cell("HDFCBANK"), _cell("ITC")][:grid]
    return {"schema": "layout/1", "grid": grid, "cells": cells, "groups": {"A": {"symbol": True, "interval": True, "crosshair": True}},
            "active": 0, **kw}


def _teammate(tenant_id: int, email: str, role: str = "VIEWER") -> dict:
    async def go():
        async with _session_factory() as session:
            session.add(User(tenant_id=tenant_id, email=email, hashed_password=hash_password("S3cur3Pass!"), role=role, is_active=True))
            await session.commit()
    asyncio.run(go())
    token = client.post("/api/auth/login", json={"email": email, "password": "S3cur3Pass!"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_flag_is_registered_off_by_default():
    assert layouts.FLAG == "chart_workspace" and layouts.FLAG in controls.FEATURE_FLAGS and layouts.FLAG in controls.DEFAULT_OFF_FLAGS
    assert "places no orders" in controls.FEATURE_FLAGS[layouts.FLAG]


def test_the_schema_fits_the_grid_and_names_only_known_values():
    for grid in layouts.GRIDS:
        assert Layout.model_validate(_layout(grid)).to_json()["schema"] == "layout/1"
    for bad, words in ((_layout(3), "grid"), ({**_layout(4), "cells": _layout(2)["cells"]}, "needs 4 cells"),
                       (_layout(2, active=2), "active"), ({**_layout(1), "cells": [_cell("BAD<SYM>")]}, "pattern"),
                       ({**_layout(1), "cells": [_cell(tf="2min")]}, "timeframe"),
                       ({**_layout(1), "cells": [_cell(chart_type="kagi")]}, "chart_type"),
                       ({**_layout(1), "cells": [_cell(group="E")]}, "group"),
                       ({**_layout(1), "groups": {"Z": {"symbol": True}}}, "groups"),
                       ({**_layout(1), "x": 1}, "Extra inputs")):
        with pytest.raises(ValidationError, match=words):
            Layout.model_validate(bad)
    assert Layout.model_validate({**_layout(1), "cells": [_cell("nifty 50")]}).cells[0].symbol == "NIFTY 50"


def test_flag_off_answers_503_and_on_stores_per_user_with_versions():
    me, who = _owner("ch5-me@example.com")
    other, _ = _owner("ch5-other@example.com")
    _flag(False)
    assert client.get("/api/charts/layouts", headers=me).status_code == 503
    assert client.post("/api/charts/layouts", json={"name": "Morning", "layout": _layout()}, headers=me).status_code == 503
    _flag(False, tenants=[who["tenant_id"]])                     # allow-listed for this organisation only
    made = client.post("/api/charts/layouts", json={"name": "  Morning   scan ", "layout": _layout(4)}, headers=me)
    assert made.status_code == 201, made.text
    row = made.json()
    assert row["name"] == "Morning scan" and row["version"] == 1 and row["layout"]["grid"] == 4
    assert client.get("/api/charts/layouts", headers=other).status_code == 503        # not on for the other organisation
    _flag(True)
    assert client.get("/api/charts/layouts", headers=other).json() == []             # per user
    assert client.put(f"/api/charts/layouts/{row['id']}", json={"version": 1, "layout": _layout(8)}, headers=other).status_code == 404
    viewer = _teammate(who["tenant_id"], "ch5-viewer@example.com")
    assert client.get("/api/charts/layouts", headers=viewer).json() == []            # a teammate's layouts are their own
    assert client.delete(f"/api/charts/layouts/{row['id']}?version=1", headers=viewer).status_code == 404
    v2 = client.put(f"/api/charts/layouts/{row['id']}", json={"version": 1, "name": "Open", "layout": _layout(8)}, headers=me).json()
    assert v2["version"] == 2 and v2["name"] == "Open" and len(v2["layout"]["cells"]) == 8
    stale = client.put(f"/api/charts/layouts/{row['id']}", json={"version": 1, "layout": _layout(1)}, headers=me)
    assert stale.status_code == 409 and stale.json()["detail"]["current"]["version"] == 2           # another tab never overwrites
    second = client.post("/api/charts/layouts", json={"name": "Close", "layout": _layout(2)}, headers=me).json()
    assert client.post("/api/charts/layouts", json={"name": "Close", "layout": _layout(2)}, headers=me).status_code == 409
    assert client.put(f"/api/charts/layouts/{second['id']}", json={"version": 1, "name": "Open", "layout": _layout(2)},
                      headers=me).status_code == 409                                  # names are unique per user
    assert [r["name"] for r in client.get("/api/charts/layouts", headers=me).json()] == ["Close", "Open"]   # newest first
    assert client.post("/api/charts/layouts", json={"name": "<b>", "layout": _layout(1)}, headers=me).status_code == 422
    assert client.delete(f"/api/charts/layouts/{row['id']}?version=1", headers=me).status_code == 409
    assert client.delete(f"/api/charts/layouts/{row['id']}?version=2", headers=me).json() == {"deleted": row["id"]}
    _flag(False)


def test_the_cap_per_user(monkeypatch):
    me, _ = _owner("ch5-cap@example.com")
    _flag(True)
    monkeypatch.setattr(layouts, "MAX_PER_USER", 2)
    for name in ("one", "two"):
        assert client.post("/api/charts/layouts", json={"name": name, "layout": _layout(1)}, headers=me).status_code == 201
    capped = client.post("/api/charts/layouts", json={"name": "three", "layout": _layout(1)}, headers=me)
    assert capped.status_code == 409 and "at most 2" in capped.json()["detail"]
    _flag(False)
