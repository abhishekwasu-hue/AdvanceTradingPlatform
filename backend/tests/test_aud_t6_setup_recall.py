"""AUD-T6 (owner audit 28 Oct; BR-M3 / BR-R1): similar-setup recall and "what the context said vs what happened".

- "similar" = an exact match on the stored fields (setup, side, holding, bot timeframe) - nothing else (not the
  symbol, not the indicator regime);
- no look-ahead: a trade closed at or after the subject's entry is never matched, nor a structure with a leg closed then;
- the aggregate words only from SETUP_RECALL_MIN_SAMPLE matches;
- plain words: no buy / sell / target / stop-loss wording;
- routes: 404 while `setup_recall` (or `ui_v2`) is off; self-scoped (another user's trade / proposal 404, a teammate's
  or another organisation's trades never matched).
"""
import json
from datetime import datetime, timedelta, timezone

from app.core import config
from app.db.models import AiActionRecord, JournalEntryRecord, TradeRecord, User
from app.setup_recall import service
from app.signals.language import is_clean
from tests.test_auth_api import _session_factory, client
from tests.test_settings_a_connect_ai import _flags
from tests.test_trading_worker import _deploy, _run, _tenant

T0 = datetime(2026, 10, 1, 4, 0, tzinfo=timezone.utc)


def _trade(i, *, pnl=10.0, entry_h=0.0, exit_h=1.0, closed=True, strategy_id="s1", direction="LONG", holding="INTRADAY",
           deployment_id=None, exit_reason="Target 1", **kw):
    return TradeRecord(id=i, tenant_id=1, user_id=1, mode=kw.pop("mode", "PAPER"), symbol=kw.pop("symbol", "RELIANCE"),
                       strategy_id=strategy_id, direction=direction, entry_time=T0 + timedelta(hours=entry_h), entry_price=100.0,
                       quantity=10.0, stop_loss=kw.pop("stop_loss", 95.0), holding=holding, deployment_id=deployment_id,
                       exit_time=T0 + timedelta(hours=exit_h) if closed else None, exit_price=101.0 if closed else None,
                       pnl=pnl if closed else None, exit_reason=exit_reason if closed else None, charges=0.0, **kw)


SUBJECT = service.Context(setup="s1", side="LONG", holding="INTRADAY", timeframe=None)


def test_only_trades_closed_before_the_entry_are_matched():
    cutoff = T0 + timedelta(hours=10)
    units = service.build_units([
        _trade(1, exit_h=9.0),                       # closed before: matched
        _trade(2, exit_h=10.0),                      # closed exactly at the entry: not
        _trade(3, entry_h=2.0, exit_h=11.0),         # entered before, closed after: not (look-ahead)
        _trade(4, closed=False),                     # still open: not
    ], {})
    assert [u.trade_id for u in service.pick(SUBJECT, cutoff, units)] == [1]


def test_a_structure_counts_once_and_only_when_every_leg_closed_before():
    cutoff = T0 + timedelta(hours=10)
    early = [_trade(1, exit_h=5.0, leg_group_id="g1"), _trade(2, exit_h=6.0, leg_group_id="g1")]
    late = [_trade(3, exit_h=5.0, leg_group_id="g2"), _trade(4, exit_h=12.0, leg_group_id="g2")]   # one leg after the entry
    matched = service.pick(SUBJECT, cutoff, service.build_units(early + late, {}))
    assert [u.trade_ids for u in matched] == [(1, 2)]


def test_similar_means_an_exact_match_on_the_stored_fields_only():
    cutoff = T0 + timedelta(hours=10)
    rows = [
        _trade(1), _trade(2, symbol="INFY", regime_at_entry="RANGING"),      # another symbol / regime: still similar
        _trade(3, strategy_id="s2"), _trade(4, direction="SHORT"), _trade(5, holding="SWING"),
        _trade(6, deployment_id=7),                                           # a bot on 5min: the subject has no timeframe
        _trade(7, direction="LONG", underlying_direction="SHORT", instrument_kind="OPTION"),   # the side is the underlying's
    ]
    units = service.build_units(rows, {7: "5min"})
    assert sorted(u.trade_id for u in service.pick(SUBJECT, cutoff, units)) == [1, 2]
    bot = service.Context(setup="s1", side="LONG", holding="INTRADAY", timeframe="5min")
    assert [u.trade_id for u in service.pick(bot, cutoff, units)] == [6]
    assert service.MATCH_FIELDS == ("setup", "side", "holding", "timeframe")


