"""Master Order 4 slice 3 (app/growth, the admin console's activation): the plan page with its usage meter, the
"my discipline this week" share card and the admin activation events, behind `ui_v2` + `growth_v2` (OFF).

Covered: both flags off = 404 naming the flag (and the admin read too); the usage numbers from fixtures (own watchlists,
own scans this month, own AI requests including those on his own key, the team's backtests and paper bots), the
plan_limits_v2 limits only while that flag enforces them, a running trial's days left, no price anywhere; the share
card's content guard (no security names, no prices, no money words, no call words) over fixtures that hold symbols and
prices, its facts (days inside his own limits - a day without a trade counts - journal streak, days traded) and that it
never rewards trading more; the activation read: admin only (403 for every tenant role, 403 without two-factor), the
funnel and the per-person steps from fixtures, masked e-mail addresses.
"""
import asyncio
import json
from datetime import date, datetime, timedelta, timezone

from app.db.models import (
    BrokerCredentialRecord, JournalEntryRecord, LlmCallRecord, RiskSettingsRecord, ScreenRunRecord, StrategyDeploymentRecord,
    SubscriptionRecord, TradeRecord, UsageRecord, User, WatchlistRecord,
)
from app.growth import plan as growth_plan, share
from app.market_data.calendar import IST
from tests.test_admin_api import _admin
from tests.test_admin_console import _member
from tests.test_amw2_watchlists import _set_flag
from tests.test_auth_api import _register, _session_factory, client

run = asyncio.run
UTC = timezone.utc
# A Wednesday far from every other suite's dates.
WED = date(2032, 3, 17)
MONDAY = WED - timedelta(days=2)


def _ist(day: date, hh: int, mm: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST).astimezone(UTC)


def _who(email: str):
    headers = {"Authorization": f"Bearer {_register(email)}"}
    me = client.get("/api/auth/me", headers=headers).json()
    return headers, me


def _on(me, *flags):
    for name in flags:
        _set_flag(name, me["tenant_id"], on=True)


def _add(*rows):
    async def go():
        async with _session_factory() as session:
            session.add_all(list(rows))
            await session.commit()
    run(go())


def _with_user(me, fn):
    async def go():
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            return await fn(session, user)
    return run(go())


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _trade(me, symbol, entry_at, *, exit_at=None, pnl=None, notes=None, entry=2456.75):
    return TradeRecord(tenant_id=me["tenant_id"], user_id=me["id"], mode="PAPER", symbol=symbol, strategy_id="manual_swing",
                       direction="LONG", entry_time=entry_at, entry_price=entry, quantity=10, stop_loss=entry - 25.5,
                       target1=entry + 60.25, exit_time=exit_at, exit_price=None if exit_at is None else entry + 12.4, pnl=pnl,
                       charges=0.0, notes=notes)


# --- flags --------------------------------------------------------------------------------------------------------------

def test_flags_off_means_404_naming_the_flag():
    headers, me = _who("mo4g-flags@example.com")
    for path in ("/api/growth/plan", "/api/growth/share/discipline-week"):
        got = client.get(path, headers=headers)
        assert got.status_code == 404 and got.headers.get("X-Feature-Disabled") == "ui_v2"
    _on(me, "ui_v2")
    for path in ("/api/growth/plan", "/api/growth/share/discipline-week"):
        got = client.get(path, headers=headers)
        assert got.status_code == 404 and got.headers.get("X-Feature-Disabled") == "growth_v2"
    _on(me, "growth_v2")
    for path in ("/api/growth/plan", "/api/growth/share/discipline-week"):
        assert client.get(path, headers=headers).status_code == 200
    assert client.get("/api/growth/plan").status_code in (401, 403)


def test_the_flag_is_registered_off_with_its_catalogue_entry():
    from app.platform.controls import DEFAULT_OFF_FLAGS, FEATURE_FLAGS
    from app.platform.flag_catalog import FLAG_INFO
    assert "growth_v2" in FEATURE_FLAGS and "growth_v2" in DEFAULT_OFF_FLAGS
    assert FLAG_INFO["growth_v2"].depends_on == ("ui_v2",)


