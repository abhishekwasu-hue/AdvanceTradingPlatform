"""Owner VPS log 25 Nov: the chart reading (and the Copilot, the hybrid analysis) call `market_zones_layer` as a Python
function without `timeframe`; it then held FastAPI's `Query(None)` object, not None, and raised "unknown timeframe
Query(None)" whenever areas were stored - the Conclusion panel's Chart read "not available". Left out = no timeframe."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.charts import zones_layer as zl


def _snap() -> SimpleNamespace:
    return SimpleNamespace(rows=[], price=25000.0, scope="platform", as_of=datetime(2026, 11, 25, 7, 0, tzinfo=timezone.utc),
                           fallback=False)


@pytest.fixture
def stored_areas(monkeypatch):
    async def no_flag(*a, **k):
        return None

    async def snap(*a, **k):
        return _snap()
    monkeypatch.setattr(zl, "require_flag", no_flag)
    monkeypatch.setattr(zl, "snapshot_for", snap)
    monkeypatch.setattr(zl.chart, "candidates", lambda rows, family: [{"id": 1}])
    monkeypatch.setattr(zl.chart, "select_bands", lambda cands, ref, **k: list(cands))


def test_a_direct_call_without_a_timeframe_reads_all_areas(stored_areas):
    user = SimpleNamespace(tenant_id=1, id=1)
    out = asyncio.run(zl.market_zones_layer(instrument="NSE_INDEX:NIFTY50", at=None, price=25000.0, family="all",
                                            max_bands=zl.chart.MAX_BANDS, user=user, session=None))
    assert out["bands"] == [{"id": 1}] and "timeframe" not in out


def test_a_given_timeframe_still_splits_own_and_higher(stored_areas, monkeypatch):
    seen = {}

    def split(cands, tf):
        seen["tf"] = tf
        return cands, []
    monkeypatch.setattr(zl.chart, "split_by_timeframe", split)
    user = SimpleNamespace(tenant_id=1, id=1)
    out = asyncio.run(zl.market_zones_layer(instrument="NSE_INDEX:NIFTY50", at=None, price=25000.0, family="all",
                                            max_bands=zl.chart.MAX_BANDS, timeframe="15m", user=user, session=None))
    assert seen["tf"] == "15m" and out["timeframe"] == "15m"
