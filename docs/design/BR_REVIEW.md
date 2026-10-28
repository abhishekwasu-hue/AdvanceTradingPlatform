# BR "Trading Brain" - independent review against the code (28 Oct 2026, origin/main 77a50057)

Exists
- Memory / coach / interview / monitor: ai/market_memory.py (AR; market_snapshots every 15 min, tenant-scoped, no user_id; reads via
  interview.analyse_market); ai/coach.py (AV; pure stats, no DB / AI); ai/interview.py (AP; trader_profiles one row per user,
  answers_json / preferences_json); ai/monitor.py (L4; rules only; ai_actions PROPOSED/APPROVED tenant + deployment_id, no user_id =
  the existing propose-then-approve path).
- Journal: journal/capture.py (Z6) hooks on_entry / on_marks (MAE/MFE) / on_stop_change / on_exit -> journal_entries (tenant + user,
  plan_json, chart_entry_json); paper/fno_journal.py. J1 / P2-8 and TP-8 not started.
- Grounding / scores / confidence: ai/grounding.py, ai/evidence.py; setup score signal_scoring/engine.py (signal_history.score);
  thesis_records.confidence already scored vs the next session (ai/thesis.py - a calibration seed); Vision drops its confidence
  (chart_analyze/contract.py).
- Copilot chat (ai/copilot_chat.py) is stateless; no setup state machine (closest: level_gate/status.py SKIPPED_GATE_* codes;
  execution/order_state_machine.py is orders only). Telegram: alert_channels / alert_deliveries, telegram_inbound/, telegram_callbacks.
- Readers: at least five today, no parity - charts/price_action_layer.py (Copilot), screener/then + level_gate + daily_trend,
  screener/runtime + breakouts, strategy_engine/* + strategy_templates/engine.py (Autopilot), price_action/market_structure
  (interview, memory, signal scoring); price_action/zones/* shared.

Extend (item -> module; migration)
- M1 -> one price_action/reader facade (P5-1 port behind it), every surface calls it, parity tests (no). M2 -> journal/capture.py +
  journal_entries context_json + outcome columns; signals / skips from signal_history and ai_actions REJECTED (yes). M3 -> pure function
  over M2 rows in journal/, no embeddings first (index only). M4 -> trader_profiles method_json + version table (yes). M5 -> copilot_chat
  route + small per-user chat context table (yes if stored).
- R1 -> coach.py + capture.on_exit; R2 -> ai_actions pattern, new LESSON action with user_id (yes); R3 -> new calibration log seeded from
  thesis scoring (yes); R4 -> existing "I don't know" lines. S1 -> setup_states table + log wrapping level_gate codes and the worker's
  evaluation (yes); S2 -> signals_feed card; S3 -> chart_analyze as critic; S4 -> ai/briefing.py + market_memory + Telegram.

Risks
- Multi-tenant: market_snapshots, ai_actions, thesis_records are tenant-only; every new row needs tenant_id + user_id (pattern
  journal/service.py:141); isolation tests before features.
- Look-ahead: M2 context from bars closed at decision time; market_memory snapshots are not decision-time contexts.
- AI cost: only BYOK Vision caps exist (ai/byok.py, llm_calls); no per-user Copilot / summary cap yet.
- Public repo: M4 / lessons are data only; generic fixtures and prompts; the owner's method only in his account row.
- Reader parity: M1 must come before M2, or episodes store disagreeing contexts.
- BR-0: SCR-FIX in progress; DATA-H in progress; P5-1 not started; AP-FIX and CP-FIX have no order file or roadmap row yet.

First BR-1 PRs (after BR-0): M1a reader facade + parity tests (no behaviour change) -> M1b surfaces one by one with an answer-key
score page -> M2 episode schema + capture + isolation / no-look-ahead tests -> M4 profile versioning -> M3 recall (minimum N) -> M5.
