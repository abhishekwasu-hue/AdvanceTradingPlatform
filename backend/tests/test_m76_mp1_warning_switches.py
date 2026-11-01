"""M76 MP-1: Settings › Trade settings › Warnings - one switch (+ "ask me") per warning type, the owner's start states,
every number Not set, and the "accepted today" store with its TTL to the end of the IST day (no table, no migration)."""
import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.cache import client as cache
from app.me import trade_settings as ts
from app.paper import pretrade_warnings as pw
from app.platform import controls, flag_catalog

# The five pure mistakes of the amendment that exist on today's tickets (lot multiple and expiry-vs-plan need a source
# the lots-based ticket / M75 plans give first - docs/design/M76_REVIEW.md section 9).
MISTAKES = {"duplicate_order", "limit_far_from_ltp", "margin_short"}
FACTS = {"market_state", "expiry_day", "mis_cutoff", "fo_ban", "event", "price_source", "liquidity_limits", "own_limits"}


def test_one_switch_and_one_ask_me_per_type_with_the_owners_start_state():
    assert set(ts.WARNING_IDS) == MISTAKES | FACTS
    for wid in ts.WARNING_IDS:
        switch, ask = ts.BY_KEY[f"warnings.{wid}"], ts.BY_KEY[f"warnings.{wid}_ask"]
        assert switch.kind == ask.kind == "switch" and switch.section == ask.section == "warnings"
        assert switch.default is True and switch.default_by == "owner"          # amendment 2: ON (mistakes and facts)
        assert ask.default is False and ask.default_by == "owner"               # never an extra click unless he turns it on
        assert switch.flag == ask.flag == "trade_warnings"


def test_no_number_is_pre_filled():
    for key in ("warnings.collar_pct", "warnings.mis_minutes"):
        f = ts.BY_KEY[key]
        assert f.default is None and f.default_by is None and f.kind in ("number", "whole"), key
    s = pw.settings_from({})
    assert s.collar_pct is None and s.mis_minutes is None and s.square_off_time is None
    assert all(s.on.values()) and not any(s.ask.values())


def test_his_saved_values_win_over_the_start_state():
    s = pw.settings_from({"warnings.fo_ban": False, "warnings.duplicate_order_ask": True, "warnings.collar_pct": 5.0,
                          "warnings.mis_minutes": 15, "exits.square_off_time": "15:10"})
    assert s.enabled("fo_ban") is False and s.enabled("event") is True
    assert s.ask["duplicate_order"] is True and s.ask["fo_ban"] is False
    assert (s.collar_pct, s.mis_minutes, s.square_off_time) == (5.0, 15, "15:10")


def test_the_view_says_not_set_with_the_default_beside_it():
    view = ts.view({}, admin=True)
    warnings = next(sec for sec in view["sections"] if sec["id"] == "warnings")
    row = next(f for f in warnings["fields"] if f["key"] == "warnings.duplicate_order")
    assert row["value"] is None and row["saved"] is False and row["default"] is True and row["default_by"] == "owner"
    assert any(f["fact"] for f in warnings["fields"])                          # "a warning never holds back an order"


def test_hidden_while_the_flag_is_off_and_for_a_trader_until_wired():
    f = ts.BY_KEY["warnings.duplicate_order"]
    assert not ts.visible(f, admin=False, off=frozenset({"trade_warnings"}))
    assert ts.visible(f, admin=True)                                            # admin Advanced while not wired


def test_the_flag_is_registered_off_with_its_catalogue_entry():
    assert "trade_warnings" in controls.FEATURE_FLAGS and "trade_warnings" in controls.DEFAULT_OFF_FLAGS
    assert flag_catalog.FLAG_INFO["trade_warnings"].status == "unfinished"
    assert flag_catalog.FLAG_GROUP["trade_warnings"] == "orders"


def test_ttl_runs_to_midnight_ist():
    # 10:30 IST -> 13 h 30 min left; 23:59:30 IST -> 30 s left.
    assert pw.seconds_to_ist_midnight(datetime(2026, 10, 27, 5, 0, tzinfo=UTC)) == 13 * 3600 + 30 * 60
    assert pw.seconds_to_ist_midnight(datetime(2026, 10, 27, 18, 29, 30, tzinfo=UTC)) == 30


def test_instrument_key_names_one_contract_and_nothing_else():
    assert pw.instrument_key("nifty", "2026-11-03", 24500.0, "ce") == "NIFTY|2026-11-03|24500|CE"
    assert pw.instrument_key("NIFTY", "2026-11-24", None, "FUT") == "NIFTY|2026-11-24||FUT"
    assert pw.instrument_key("NI FTY*", "x", 1, "CE") == "NIFTY|x|1|CE"


@pytest.fixture
def user():
    return SimpleNamespace(id=9001, tenant_id=77)


def test_accepted_today_is_remembered_per_type_and_contract_with_a_ttl(user):
    async def go():
        await cache._get_client().delete(*[pw._key(user, w, "NIFTY|2026-11-03|24500|CE") for w in ts.WARNING_IDS])
        done = await pw.accept(user, ["event", "fo_ban", "not_a_type", "event"], "NIFTY|2026-11-03|24500|CE",
                               now=datetime(2026, 10, 27, 5, 0, tzinfo=UTC))
        same = await pw.accepted(user, "NIFTY|2026-11-03|24500|CE")
        other = await pw.accepted(user, "NIFTY|2026-11-03|24600|CE")
        ttl = await cache._get_client().ttl(pw._key(user, "event", "NIFTY|2026-11-03|24500|CE"))
        return done, same, other, ttl
    done, same, other, ttl = asyncio.run(go())
    assert done == ["event", "fo_ban"]
    assert same == {"event", "fo_ban"} and other == set()
    assert 0 < ttl <= 13 * 3600 + 30 * 60


def test_redis_down_suppresses_nothing(user, monkeypatch):
    class Down:
        async def mget(self, keys):
            raise ConnectionError("down")

        async def set(self, *a, **k):
            raise ConnectionError("down")
    monkeypatch.setattr(cache, "_get_client", lambda: Down())
    assert asyncio.run(pw.accepted(user, "X")) == set()
    assert asyncio.run(pw.accept(user, ["event"], "X")) == []