# --- the plan page and the usage meter ------------------------------------------------------------------------------------

def test_usage_numbers_come_from_the_fixtures():
    headers, me = _who("mo4g-usage@example.com")
    _, other = _who("mo4g-usage-other@example.com")
    _on(me, "ui_v2", "growth_v2")
    now = datetime(2032, 3, 17, 9, 0, tzinfo=UTC)
    this_month, last_month = datetime(2032, 3, 2, tzinfo=UTC), datetime(2032, 2, 20, tzinfo=UTC)
    llm = dict(feature="copilot", provider="anthropic", system_sha256="x", user_sha256="y", system_text="", user_text="")
    _add(
        WatchlistRecord(tenant_id=me["tenant_id"], user_id=me["id"], name="Mine 1"),
        WatchlistRecord(tenant_id=me["tenant_id"], user_id=me["id"], name="Mine 2"),
        WatchlistRecord(tenant_id=other["tenant_id"], user_id=other["id"], name="Not mine"),
        *[ScreenRunRecord(tenant_id=me["tenant_id"], user_id=me["id"], ast_sha256="a" * 64, ast_version="screenql/1", base_tf="1d",
                          universe_json="[]", data_source="lake", created_at=this_month) for _ in range(3)],
        ScreenRunRecord(tenant_id=me["tenant_id"], user_id=me["id"], ast_sha256="b" * 64, ast_version="screenql/1", base_tf="1d",
                        universe_json="[]", data_source="lake", created_at=last_month),
        LlmCallRecord(tenant_id=me["tenant_id"], user_id=me["id"], created_at=this_month, **llm),
        LlmCallRecord(tenant_id=me["tenant_id"], user_id=me["id"], created_at=this_month, billed_to_user_key=True, **llm),
        LlmCallRecord(tenant_id=me["tenant_id"], user_id=me["id"], created_at=last_month, **llm),
        LlmCallRecord(tenant_id=other["tenant_id"], user_id=other["id"], created_at=this_month, **llm),
        UsageRecord(tenant_id=me["tenant_id"], metric="backtest", quantity=4, period_start=this_month, period_end=this_month + timedelta(days=1)),
        UsageRecord(tenant_id=me["tenant_id"], metric="backtest", quantity=9, period_start=last_month, period_end=last_month + timedelta(days=1)),
        StrategyDeploymentRecord(tenant_id=me["tenant_id"], strategy_id="orb_15m_5m", symbol="NIFTY 50", mode="PAPER", status="ACTIVE",
                                 created_by=me["id"]),
    )
    page = _with_user(me, lambda s, u: growth_plan.plan_page(s, u, now=now))
    meters = {m["key"]: m for m in page["meters"]}
    assert page["plan"]["name"] == "Free" and page["trial"] is None
    assert meters["watchlists"]["used"] == 2 and meters["watchlists"]["limit"] is None        # plan_limits_v2 off: no limit
    assert meters["watchlists"]["note"] == "No limit on your plan right now."
    assert meters["scans"]["used"] == 3 and meters["scans"]["text"] == "3 this month"
    assert meters["ai"]["used"] == 2                                                          # own key counted, other user not
    assert meters["backtests"]["used"] == 4 and meters["backtests"]["limit"] == 50 and meters["backtests"]["text"] == "4 of 50 this month"
    assert meters["paper_bots"]["used"] == 1 and meters["paper_bots"]["limit"] == 2 and meters["paper_bots"]["state"] == "ok"
    assert "analyses_today" not in meters                                                     # chart_analyze off
    assert page["month_starts"] == "2032-03-01"

    _on(me, "plan_limits_v2")
    page = _with_user(me, lambda s, u: growth_plan.plan_page(s, u, now=now))
    meters = {m["key"]: m for m in page["meters"]}
    assert meters["watchlists"]["limit"] == 5 and meters["watchlists"]["text"] == "2 of 5"
    assert meters["scans"]["note"] == "Up to 50 stocks in one scan."
    assert any(row["text"] == "5 watchlists" for row in page["includes"])

    # No price anywhere on the plan page (the owner decides prices: OPEN_QUESTIONS MO4-21).
    body = client.get("/api/growth/plan", headers=headers).json()
    text = json.dumps(body)
    assert "2999" not in text and "2,999" not in text and "₹" not in text and "price" not in text and "INR" not in text


