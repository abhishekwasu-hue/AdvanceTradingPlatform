"""N1 U4 X1: a scan's "Then" - Notify / One-click (flag `scan_then`, off by default; intents need `execution_core`).

When a scan alert rule with a Then template matches a symbol, the level gate (Trade's level-bot rules, ported in
`app/price_action/level_gate` and `app/price_action/daily_trend`) watches that symbol for the rest of the session:

1. trend first - the symbol's Daily context from the shared lake's Daily bars, computed once per symbol and day
   (`trend.daily_context`; any failure means no entry);
2. the area - the symbol's live Z7a market zones for the organisation (`areas.areas_for`);
3. the gate on CLOSED `confirm_tf` bars (handover study, a dominant commitment candle on higher volume, real break,
   R:R) - `runner.run_due`, every worker cycle, once per new closed confirmation bar;
4. a CONFIRMED setup (on its commitment candle only) becomes an execution-core intent (`intents.submit`, one per rule,
   symbol, area and commitment bar) with the gate's ticket as evidence. The core routes it: NOTIFY -> a notification,
   ONE_CLICK (and AUTO_PAPER, which stays a proposal) -> a PAPER entry proposal (Vision audit + Telegram / in-app
   approval). AUTO_LIVE cannot be saved and the core refuses it anyway.

Nothing here places, sizes or routes an order, and nothing holds a broker: the routes only write template rows and the
runner only writes intents. Exits are never touched (ADR-0004).
"""
