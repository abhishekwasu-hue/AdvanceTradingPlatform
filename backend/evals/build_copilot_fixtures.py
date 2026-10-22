"""Builds `evals/sets/copilot_tools.json`: the recorded tool results the Copilot chat evals run on (no network, no model).

Every value is synthetic and hand-made (no broker, vendor or Trade data): a made-up option chain is passed through ATP's
real option chain analyses (app/option_chain/analyses.py) and the summaries of app/ai/tools/copilot.py, made-up open
trades through `copilot_trades.group_rows`, and a made-up journal week through the discipline engine's own score and
review (app/journal/score.py, review.py), so the shapes are exactly what the tools return. Regenerate after a change to those shapes, then pin the new sha256 in
scripts/check_public_repo.py ALLOWED_TEST_DATA:

    cd backend && python -m evals.build_copilot_fixtures
    python3 ../scripts/check_public_repo.py --hash evals/sets/copilot_tools.json
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from app.ai.tools.copilot import summarise_analyses
from app.brokers.models import OptionChain, OptionChainRow
from app.option_chain import analyses

OUT = Path(__file__).resolve().parent / "sets" / "copilot_tools.json"
NOW = datetime(2026, 10, 21, 3, 30, tzinfo=timezone.utc)
AS_OF = NOW.isoformat()


def _chain() -> OptionChain:
    spot, rows = 24850.5, []
    for i, strike in enumerate(range(24350, 25351, 50)):
        d = (strike - 24850) / 50
        rows.append(OptionChainRow(strike=strike, call_oi=40000 + 9000 * max(0, d) + (60000 if strike == 25100 else 0),
                                   put_oi=42000 + 8000 * max(0, -d) + (70000 if strike == 24600 else 0),
                                   call_ltp=round(max(5.0, 120.5 - 40 * d), 2), put_ltp=round(max(5.0, 110.0 + 40 * d), 2),
                                   call_iv=14.0 + 0.1 * abs(d), put_iv=14.6 + 0.12 * abs(d), call_change_oi=1000.0 * (i % 3), put_change_oi=800.0))
    return OptionChain(underlying="NIFTY", expiry="2026-10-27", underlying_ltp=spot, rows=rows)


def _analyses() -> Dict[str, Any]:
    chain = _chain()
    out = {"symbol": "NIFTY", "expiry": "2026-10-27", "state": "ok", "message": None, "spot": chain.underlying_ltp, "data_as_of": AS_OF,
           "analyses": analyses.compute_all(chain, [], NOW)}
    return summarise_analyses(out)


def _structure(symbol: str, close: float, protected: float, event: float, hourly: float) -> Dict[str, Any]:
    return {"symbol": symbol, "instrument": f"NSE_INDEX:{'NIFTY50' if symbol == 'NIFTY' else 'NIFTYBANK'}", "tf": "1d", "as_of": AS_OF,
            "bars": 740, "last_close": close, "reason": None,
            "chart_state": {"direction": "up", "protected": protected, "range_high": None, "range_low": None, "last_event": "BOS",
                            "last_event_price": event},
            "timeframes": [{"tf": "1w", "direction": "range", "protected": None, "bars": 150, "as_of": AS_OF, "last_event": None, "last_event_price": None},
                           {"tf": "1d", "direction": "up", "protected": protected, "bars": 740, "as_of": AS_OF, "last_event": "BOS",
                            "last_event_price": event},
                           {"tf": "1h", "direction": "down (weak)", "protected": hourly, "bars": 900, "as_of": AS_OF, "last_event": "CHoCH",
                            "last_event_price": round(close + 50, 2)},
                           {"tf": "15m", "direction": "up", "protected": round(close - 120, 2), "bars": 1000, "as_of": AS_OF, "last_event": None,
                            "last_event_price": None}],
            "levels": [{"kind": "resistance", "price": round(close + 160, 2), "degree": 2, "status": "live"},
                       {"kind": "support", "price": round(close - 250, 2), "degree": 2, "status": "live"}]}


def _open_trades() -> Any:
    """Two made-up open PAPER trades: a NIFTY future whose stop was widened, and a BANKNIFTY call in profit."""
    from datetime import date

    from app.db.models import TradeRecord
    fut = TradeRecord(id=51, tenant_id=1, user_id=1, mode="PAPER", symbol="NIFTY26OCTFUT", strategy_id="pullback-to-demand", direction="LONG",
                      entry_time=datetime(2026, 10, 20, 5, 0, tzinfo=timezone.utc), entry_price=24900.0, quantity=75.0, stop_loss=24700.0,
                      initial_stop_loss=24760.0, mark_price=24850.5, mark_time=NOW, instrument_kind="FUT", underlying_symbol="NIFTY",
                      expiry=date(2026, 10, 27))
    call = TradeRecord(id=52, tenant_id=1, user_id=1, mode="PAPER", symbol="BANKNIFTY26OCT52000CE", strategy_id="handover-continuation",
                       direction="LONG", entry_time=datetime(2026, 10, 21, 4, 0, tzinfo=timezone.utc), entry_price=410.0, quantity=30.0,
                       stop_loss=300.0, initial_stop_loss=300.0, mark_price=455.0, mark_time=NOW, instrument_kind="OPTION",
                       underlying_symbol="BANKNIFTY", underlying_direction="LONG", expiry=date(2026, 10, 23))
    return [call, fut]


def _positions() -> Dict[str, Any]:
    from app.ai.tools.copilot_trades import group_rows
    rows = group_rows(_open_trades())
    risks = [r["loss_if_stop_hit"] for r in rows if (r["loss_if_stop_hit"] or 0) > 0]
    return {"count": len(rows), "positions": rows, "total_loss_if_stops_hit": round(sum(risks), 2),
            "total_marked_pnl": round(sum(r["marked_pnl"] for r in rows), 2), "unpriced": 0, "bullish": 2, "bearish": 0, "neutral": 0,
            "realised_today": -1545.0, "closed_today": 1, "read_at": AS_OF}


def _last_trade() -> Dict[str, Any]:
    return {"trade": {"id": 41, "symbol": "NIFTY26OCTFUT", "mode": "PAPER", "side": "bullish", "setup": "pullback-to-demand",
                      "entry_date": "2026-10-20", "exit_date": "2026-10-20", "planned_entry": 24620.0, "entry_price": 24625.0,
                      "exit_price": 24605.0, "quantity": 75.0, "initial_stop": 24560.0, "stop_at_exit": 24540.0, "planned_rr": 2.0,
                      "risk_planned": 4875.0, "pnl": -1545.0, "charges": 45.0, "r_multiple": -0.32, "exit_reason": "MANUAL",
                      "exit_words": "you closed it by hand", "held_minutes": 95, "stop_changes": 1, "mfe_r": 0.4, "mae_r": 0.9,
                      "tags": [{"tag": "moved_stop_against", "label": "stop moved against the position", "status": "SUGGESTED", "source": "AUTO"}]}}


def _weekly_review() -> Dict[str, Any]:
    """A made-up journal week through the discipline engine's own score and review builder."""
    from app.journal.review import ReviewTrade, build_review
    from app.journal.score import TagState, weekly_score
    from app.journal.settings import DisciplineSettings
    s = DisciplineSettings()
    trades = [ReviewTrade(41, "pullback-to-demand", "t:41", -1545.0, 45.0, 4875.0, 95.0),
              ReviewTrade(42, "pullback-to-demand", "t:42", 3120.0, 40.0, 2600.0, 210.0),
              ReviewTrade(43, "handover-continuation", "t:43", -980.0, 38.0, 1500.0, 30.0),
              ReviewTrade(44, "handover-continuation", "t:44", 1460.0, 35.0, 1200.0, 160.0)]
    tags = [TagState(41, "moved_stop_against", "SUGGESTED"), TagState(43, "revenge_trade", "CONFIRMED")]
    score = weekly_score({t.id: t.unit for t in trades}, tags, s)
    history = [{"week": "2026-W42", "score": 78, "trades": 6, "net_pnl": 2310.0, "avg_r": 0.35, "win_rate": 50.0},
               {"week": "2026-W41", "score": 71, "trades": 5, "net_pnl": -840.0, "avg_r": -0.12, "win_rate": 40.0},
               {"week": "2026-W40", "score": 85, "trades": 4, "net_pnl": 1220.0, "avg_r": 0.41, "win_rate": 50.0}]
    review = build_review("2026-W43", trades, [(t.trade_id, t.tag) for t in tags], score, history, s)
    patterns = [{"tag": "moved_stop_against", "label": "stop moved against the position", "weeks": 3, "of_weeks": 4, "trades": 4},
                {"tag": "revenge_trade", "label": "revenge trade", "weeks": 2, "of_weeks": 4, "trades": 2}]
    data = {"week": review["week"], "totals": review["totals"], "behaviours": review["behaviours"], "costliest": review["costliest"],
            "discipline": {"score": review["discipline"].get("score")}, "history": review["history"], "lines": review["lines"],
            "what_worked": review["what_worked"], "what_lost": review["what_lost"], "patterns": patterns, "weeks_read": 4}
    return json.loads(json.dumps(data, default=str))