def test_paper_bots_full_and_near_states_and_the_team_note():
    _, me = _who("mo4g-full@example.com")
    _member("mo4g-full-member@example.com", me["tenant_id"], "USER")
    _add(*[StrategyDeploymentRecord(tenant_id=me["tenant_id"], strategy_id=f"s{i}", symbol="NIFTY 50", mode="PAPER", status="ACTIVE")
           for i in range(2)])
    page = _with_user(me, lambda s, u: growth_plan.plan_page(s, u))
    bots = next(m for m in page["meters"] if m["key"] == "paper_bots")
    assert bots["state"] == "full" and bots["text"] == "2 of 2" and bots["note"] == growth_plan.TEAM_NOTE
    assert growth_plan._meter("x", "X", 8, 10, "")["state"] == "near"
    assert growth_plan._meter("x", "X", None, 10, "")["text"] == "Not counted yet"


def test_a_running_trial_shows_its_days_left():
    _, me = _who("mo4g-trial@example.com")
    now = datetime(2032, 3, 17, 9, 0, tzinfo=UTC)
    _add(SubscriptionRecord(tenant_id=me["tenant_id"], plan_id="pro", status="TRIALING", trial_end=now + timedelta(days=3, hours=4),
                            current_period_end=now + timedelta(days=3, hours=4)))
    page = _with_user(me, lambda s, u: growth_plan.plan_page(s, u, now=now))
    assert page["trial"] == {"plan_name": "Pro", "ends_on": "2032-03-20", "days_left": 4}


# --- the share card -----------------------------------------------------------------------------------------------------

def test_the_guard_refuses_names_prices_money_and_call_words():
    names = ["RELIANCE", "manual_swing"]
    assert share.problems("I stayed inside my own daily limits on 3 of 5 market days.", names) == []
    for bad in ("Bought RELIANCE this week", "Entry near 2,456.75", "Seller area 23000", "A profit of 4 points", "Booked P&L",
                "Stop-loss held", "My target was hit", "₹5 kept", "manual_swing worked", "I lost 2 trades", "Sell when it breaks"):
        assert share.problems(bad, names), bad
    card = {"title": share.TITLE, "week_label": "Week of 15 Mar", "footer": share.FOOTER,
            "facts": [{"key": "a", "label": "Days inside my own limits", "value": "3 of 5"}, {"key": "b", "label": "RELIANCE", "value": "1"}],
            "lines": ["Clean line.", "Exit at 2456.75."]}
    shown = share.guard(card, names)
    assert [f["key"] for f in shown["facts"]] == ["a"] and shown["lines"] == ["Clean line."] and shown["title"] == share.TITLE


def test_inside_limits_counts_a_day_without_a_trade_and_never_rewards_more_trades():
    me = {"tenant_id": 1, "id": 1}
    days = [MONDAY + timedelta(days=i) for i in range(3)]
    calm = [_trade(me, "TCS", _ist(MONDAY, 10), exit_at=_ist(MONDAY, 14), pnl=-100.0)]
    busy = calm + [_trade(me, "TCS", _ist(days[1], 10 + i % 5, i), exit_at=_ist(days[1], 15), pnl=-10.0) for i in range(4)]
    assert share.inside_limits([], days, 3, 1000.0) == 3                 # no trade at all: every day inside
    assert share.inside_limits(calm, days, 3, 1000.0) == 3
    assert share.inside_limits(busy, days, 3, 1000.0) == 2               # more trades than his own number: fewer days inside
    big_loss = [_trade(me, "TCS", _ist(MONDAY, 10), exit_at=_ist(MONDAY, 14), pnl=-1500.0)]
    assert share.inside_limits(big_loss, days, 3, 1000.0) == 2


