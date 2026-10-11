From Abhi — Trade repo → ATP port map (English). Many useful pieces of the Trade repo are still not in ATP. First produce the map, then port in the order below. Same working rules; port = logic + golden-parity tests against the Trade functions (same input → same output), English names/docstrings, settings-driven (no hardcoded symbols, strike steps, times), multi-tenant safe. Do not port Streamlit pages, Trade's own DB layers (cloud_db.py, database.py), broker adapters ATP already has, research/diagnostic scripts.

0. Deliverable first: docs/TRADE_PORT_MAP.md — every Trade module/folder (≈140 top-level files + packages) with: purpose, ATP equivalent (path) or "none", status (ported / partial / missing / not needed + reason), parity test location. Read the Trade repo at origin/main (read-only).

1. Port inside Phase 0 (fold into Z3/Z5/Z10; these make PAPER and proposals correct)
 - order_safety.py (601 lines): freeze-quantity split, price-band/circuit checks, max order value, duplicate-order guard, stale-price guard, hedge-leg-first ordering → ATP risk/execution pre-trade checks (PAPER path now, LIVE later). ATP's hierarchy has only part of this.
 - charges.py: verify ATP's charges engine against Trade's on golden fixtures (STT, exchange, SEBI, stamp, GST, brokerage per broker); fix differences.
 - strike_breach_model.py: probability that a short strike is breached before expiry → shown on proposals and in the options builder metrics.
 - India VIX regime / spike halt (Trade gates) → risk-by-default context (size reduction / new-entry pause, exits never blocked).
 - paper/ (journal with charges and net P&L, would-have records, lot sizes from instrument master, manual trigger, PAPER-scope pause) and the Telegram commands (/status /positions /pause /resume /paper) → BT2/BT3 PAPER flow and the Telegram bot.
 - position_stream_monitor.py + trade_monitor.py exit logic (lock-protected exit loop, SL/target/time exits, never blocked, throttled updates) → PAPER exit engine in Z3 (and the base for E later).
 - expiry rule from weekly_dual_expiry_option_seller (if today is expiry, use the next weekly expiry; per-underlying expiry weekday from the calendar) → options builder / proposal strike & expiry selection.
2. Port right after Phase 0 (N-list)
 - pdf_reports.py (performance/daily PDF) → journal and weekly-review export (N2).
 - market_report.py / eod_market_report.py → merge into ATP's briefing/global-cues (N2): EOD summary with index levels, FII/DII, VIX, breadth, events.
 - level_memory.py + market_zones.py (zone lifecycle store) → price_action layers (N4) and screener levels.
 - backtest_review/ (day-by-day review views, Telegram review images) → CH3 replay + U8 evidence (N4).
 - iv_snapshot_collector + IV-change-from-sideways-baseline gate → check against ATP oi_gates (partial); complete (N7).
 - Strategy library as user templates (generic names, all parameters editable, PAPER default, white-box): pullback credit spread (pullback_credit_spread/), dynamic S/R instant (5-min), 15-min dynamic reversal, S/R levels v3, classic S/R reversal, gap-fill reversal, multi-timeframe pullback, OI-Greeks-VIX, NIFTY futures swing (RSI + Supertrend), weekly dual-expiry option seller, MCX futures (commodity, if MCX data/broker available) → strategy_engine declarative templates with backtests on the lake (N2/N7).
3. Port only when the owner marks it stable (G)
 - decision3 (v2.2 method-first engine) and its layers swings2/legs2/patterns2/zones2/trendlines2/rsi2, elliott/ counts, mtf/, simple_core/, opportunity_engine/, vision2/vision_led/. They are still changing (degree-aware Dow, correction-pattern fixture library in progress). Keep ATP's price_action module aligned by importing behaviour specs, not code, until then.
4. Reporting: the port map's summary table goes into ROADMAP_STATUS.md (counts by status); each port PR updates the map row.