def ok(data: Any, source: str) -> Dict[str, Any]:
    return {"ok": True, "data": data, "as_of": AS_OF, "source": source}


def build() -> Dict[str, Any]:
    context = {"as_of": AS_OF,
               "structure": {"status": "ok", "market": "NIFTY 50", "close": 24850.5, "as_of": "2026-10-20", "daily": "UPTREND", "weekly": "RANGE"},
               "vix": {"status": "ok", "close": 12.4, "change_pct": -3.1, "percentile": 18.5, "as_of": "2026-10-20"},
               "fii_dii": {"status": "na", "reason": "No FII / DII source with clear terms of use is configured yet"},
               "breadth": {"status": "ok", "advances": 1210, "declines": 840, "unchanged": 60, "ratio": 1.44, "as_of": "2026-10-20"},
               "global": {"status": "ok", "as_of": AS_OF, "markets": [
                   {"key": "SP500", "name": "S&P 500", "group": "US", "last": 5800.2, "change_pct": 0.42},
                   {"key": "NASDAQ", "name": "Nasdaq Composite", "group": "US", "last": 18310.0, "change_pct": 0.65},
                   {"key": "US10Y", "name": "US 10-year yield", "group": "RATES", "last": 4.21, "change_pct": -0.5},
                   {"key": "DXY", "name": "US dollar index", "group": "FX", "last": 103.1, "change_pct": 0.1}]},
               "events": {"status": "ok", "count": 1, "items": [
                   {"date": "2026-10-27", "start_time": None, "kind": "EXPIRY", "scope": "NIFTY", "description": "NIFTY monthly expiry", "today": False}]}}
    zones = {"symbol": "NIFTY", "instrument": "NSE_INDEX:NIFTY50", "as_of": AS_OF, "price": 24850.5, "live_count": 5, "fallback": True, "reason": None,
             "bands": [{"role": "supply", "zone_type": "SUPPLY_ZONE", "family": "zones", "low": 25080.0, "high": 25160.0, "state": "fresh", "touches": 0,
                        "distance_pct": 0.92, "timeframe": "1d"},
                       {"role": "demand", "zone_type": "DEMAND_ZONE", "family": "zones", "low": 24520.0, "high": 24610.0, "state": "tested",
                        "touches": 1, "distance_pct": 0.97, "timeframe": "1d"}]}
    risk = {"capital": 200000.0, "risk_per_trade_pct": 1.0, "max_daily_loss_pct": 3.0, "max_trades_per_day": 5, "max_open_positions": 3,
            "max_consecutive_losses": 3, "min_risk_reward": 1.5, "lot_size": 75, "max_portfolio_risk_pct": 6.0}
    failed = {"ok": False, "error": "no lake bars for this instrument and timeframe in the window"}
    return {
        "_note": "Synthetic, hand-made tool results for the Copilot chat evals (backend/evals/build_copilot_fixtures.py). Not market data.",
        "default": {
            "get_market_context": ok(context, "context_panel"),
            "get_chart_structure:NIFTY": ok(_structure("NIFTY", 24850.5, 24310.0, 24790.0, 25020.0), "price_action_layer:nse_bhav"),
            "get_chart_structure:BANKNIFTY": ok(_structure("BANKNIFTY", 52100.0, 51200.0, 51950.0, 52480.0), "price_action_layer:nse_bhav"),
            "get_market_zones:NIFTY": ok(zones, "market_zones"),
            "get_option_chain_analyses:NIFTY": ok(_analyses(), "option_chain_analyses:broker:paper"),
            "get_risk_limits": ok(risk, "risk_settings"),
            "get_my_positions": ok(_positions(), "trades"),
            "get_my_last_trade": ok(_last_trade(), "trades"),
            "get_weekly_review": ok(_weekly_review(), "journal"),
        },
        "no_data": {
            "get_market_context": {"ok": False, "error": "get_market_context is not enabled for this organisation (flag context_panel)"},
            "get_chart_structure:NIFTY": failed, "get_chart_structure:BANKNIFTY": failed,
            "get_market_zones:NIFTY": {"ok": False, "error": "no zones for this instrument"},
            "get_option_chain_analyses:NIFTY": {"ok": False, "error": analyses_no_broker()},
            "get_risk_limits": ok({"note": "the organisation has not set its own risk limits; platform defaults apply"}, "risk_settings"),
            "get_my_positions": ok({"count": 0, "positions": [], "total_loss_if_stops_hit": 0.0, "total_marked_pnl": None, "unpriced": 0,
                                    "bullish": 0, "bearish": 0, "neutral": 0, "realised_today": 0.0, "closed_today": 0, "read_at": AS_OF}, "trades"),
            "get_my_last_trade": ok({"trade": None, "reason": "you have no closed trade yet"}, "trades"),
            "get_weekly_review": {"ok": False, "error": "get_weekly_review is not enabled for this organisation (flag discipline_engine)"},
        },
    }


def analyses_no_broker() -> str:
    from app.option_chain.analyses_routes import NO_BROKER
    return NO_BROKER


if __name__ == "__main__":
    OUT.write_text(json.dumps(build(), indent=1, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