def test_journal_streak_skips_weeks_without_a_closed_trade():
    me = {"tenant_id": 1, "id": 1}
    noted = lambda d: _trade(me, "TCS", _ist(d, 10), exit_at=_ist(d, 14), pnl=5.0, notes="followed my plan")  # noqa: E731
    bare = lambda d: _trade(me, "TCS", _ist(d, 10), exit_at=_ist(d, 14), pnl=5.0, notes="  ")              # noqa: E731
    closed = [noted(MONDAY), noted(MONDAY - timedelta(days=14)), bare(MONDAY - timedelta(days=21))]
    assert share.journal_streak(closed, MONDAY) == 2                     # this week, (a week with nothing), two weeks back
    assert share.journal_streak([bare(MONDAY)] + closed, MONDAY) == 0
    assert share.journal_streak([], MONDAY) == 0


def test_the_card_from_fixtures_holds_discipline_facts_only(monkeypatch):
    headers, me = _who("mo4g-card@example.com")
    _on(me, "ui_v2", "growth_v2")
    now = _ist(WED, 18)

    async def no_holidays(*_a, **_k):
        return set()
    monkeypatch.setattr("app.market_data.calendar.family_holidays", no_holidays)
    _add(
        RiskSettingsRecord(tenant_id=me["tenant_id"], capital=200000.0, max_daily_loss_pct=2.0, max_trades_per_day=2),
        _trade(me, "RELIANCE", _ist(MONDAY, 10), exit_at=_ist(MONDAY, 14), pnl=-1234.5, notes="waited for my area"),
        _trade(me, "RELIANCE", _ist(WED, 10), exit_at=_ist(WED, 11), pnl=820.25, notes="per plan"),
        _trade(me, "HDFCBANK", _ist(WED, 12), exit_at=_ist(WED, 13), pnl=-50.0, notes="ok"),
        _trade(me, "HDFCBANK", _ist(WED, 13, 30), pnl=None),                                   # a third entry on Wednesday
    )
    card = _with_user(me, lambda s, u: share.discipline_week(s, u, now=now))
    facts = {f["key"]: f["value"] for f in card["facts"]}
    assert card["title"] == "My discipline this week" and card["week_label"] == "Week of 15 Mar"
    assert facts["inside_limits"] == "2 of 3"          # Wednesday had three entries against his own two
    assert facts["journal_streak"] == "1 week"
    assert facts["days_traded"] == "2 of 3"
    assert "discipline_score" not in facts             # the journal is off
    assert share.NO_TRADE_LINE in card["lines"]
    for text in _strings({k: v for k, v in card.items() if k != "hints"}):
        assert share.problems(text, ["RELIANCE", "HDFCBANK", "manual_swing"]) == [], text
    blob = json.dumps(card)
    for leak in ("RELIANCE", "HDFCBANK", "2456", "1234", "820", "manual_swing", "₹", "profit", "target", "stop"):
        assert leak not in blob, leak
    assert card["empty"] is False

    # The route answers the same guarded shape for the caller's own week.
    body = client.get("/api/growth/share/discipline-week", headers=headers).json()
    assert body["title"] == "My discipline this week" and "RELIANCE" not in json.dumps(body)


def test_the_card_without_risk_settings_or_trades_says_what_to_do():
    _, me = _who("mo4g-card-empty@example.com")
    card = _with_user(me, lambda s, u: share.discipline_week(s, u, now=_ist(WED, 18)))
    assert card["empty"] is True
    assert [f["key"] for f in card["facts"]] == ["journal_streak", "days_traded"]
    assert any("Risk limits page" in h for h in card["hints"])