def test_the_subject_is_never_its_own_match():
    units = service.build_units([_trade(1), _trade(2)], {})
    assert [u.trade_id for u in service.pick(SUBJECT, T0 + timedelta(hours=5), units, {2})] == [1]


def test_aggregate_words_only_from_the_minimum_sample():
    units = service.build_units([_trade(i, pnl=50.0 if i % 2 else -25.0, exit_reason="Stop Loss" if i % 2 == 0 else "Target 1")
                                 for i in range(1, 5)], {})
    few = service.recall(SUBJECT, T0 + timedelta(hours=5), units, min_sample=5, max_listed=10)
    assert few["count"] == 4 and not few["enough"] and few["wins"] is None and few["avg_r"] is None
    assert few["line"] == "Not enough history yet: 4 of 5 similar closed trades."
    assert len(few["matches"]) == 4                                         # the matches are still listed
    enough = service.recall(SUBJECT, T0 + timedelta(hours=5), units, min_sample=4, max_listed=3)
    assert enough["enough"] and enough["wins"] == 2 and enough["losses"] == 2
    assert enough["avg_r"] == round((1.0 - 0.5 + 1.0 - 0.5) / 4, 2)          # risk 5 x 10 = 50
    assert enough["common_loss_exit"] == "Closed at your exit-if-wrong level"
    assert len(enough["matches"]) == 3 and enough["matches"][0]["trade_id"] == 4   # newest closed first, capped
    assert service.recall(SUBJECT, T0, [], min_sample=1)["line"] == service.NO_MATCHES


def test_words_carry_no_call_wording():
    for reason in ("Stop Loss", "Target 1", "Target 2", "Trailing stop", "Time exit 15:15", "Premium floor", "Emergency Exit",
                   "AI action #3 (stop_too_wide) approved", "Breakeven stop", "Expiry", "", None, "whatever"):
        assert is_clean(service.exit_words(reason)), reason
    units = service.build_units([_trade(i, pnl=-10.0, exit_reason="Stop Loss") for i in range(1, 4)], {})
    assert is_clean(service.recall(SUBJECT, T0 + timedelta(hours=5), units, min_sample=1)["line"])
    assert service.clean_reasons(["Pullback into the buyer area", "Buy now", 3, "Pullback into the buyer area"]) == ["Pullback into the buyer area", "3"]


def test_reflection_uses_the_stored_context_and_the_outcome():
    t = _trade(1, pnl=90.0, deployment_id=7, exit_reason="Target 1")
    unit = service.build_units([t], {7: "15min"})[0]
    out = service.reflection(t, unit, planned_rr=2.0, excursions={"mae_r": 0.4, "mfe_r": 2.1}, reasons=["Pullback into the buyer area"],
                             source="STRATEGY")
    assert out["closed"] and out["said"]["timeframe"] == "15min" and out["said"]["reasons"] == ["Pullback into the buyer area"]
    assert out["happened"]["r"] == 1.8 and out["happened"]["how_closed"] == "Closed at your exit-at-profit level"
    assert out["line"] == ("Entered on the long side of the 15min chart, planned reward to risk 2; closed at your exit-at-profit level, "
                           "+1.80 R, a gain; while open it moved up to 2.1 R in your favour and 0.4 R against you.")
    assert is_clean(out["line"])
    open_t = _trade(2, closed=False)
    assert service.reflection(open_t, service.build_units([open_t], {})[0], planned_rr=None, excursions={}, reasons=[], source=None)["happened"] is None


# --- routes -------------------------------------------------------------------------------------------------------------

def _seed(rows):
    async def go():
        async with _session_factory() as session:
            for r in rows:
                session.add(r)
            await session.commit()
            return [getattr(r, "id", None) for r in rows]
    return _run(go())


def _db(t, i, *, user_id=None, **kw):
    row = _trade(i, **kw)
    row.id = None
    row.tenant_id, row.user_id = t["tenant_id"], user_id or t["user_id"]
    return row


