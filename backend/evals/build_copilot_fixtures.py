"""Builds `evals/sets/copilot_tools.json`: the recorded tool results the Copilot chat evals run on (no network, no model).

Every value is synthetic and hand-made (no broker, vendor or Trade data): a made-up option chain is passed through ATP's
real option chain analyses (app/option_chain/analyses.py) and the summaries of app/ai/tools/copilot.py, so the shapes are
exactly what the tools return. Regenerate after a change to those shapes, then pin the new sha256 in
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
            "max_consecutive_losses": 3, "min_risk_reward": 1.5, "lot_size": 75}
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
        },
        "no_data": {
            "get_market_context": {"ok": False, "error": "get_market_context is not enabled for this organisation (flag context_panel)"},
            "get_chart_structure:NIFTY": failed, "get_chart_structure:BANKNIFTY": failed,
            "get_market_zones:NIFTY": {"ok": False, "error": "no zones for this instrument"},
            "get_option_chain_analyses:NIFTY": {"ok": False, "error": analyses_no_broker()},
            "get_risk_limits": ok({"note": "the organisation has not set its own risk limits; platform defaults apply"}, "risk_settings"),
        },
    }


def analyses_no_broker() -> str:
    from app.option_chain.analyses_routes import NO_BROKER
    return NO_BROKER


if __name__ == "__main__":
    OUT.write_text(json.dumps(build(), indent=1, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