# --- the admin activation events ------------------------------------------------------------------------------------------

def test_activation_is_admin_only_with_two_factor():
    headers, me = _who("mo4g-act-owner@example.com")
    _on(me, "ui_v2", "growth_v2")
    assert client.get("/api/admin/console/activation", headers=headers).status_code == 403
    member = _member("mo4g-act-user@example.com", me["tenant_id"], "USER")
    assert client.get("/api/admin/console/activation", headers=member).status_code == 403
    no_mfa, admin_me = _who("mo4g-act-admin-no-mfa@example.com")

    async def promote():
        async with _session_factory() as session:
            (await session.get(User, admin_me["id"])).role = "SUPER_ADMIN"
            await session.commit()
    run(promote())
    _on(admin_me, "ui_v2", "growth_v2")
    got = client.get("/api/admin/console/activation", headers=no_mfa)
    assert got.status_code == 403 and got.headers.get("X-Step-Up") == "mfa"
    assert client.get("/api/admin/console/activation").status_code in (401, 403)


def test_activation_funnel_from_fixtures():
    admin, admin_me = _admin("mo4g-act-admin@example.com")
    got = client.get("/api/admin/console/activation", headers=admin)
    assert got.status_code == 404 and got.headers.get("X-Feature-Disabled") == "growth_v2"      # flag off: nothing
    _on(admin_me, "growth_v2")
    _, full = _who("mo4g-act-full@example.com")
    _, half = _who("mo4g-act-half@example.com")

    async def seed():
        async with _session_factory() as session:
            user = await session.get(User, full["id"])
            signed = user.created_at if user.created_at.tzinfo else user.created_at.replace(tzinfo=UTC)
            session.add(BrokerCredentialRecord(tenant_id=full["tenant_id"], user_id=full["id"], broker_name="upstox", encrypted_payload="x",
                                               created_at=signed + timedelta(hours=1)))
            session.add(StrategyDeploymentRecord(tenant_id=full["tenant_id"], strategy_id="orb_15m_5m", symbol="NIFTY 50", mode="PAPER",
                                                 created_by=full["id"], created_at=signed + timedelta(hours=2)))
            trade = TradeRecord(tenant_id=full["tenant_id"], user_id=full["id"], mode="PAPER", symbol="TCS", strategy_id="manual",
                                direction="LONG", entry_time=signed, entry_price=1.0, quantity=1, stop_loss=0.5)
            session.add(trade)
            await session.flush()
            session.add(JournalEntryRecord(tenant_id=full["tenant_id"], user_id=full["id"], trade_id=trade.id,
                                           captured_at=signed + timedelta(hours=6)))
            session.add(StrategyDeploymentRecord(tenant_id=half["tenant_id"], strategy_id="orb_15m_5m", symbol="NIFTY 50", mode="LIVE",
                                                 created_by=half["id"]))                      # LIVE is not a paper bot
            await session.commit()
    run(seed())
    body = client.get("/api/admin/console/activation?days=30", headers=admin).json()
    people = {p["id"]: p for p in body["people"]}
    assert people[full["id"]]["broker_at"] and people[full["id"]]["paper_bot_at"] and people[full["id"]]["journal_at"]
    assert people[full["id"]]["activated_at"] == people[full["id"]]["journal_at"]
    assert people[half["id"]]["paper_bot_at"] is None and people[half["id"]]["activated_at"] is None
    assert people[full["id"]]["email"].startswith("mo") and "*" in people[full["id"]]["email"]
    assert admin_me["id"] not in people                                                          # operators are not counted
    funnel = {f["key"]: f["count"] for f in body["funnel"]}
    assert list(funnel) == ["signed_up", "broker", "paper_bot", "journal", "activated"]
    assert funnel["signed_up"] >= 2 and funnel["activated"] >= 1
    assert funnel["broker"] >= funnel["activated"] and funnel["paper_bot"] >= funnel["activated"]
    assert body["median_hours_to_activation"] is not None