def test_routes_are_flagged_self_scoped_and_never_look_ahead():
    t = _tenant("aud-t6-route@example.com")
    other = _tenant("aud-t6-other@example.com")
    dep = _deploy(t, symbol="INFY")

    async def teammate():
        async with _session_factory() as session:
            u = User(tenant_id=t["tenant_id"], email="aud-t6-mate@example.com", hashed_password="x")
            session.add(u)
            await session.commit()
            return u.id
    mate = _run(teammate())
    ids = _seed([
        _db(t, 1, exit_h=2.0, pnl=50.0),                              # matched
        _db(t, 2, exit_h=3.0, pnl=-25.0, exit_reason="Stop Loss"),    # matched
        _db(t, 3, entry_h=4.0, exit_h=20.0, pnl=99.0),                # closed after the subject's entry: never
        _db(t, 4, exit_h=2.5, pnl=99.0, user_id=mate),                # a teammate's: never
        _db(t, 5, exit_h=2.5, pnl=99.0, deployment_id=dep),           # a bot on 1min: another timeframe
        _db(t, 6, entry_h=10.0, exit_h=12.0, pnl=90.0),               # the subject
    ])
    _seed([_db(other, 7, exit_h=1.0, pnl=99.0)])                      # another organisation's
    subject = ids[5]
    _seed([JournalEntryRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], trade_id=subject, high_price=110.0, low_price=98.0,
                              plan_json=json.dumps({"rr": 2.0}))])
    action_ids = _seed([
        AiActionRecord(tenant_id=t["tenant_id"], action="ENTRY", rule="ENTRY_SIGNAL", reason="r", status="EXECUTED", trade_id=subject,
                       owner_user_id=t["user_id"], expires_at=T0 + timedelta(hours=11), created_at=T0 + timedelta(hours=9, minutes=59),
                       evidence_json=json.dumps({"strategy_id": "s1", "direction": "LONG", "holding": "INTRADAY",
                                                 "signal": {"reasons": ["Pullback into the buyer area", "Buy above the high"]}})),
        AiActionRecord(tenant_id=t["tenant_id"], action="ENTRY", rule="ENTRY_SIGNAL", reason="r", status="PROPOSED", owner_user_id=mate,
                       expires_at=T0 + timedelta(hours=11), evidence_json="{}"),
        AiActionRecord(tenant_id=t["tenant_id"], action="ENTRY", rule="ENTRY_SIGNAL", reason="r", status="PROPOSED", owner_user_id=t["user_id"],
                       deployment_id=dep, expires_at=T0 + timedelta(hours=30), created_at=T0 + timedelta(hours=25),
                       evidence_json=json.dumps({"strategy_id": "s1", "direction": "LONG", "holding": "INTRADAY"})),
    ])
    own_action, mate_action, bot_action = action_ids
    _flags(ui_v2=True, setup_recall=False)
    try:
        assert client.get(f"/api/setup-recall/trades/{subject}", headers=t["headers"]).status_code == 404
        _flags(ui_v2=False, setup_recall=True)
        assert client.get(f"/api/setup-recall/proposals/{own_action}", headers=t["headers"]).status_code == 404
        _flags(ui_v2=True, setup_recall=True)
        body = client.get(f"/api/setup-recall/trades/{subject}", headers=t["headers"]).json()
        rec = body["recall"]
        assert rec["context"] == {"setup": "s1", "side": "LONG", "holding": "INTRADAY", "timeframe": None}
        assert [m["trade_id"] for m in rec["matches"]] == [ids[1], ids[0]] and rec["count"] == 2
        assert rec["min_sample"] == config.SETUP_RECALL_MIN_SAMPLE and rec["matches"][0]["journal_week"] == "2026-W40"
        ref = body["reflection"]
        assert ref["closed"] and ref["said"]["planned_rr"] == 2.0 and ref["said"]["reasons"] == ["Pullback into the buyer area"]
        assert ref["happened"]["mfe_r"] == 2.0 and ref["happened"]["mae_r"] == 0.4 and ref["happened"]["r"] == 1.8
        assert all("tenant_id" not in m and "user_id" not in m for m in rec["matches"])
        # The proposal raised before trade 3 closed: its cut-off is its own time raised.
        prop = client.get(f"/api/setup-recall/proposals/{own_action}", headers=t["headers"]).json()["recall"]
        assert [m["trade_id"] for m in prop["matches"]] == [ids[1], ids[0]]
        # A bot proposal (1min) matches only the bot's 1min trade; trade 3 has closed by then but has no timeframe.
        bot = client.get(f"/api/setup-recall/proposals/{bot_action}", headers=t["headers"]).json()["recall"]
        assert bot["context"]["timeframe"] == "1min" and [m["trade_id"] for m in bot["matches"]] == [ids[4]]
        # Scoping: a teammate's proposal, another user's trade, another organisation.
        assert client.get(f"/api/setup-recall/proposals/{mate_action}", headers=t["headers"]).status_code == 404
        assert client.get(f"/api/setup-recall/trades/{ids[3]}", headers=t["headers"]).status_code == 404
        assert client.get(f"/api/setup-recall/trades/{subject}", headers=other["headers"]).status_code == 404
        assert client.get(f"/api/setup-recall/proposals/{own_action}", headers=other["headers"]).status_code == 404
    finally:
        _flags(ui_v2=False, setup_recall=False)
