"""Phase M / V4.13: platform-wide operator controls, read on every new entry.

- **Maintenance mode**: no new entries anywhere (PAPER or LIVE); exits, monitoring and the API
  keep working; the UI shows the operator's message. Distinct from the global kill switch: this is
  planned and announced, the kill switch is the emergency stop.
- **Disabled brokers**: no new LIVE entries through a named broker (its API is degraded, or the
  operator is rotating credentials). Exits still go through.
- **Per-user trading disable** lives on `users.trading_disabled_reason` (set by the tenant OWNER
  or a platform admin) and is checked here too.
"""
import json
from typing import Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.db.models import PlatformControlRecord, User
from app.platform import deployment_phase

KEY_MAINTENANCE = "maintenance_mode"
KEY_DISABLED_BROKERS = "disabled_brokers"


async def _get(session: AsyncSession, key: str) -> Dict:
    row = await session.get(PlatformControlRecord, key)
    if row is None:
        return {}
    try:
        value = json.loads(row.value_json or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}       # a malformed row reads as unset (never a crash)


async def _set(session: AsyncSession, key: str, value: Dict, user: Optional[User]) -> None:
    row = await session.get(PlatformControlRecord, key)
    if row is None:
        row = PlatformControlRecord(key=key)
        session.add(row)
    row.value_json = json.dumps(value)
    row.updated_by = user.id if user else None


async def status(session: AsyncSession) -> Dict:
    maintenance = await _get(session, KEY_MAINTENANCE)
    brokers = await _get(session, KEY_DISABLED_BROKERS)
    return {"maintenance_mode": bool(maintenance.get("on")), "maintenance_message": maintenance.get("message") or None,
            "disabled_brokers": sorted(brokers.get("names") or [])}


async def set_maintenance(session: AsyncSession, user: User, *, on: bool, message: Optional[str]) -> Dict:
    await _set(session, KEY_MAINTENANCE, {"on": on, "message": (message or "").strip()[:300]}, user)
    await write_audit_log(session, None, user.id, "maintenance_mode_" + ("on" if on else "off"), (message or "")[:200])
    await session.commit()
    return await status(session)


async def set_disabled_brokers(session: AsyncSession, user: User, names: List[str]) -> Dict:
    clean = sorted({n.strip().lower() for n in names if n and n.strip()})
    await _set(session, KEY_DISABLED_BROKERS, {"names": clean}, user)
    await write_audit_log(session, None, user.id, "brokers_disabled", ",".join(clean) or "-")
    await session.commit()
    return await status(session)


async def entry_blocks(session: AsyncSession, *, user: Optional[User], mode: str, broker_name: Optional[str]) -> List[str]:
    """Reasons a new entry is refused by platform controls (empty when none apply)."""
    reasons: List[str] = []
    current = await status(session)
    if current["maintenance_mode"]:
        reasons.append("Platform maintenance: no new entries" + (f" - {current['maintenance_message']}" if current["maintenance_message"] else ""))
    if mode == "LIVE":
        # Z10-c: the Phase 0 PAPER-only lock (flag phase0_paper_only, off by default). Entries only - exits never come here.
        from app.platform import phase0_lock
        if await phase0_lock.is_on(session, user.tenant_id if user is not None else None):
            reasons.append(phase0_lock.ENTRY_REASON)
    if mode == "LIVE" and broker_name and broker_name.lower() in current["disabled_brokers"]:
        reasons.append(f"Broker {broker_name} is disabled by the platform operator - LIVE entries refused")
    if user is not None and user.trading_disabled_reason:
        reasons.append(f"Trading disabled for this user: {user.trading_disabled_reason}")
    return reasons


# --- Phase N4: feature flags ---------------------------------------------------------------------
# Kill-flag semantics: a feature is ON unless the operator turned its flag off, optionally keeping
# it on for an allow-list of tenants (staged rollouts / beta access). Unknown flags are ON.
KEY_FEATURE_FLAGS = "feature_flags"

FEATURE_FLAGS: Dict[str, str] = {
    "ai_copilot": "AI strategy drafts, regime engine and monitoring agent",
    "marketplace": "Strategy marketplace listings and subscriptions",
    "public_api": "Public REST API keys and /api/public/v1",
    "backtest_optimizer": "Parameter optimisation grid runs",
    "live_trading": "New LIVE deployments (PAPER unaffected; exits always work)",
    # Z10-c (Phase 0): the PAPER-only lock (app/platform/phase0_lock.py). OFF by default. The one exception is the explicit
    # Phase 0 deployment mode (ATP_DEPLOYMENT_PHASE=phase0, app/platform/deployment_phase.py, owner decision 2026-10-12):
    # it seeds this flag ON and `live_trading` OFF where no entry is stored - the restrictive direction only.
    "phase0_paper_only": "Phase 0 PAPER-only lock: every new LIVE entry is refused, at entry checks and at the broker-order chokepoint (exits of LIVE positions always run)",
    "self_signup": "Public registration (off: invite-only)",
    "news_feed": "Live news feed from public sources (unverified items, shared ingest, tenant-key classification)",
    "telegram_inbound": "Telegram commands and approve/reject buttons from whitelisted chats (PAPER + reduce/pause only)",
    "market_thesis": "Per-symbol market thesis with scenarios and a shadow-only size multiplier (never applied)",
    "options_builder": "Options Strategy Builder: payoff, T+0 model, PoP and strike suggestions (research only, places no orders)",
    "options_builder_saved": "Options Builder: save strategies and load them again, shared within the organisation (research data only)",
    # P0.8-D compliance switches, OFF until the operator has the SEBI RA/IA position cleared.
    "marketplace_ai_listings": "Allow AI-originated strategies (AI drafts, strategist adoptions) to be listed on the marketplace",
    "thesis_stock_targets": "Show price targets and a confidence % in the thesis of a single stock (indices always show them)",
    # S1d (ADR-0021): the ScreenQL screener API (validate, saved screens, runs on server data). OFF: the scanner is unchanged.
    "screener_v2": "Screener v2: ScreenQL validation, saved screens and runs on the organisation's broker data",
    "screener_intrabar": "Intrabar alerts: rules may fire on the bar still forming (at most once per bar); bar-close stays the default",
    # H-C2 (ADR-0019): the Copilot agent (typed read tools, bounded loop). OFF: the single-shot Copilot is unchanged.
    "ai_agent": "Copilot agent: the AI reads typed, read-only tools (market memory, news, positions, P&L, risk limits) before answering",
    # H-C3 (spec C3): the strategy research loop - AI drafts backtested on server bars, every trial in a ledger, a
    # deflated report. Nothing deploys. OFF until the operator turns it on.
    "ai_research": "Strategy research loop: AI drafts are backtested in-sample on server bars, recorded as trials, and reported deflated",
    # PW6 (beta spec 4a, Phase 0): the closed-beta gate. ON: sign-up needs a SUPER_ADMIN-issued invite code and stops at
    # BETA_COHORT_CAP accounts. OFF: registration is exactly as before (self_signup still decides). OFF by default.
    "beta_cohort_gate": "Closed beta: sign-up needs an invite code and stops at the cohort cap (BETA_COHORT_CAP, default 500)",
    # BYOK-V1 (docs/BYOK.md): each user's own AI provider key for Vision chart review. OFF: the endpoints answer 404 and
    # the Settings section says it is not enabled. Vision only comments; it never places, blocks or changes an order.
    "vision_byok": "Vision with your own AI key: users store their own provider API key (encrypted), test it and set spend caps",
    # PW5 (beta spec 4a): a proposal links to a pre-filled order in the user's own broker app where the broker offers
    # such a link (docs/BROKER_DATA_TERMS.md). The user confirms there; ATP sends nothing to the broker API.
    "broker_app_links": "\"Trade in your broker app\" links on proposals (pre-filled basket in the user's own broker app; ATP places no order)",
    # Z6 / P1-e (ADR-0026): journal auto-capture, behaviour tags and the weekly discipline score. OFF: no capture, the
    # /api/journal routes answer 503 and the Journal page says it is not enabled. Numbers only; it never touches an order.
    "discipline_engine": "Discipline engine: auto-journal of every trade, behaviour tags, weekly discipline score and review (numbers only)",
    # BT1 (beta spec §2/§4a, Phase 0): live data only through each user's own broker session at a broker whose data terms
    # are confirmed (docs/BROKER_DATA_TERMS.md), never pooled or shown to another user; everything else EOD; every number
    # carries its data tier. OFF: market data reads are exactly as before. Places no order.
    "byob_data_tier": "Bring-your-own-broker data tier: live data only through each user's own confirmed broker session, EOD otherwise, data-tier badges",
    # HB0 (docs/design/HB_HISTORY_BACKFILL.md): lets the operator run the R2 history import by hand (CLI). OFF: the
    # importer refuses to start. Nothing runs on a schedule; 1-minute data only ever goes to the owner's tenant.
    "history_import": "History import from the R2 store: daily bars into the shared lake, 1-minute bars into the owner's private store",
    # HB1 (docs/design/HB_HISTORY_BACKFILL.md): the daily NSE bhavcopy backfill (CLI), the nightly EOD fetch (worker) and
    # the admin's manual file / corporate-action / holiday uploads. OFF: all of them refuse and nothing contacts NSE.
    "history_backfill": "Daily NSE bhavcopy backfill and nightly EOD fetch into the shared lake (cash, indices, F&O futures)",
    # HB2 (docs/design/HB2_INTRADAY_HISTORY.md): the owner's 1-minute history - captured after the close and backfilled
    # through the OWNER's own broker session (read-only candle endpoints) into the owner's private store only, sharing
    # the worker's rate budget with a reserve kept for exits. OFF: the job, the CLI and the worker hook do nothing.
    "history_intraday": "Owner intraday history: 1-minute bars from the owner's own broker session into the owner's private store (read-only, places no orders)",
    # Z3 / BT2 (beta spec, Phase 0): every automatic entry (deployments, TradingView webhooks, public API signals) becomes
    # an ENTRY proposal (Approve / Reject / Snooze); Approve places a PAPER order at a re-quoted price. Target exits ask
    # "Exit now?"; protective exits (stop, time stop, square-off, kill switch) stay automatic (ADR-0004). OFF: unchanged.
    "approval_only_entries": "Approval-only entries: automatic entries become proposals; Approve places a PAPER order at a re-quoted price",
    # Z3-b / BT3: each user's own PAPER account (starting capital, balance, P&L after charges, positions, journal), the
    # progression ladder card (PAPER stage only in Phase 0) and the Telegram /status /pause /resume /paper commands.
    "paper_account": "Per-user PAPER account, progression ladder card and Telegram /status /pause /resume /paper",
    # Z5 / P1-d (P1 spec 1.1, 1.2, 2.2): the plan-before-trade PAPER ticket (a plan, a checklist, quantity sized from the
    # user's risk cap, a logged and counted override, the cost line), the per-user daily loss lockout (new PAPER entries
    # only; exits are never locked) and the short-strike breach chance on proposal cards. OFF: nothing changes.
    "risk_by_default": "Plan-before-trade PAPER ticket, per-user risk cap sizing with logged overrides, daily loss lockout (exits allowed) and cost line",
    # Z5 (Trade port): India VIX gate - a spike against the previous close pauses new bullish index entries for the day;
    # a high VIX level cuts the risk per trade. Exits are never gated. OFF: nothing changes.
    "vix_gate": "India VIX gate: a VIX spike pauses new bullish index entries for the day, a high VIX cuts the size (exits never gated)",
    # U9 TX1 (docs/specs/ATP_SCREENER_TAXONOMY_ADDENDUM.md): the screen library taxonomy (method x setup) and the gallery
    # (method rail, setup chips) on the Screener page. OFF: /api/screener/taxonomy answers 503 and the page is unchanged.
    # Files screens only - never ranks or recommends one.
    "screener_gallery": "Screen gallery: browse pre-built screens by method and setup type on the Screener page (research only)",
    # Z10-d (G4, docs/PERFORMANCE.md): screener runs and bar-close alert rules evaluate the symbols that share a bar index
    # together (app/screener/panel.py) - the same matches, computed once over the universe. OFF: per symbol, as before.
    "screener_vectorized": "Vectorised screens: screener runs and bar-close alerts evaluate the whole universe at once (same matches, less time)",
    # Z10-b (G5, docs/design/Z10_LAUNCH_HARDENING.md): stale data -> zero alerts. The bar-close engine, the rule flush and
    # the dispatcher hold back a screen / instrument alert whose bars are no longer current. Trade, exit, risk and
    # system notifications are never held back. OFF: unchanged.
    "alerts_stale_guard": "Stale-data guard for alerts: no screen or instrument alert is recorded or sent on market data that is no longer current",
    # Z10-b: outbox reliability - a worker claims a delivery with a lease before sending, one idempotency key per
    # delivery, a DEAD state after the last attempt, operator replay of dead letters. OFF: the outbox is unchanged.
    "alerts_reliable_outbox": "Reliable alert outbox: claimed deliveries (never sent twice by two workers), idempotency keys, dead letters and replay",
    # Z10-f (docs/design/Z10_LAUNCH_HARDENING.md): the how-to support bot over the help and legal pages. No tools (reads
    # no positions, touches no order), refuses advice, hands low-confidence questions to the feedback form. OFF: 404.
    "support_bot": "Support bot: how-to answers from the help and legal pages (no advice, no account data, no orders)",
    # Z10-f: the public legal pages (Privacy notice, Terms of use, Risk disclosure), the acceptance box on sign-up and
    # team join, and the acceptance banner. OFF: the routes answer 404 and sign-up is unchanged. Refused while a document
    # still has an open [PLACEHOLDER] unless the operator overrides (`override_open_placeholders`).
    "legal_pages": "Legal pages (Privacy notice, Terms of use, Risk disclosure), acceptance at sign-up and the acceptance banner",
    # U6 BK1 (docs/specs/ATP_BREAKOUT_SCREENERS_ADDENDUM.md): the breakout screens (B1/B2/B4/B7/B13/B14), the quality
    # overlay and the style / timeframe selector in the screener. OFF: the breakout functions are hidden and refused.
    # Research only - matches, never orders.
    "screener_breakouts": "Breakout screens: range, swing, NR7, 52-week, ATR and volume breakouts with a quality grade (research only, places no orders)",
    # U6 BK3 (spec §8 BK3): the VCP breakout + Watchlist (B3) and the indicator breakouts - Donchian (B10), Bollinger / TTM
    # squeeze (B11), moving average (B12), RSI / ADX regime (B15). Needs `screener_breakouts` too. OFF: their ScreenQL
    # functions are hidden and refused, their templates and screens are left out of the catalog and the run, and alert
    # rules using them pause. Research only - matches, never orders.
    "screener_breakouts_bk3": "Breakout screens, set 2: VCP breakout and watchlist, Donchian, Bollinger / TTM squeeze, moving-average and RSI / ADX regime breakouts (needs screener_breakouts; research only, places no orders)",
    # U8 EV1 + EV2 (docs/specs/ATP_SCREEN_EVIDENCE_CHARTS_ADDENDUM.md): evidence packs stored with screen runs and alert
    # events, "Show on chart" with the verify strip, and the evidence image on Telegram (the user's own linked chat).
    # OFF: no pack is built or stored, the evidence endpoints answer 404 and alerts go out as text only. Informational.
    "evidence_charts": "Evidence charts: each screen match drawn with only the screen's own conditions, a verify strip with the engine's numbers, and the image on Telegram (informational, places no orders)",
    # Z10-e (G7): the public read-only status page (/status, GET /api/status): API, worker, data feed, alerts outbox lag
    # and the incident banner, from the existing health probes. No tenant data, no internal detail. OFF: /api/status
    # answers 404 and the page says it is not enabled.
    "public_status_page": "Public status page: API, worker, data feed, alert delivery and incidents at /status (no login, no tenant data)",
    # Z7a-2 (docs/specs/ATP_MARKET_ZONES_PORT.md): the nightly market-zones job - demand / supply, order blocks, S/R and
    # gaps with their lifecycle and level memory, computed from the shared lake bars and stored as-of. Reference data only.
    # Z7a-3: also the chart layer (<= 6 bands) and the ScreenQL zone functions (NearZone, ZoneRole, ZoneState,
    # ZoneStrength). OFF: the layer answers 503, the functions are hidden and refused, zone alert rules pause.
    "market_zones": "Market zones: nightly demand / supply, order-block, S/R and gap zones with lifecycle and level memory, the chart layer and the ScreenQL zone functions (read-only, places no orders)",
    # U7 LG1 (docs/specs/ATP_LEGENDS_SCREENS_ADDENDUM.md): the Legends cards - publicly described methods with a screen
    # modelled on each. OFF: /api/screener/legends answers 503 and the Legends screens are hidden from the taxonomy.
    # Educational only - never a recommendation, never an endorsement.
    "screener_legends": "Legends: cards on publicly described trading methods, each with an editable screen (educational only)",
    "scan_then": "Scan Then (N1 U4 X1): after a scan alert matches, the level gate watches the symbol at its market zones and a confirmed setup is sent as a notification or a PAPER entry proposal (never an order; needs execution_core)",
    # CH5 (docs/design/CHARTING.md): the multi-chart workspace - 1/2/4/6/8 charts, symbol / interval / crosshair link
    # groups, saved layouts per user, keyboard shortcuts, chart types. OFF: /api/charts/layouts answers 503 and the
    # workspace is not offered. Charts only; nothing places an order.
    "chart_workspace": "Multi-chart workspace: 1/2/4/6/8 linked charts, saved layouts, shortcuts and chart types (places no orders)",
    "execution_core": "Execution core (N1 E1): entry intents from scans are queued and routed by one policy to a notification or a PAPER entry proposal (never a broker order)",
    # N2-1 (P1 spec 1.4 / 1.5, docs/design/N2_P1FGH.md): the progression ladder's discipline-score criterion (from the Z6
    # journal, when `discipline_engine` is on too) and the user's own figures on the ladder card - expectancy, profit
    # factor, payoff ratio, a Kelly reference and a risk-of-ruin bound from their own closed PAPER trades. OFF: the ladder
    # answers exactly as before. Information only: never used for sizing, never a recommendation.
    "ladder_evidence": "Ladder evidence: the discipline-score criterion and your own figures (expectancy, profit factor, Kelly reference, risk-of-ruin bound) on the PAPER ladder card (information only)",
    # N2-2 (P1 spec 1.4, docs/design/N2_P1FGH.md): experience levels from the user's own PAPER record (Beginner /
    # Intermediate / Advanced) gate new futures entries (Intermediate) and naked short options (Advanced), single-leg and
    # structures; an explicit opt-out with a reason lifts one gate for that IST day, logged and counted. Exits are never
    # gated. OFF: nothing is gated and /api/paper/experience answers 404.
    "experience_levels": "Experience levels: futures and naked short option entries open with your PAPER record; a logged, counted opt-out lifts a gate for the day (exits never gated)",
    # N4 CH3 (docs/design/CHARTING.md): the price-action chart layer - causal swings, legs, real breaks, swing levels and
    # the Dow trend per degree from the shared lake bars as of a time, with toggles, a debug mode and bar replay that
    # recomputes the layer as of each replayed bar. OFF: GET /api/charts/layers/price-action answers 503 and the chart
    # shows no layer. Reference only - places no orders.
    # N3 S6a: screen history - who passed a daily screen on a past date (point in time: bars closed by then, index
    # membership as of then, delisted names included) and screen fitness (forward returns of its matches against the
    # universe, deflated through the research trial ledger). Shared end-of-day lake bars only. OFF: the routes answer 503.
    "screener_history": "Screen history: who passed on a past date (point in time) and screen fitness - forward returns of matches vs the universe, deflated (information only)",
    # N1 F1: the user's own PAPER book as one risk view - exposure, open risk at the stops by sector bucket (all indices
    # in one), beta to NIFTY 50 from the lake's Daily closes, and the one-sided direction warning. OFF:
    # GET /api/portfolio/risk answers 503. Read-only: it refuses nothing and places no orders.
    "portfolio_risk": "Portfolio risk view of the user's own PAPER book: exposure, open risk by sector bucket, beta to NIFTY 50, one-sided warning (read-only)",
    # N1 F2: the user's own portfolio caps (max exposure in one sector bucket, max open risk at the stops in one
    # bucket, max open positions; each 0 = no limit, set in the PAPER risk settings) refuse new PAPER entries before a
    # proposal and at Approve. Acts only while portfolio_risk is on too. Exits are never refused (ADR-0004).
    "portfolio_risk_caps": "Portfolio caps on new PAPER entries: sector exposure, open risk per bucket, open positions (entries only; exits never refused)",
    # N4 CH6 (docs/design/CHARTING.md): ScreenQL expressions drawn as chart studies, computed by the screener's own runtime
    # from the shared lake bars as of a time (lines for numbers, marks for conditions). OFF: GET /api/charts/studies
    # answers 503 and the chart shows no study panel. Reference only - places no orders.
    "chart_studies": "Chart studies: ScreenQL expressions drawn on the chart by the screener's own engine, as of any bar (reference only, places no orders)",
    "chart_price_action": "Price-action chart layer: swings, legs, breaks, swing levels and trend per degree, as of any bar, with replay (reference only, places no orders)",
    # N2-3 (P1 spec 1.5, docs/design/N2_P1FGH.md): decision tools - the risk-of-ruin / Kelly calculator on typed inputs
    # (prefilled from the user's own figures) and the what-if for the user's own open positions (P&L now, on a later
    # day with an IV shift, and at expiry, at the spot and IVs typed in). OFF: /api/decision/* answers 404. Information
    # only: nothing sizes, places or closes an order.
    # N2-4: also the exposure view (own positions by underlying and sector, beta, correlated pairs, from the daily lake)
    # and the expected-move cone on the chart.
    "decision_tools": "Decision tools: risk-of-ruin and Kelly calculator, what-if for your own open positions, exposure and correlation view, expected-move cone on the chart (information only)",
    # N2-5 (P1 spec 1.5, docs/design/N2_P1FGH.md): the market context panel - regime (Dow structure of the NIFTY 50 on
    # daily and weekly bars), breadth, India VIX with its 1-year rank, FII / DII (seam, off until a source is set),
    # global cues and this week's events, each tile with its source and as-of and NA on its own failure. Shared data
    # only (plus the organisation's own events). OFF: /api/context/panel answers 404.
    "context_panel": "Market context panel: regime, breadth, India VIX and its rank, FII / DII, global cues and this week's events (information only)",
    # N2-6 (P1 spec 2.4, docs/design/N2_P1FGH.md): the positions board - the caller's own open positions with P&L in ₹
    # and R, stop / target status, time in trade, plan vs current stop (a widened stop flagged) and how each exits.
    # Read-only; exits stay on the existing one-click Exit now and are never gated. OFF: /api/positions/board is 404.
    "positions_board": "Positions board: your open positions with P&L in ₹ and R, stop and target status, time in trade, plan vs current stop (read-only; exits never gated)",
    # N7 C2b: the backtest execution models after C2 PR 1 - next_open fill, spread_half / volume_share slippage, fixed_ms
    # latency (docs/design/C2_MODELS.md). OFF: a backtest naming one is refused with the reason; the defaults are unchanged.
    "backtest_models": "Backtest execution models: next-open fills, spread / volume-share slippage and order latency (opt-in per run)",
    # S10 (SCREENER_SPEC §5 S10, Phase 0 slice): the ETF screen (J) - NSE ETFs from the shared end-of-day history with
    # traded value and the tracking difference against the index each tracks. OFF: GET /api/screener/etf answers 503 and
    # the Screener page shows no ETF button. Information only, no recommendation, places no orders.
    "screener_etf": "ETF screen: NSE ETFs with traded value and tracking difference against their index, from the shared end-of-day history (information only)",
    # S10 seam: the WhatsApp alert channel (a Business gateway, approved template + opt-in, paid plans) and the SMS
    # channel's India DLT fields (entity id, template id, sender header). OFF: such a channel or field is refused (403).
    "alerts_whatsapp_dlt": "WhatsApp alert channel and SMS DLT fields (India) for out-of-app alerts",
    # N2-7 (P1 spec 3.2, docs/design/N2_P1FGH.md): builder adjustments - roll a leg, add a hedge, convert a short strangle
    # to an iron condor, close one side - with the before / after payoff and Greeks. Computes only: no order is placed,
    # staged or proposed. OFF: POST /api/options-builder/adjust answers 404 (503 first while the builder is off).
    "builder_adjustments": "Builder adjustments: roll, add a hedge, convert to an iron condor, close one side - before / after payoff and Greeks (models only, places nothing)",
    # N2-8 (P1 spec 3.2, docs/design/N2_P1FGH.md): compare up to three strategies side by side in the builder (payoff,
    # PoP, max loss, margin estimate, theta per day, breakevens). Client-side over the existing evaluate endpoint (and
    # saved strategies when options_builder_saved is on); no new route. OFF: the builder drawer does not show it.
    "builder_compare": "Builder compare: up to three strategies side by side - payoff, PoP, max loss, margin estimate, theta per day, breakevens (models only)",
    # N2-9 (P1 spec 2.1, 2.5, docs/design/N2_P1FGH.md): the Trade workspace page - chart, the P1-d PAPER ticket and the
    # positions board on one screen, keyboard shortcuts (ticket, close the focused position, flatten all with a
    # confirmation), a phone bottom-sheet ticket, market / lockout / stale states and a per-viewer saved layout. It uses
    # existing routes only (exits through Exit now, never gated). OFF: the page says it is not turned on.
    "trade_workspace": "Trade workspace: chart, PAPER ticket and positions on one screen with keyboard shortcuts and a saved layout (exits never gated)",
    # N2-10 (P1 spec 1.6, docs/design/N2_P1FGH.md): the method library (trend, level, pullback, commitment, reward to
    # risk) and glossary at /learn, each step linked to its chart layer, screens, checklist item and journal tags.
    # Education only (no advice, no performance claims; official NSE / SEBI / CBDT links only). Static frontend content,
    # no route. OFF: the page says it is not turned on and Coach & Guide shows no link.
    "education_pages": "Method library and glossary: trend, level, pullback, commitment, reward to risk (education only, no advice)",
}
# Phase BB/BE: flags that start OFF until the operator turns them on (everything else is a kill flag).
DEFAULT_OFF_FLAGS = frozenset({"phase0_paper_only", "news_feed", "telegram_inbound", "market_thesis", "marketplace_ai_listings", "thesis_stock_targets", "screener_v2", "screener_intrabar", "ai_agent", "ai_research", "alerts_stale_guard", "alerts_reliable_outbox",
                               "vision_byok", "beta_cohort_gate", "broker_app_links", "discipline_engine", "byob_data_tier", "history_import",
                               "history_backfill", "history_intraday", "options_builder_saved", "approval_only_entries", "paper_account", "screener_gallery",
                               "screener_vectorized", "risk_by_default", "vix_gate", "public_status_page", "market_zones", "screener_breakouts",
                               "support_bot", "legal_pages", "evidence_charts", "screener_legends", "ladder_evidence", "execution_core", "chart_price_action", "chart_studies", "portfolio_risk", "portfolio_risk_caps", "experience_levels", "chart_workspace", "scan_then", "screener_breakouts_bk3", "screener_etf", "alerts_whatsapp_dlt", "backtest_models", "screener_history", "decision_tools",
                               "context_panel",
                               "positions_board", "builder_adjustments", "builder_compare", "trade_workspace", "education_pages"})


async def feature_flags(session: AsyncSession) -> Dict[str, Dict]:
    stored = (await _get(session, KEY_FEATURE_FLAGS)).get("flags") or {}
    if not isinstance(stored, dict):
        stored = {}
    result: Dict[str, Dict] = {}
    for name, description in FEATURE_FLAGS.items():
        entry = stored.get(name) or {}
        # Phase 0 deployment mode only: a seeded flag with no stored entry resolves to its (restrictive) seed value.
        phase_default = deployment_phase.default_for(name)
        default = (name not in DEFAULT_OFF_FLAGS) if phase_default is None else phase_default
        result[name] = {"on": bool(entry.get("on", default)), "tenants": sorted(int(t) for t in entry.get("tenants") or []),
                        "description": description}
    return result


async def flag_enabled(session: AsyncSession, name: str, tenant_id: Optional[int] = None) -> bool:
    flags = await feature_flags(session)
    entry = flags.get(name)
    if entry is None:
        return True
    if entry["on"]:
        return True
    return tenant_id is not None and tenant_id in entry["tenants"]


def legal_open_placeholders() -> Dict[str, List[str]]:
    """Z10-f: {slug: [placeholder, ...]} for every legal document that still has an unfilled [PLACEHOLDER]."""
    from app.legal import documents
    return {slug: doc.open_placeholders() for slug, doc in documents.documents().items() if doc.open_placeholders()}


async def set_flag(session: AsyncSession, user: User, name: str, *, on: bool, tenants: Optional[List[int]] = None,
                   override_open_placeholders: bool = False) -> Dict[str, Dict]:
    if name not in FEATURE_FLAGS:
        raise ValueError(f"Unknown feature flag '{name}' - known: {sorted(FEATURE_FLAGS)}")
    if name == "live_trading":
        # Phase 0 deployment mode: no live_trading while phase0_paper_only is on (audited refusal, ValueError -> 400).
        await deployment_phase.refuse_live_trading_on(session, user.id, on=on, tenants=tenants)
    open_items = legal_open_placeholders() if name == "legal_pages" and (on or tenants) else {}
    if open_items and not override_open_placeholders:
        listed = "; ".join(f"{slug}: {', '.join(items)}" for slug, items in sorted(open_items.items()))
        raise ValueError(f"The legal pages still have open placeholders ({listed}). Fill them in (and have the text reviewed) "
                         "first, or switch the flag on with override_open_placeholders = true")
    if open_items:
        await write_audit_log(session, None, user.id, "legal_pages_placeholder_override",
                              ", ".join(f"{slug}:{len(items)}" for slug, items in sorted(open_items.items())))
    current = await _get(session, KEY_FEATURE_FLAGS)
    flags = current.get("flags") or {}
    flags[name] = {"on": on, "tenants": sorted({int(t) for t in (tenants or [])})}
    await _set(session, KEY_FEATURE_FLAGS, {"flags": flags}, user)
    await write_audit_log(session, None, user.id, "feature_flag_set", f"{name}: on={on} tenants={flags[name]['tenants'] or '-'}")
    await session.commit()
    return await feature_flags(session)


async def features_for_tenant(session: AsyncSession, tenant_id: Optional[int]) -> Dict[str, bool]:
    return {name: await flag_enabled(session, name, tenant_id) for name in FEATURE_FLAGS}


async def require_flag(session: AsyncSession, name: str, tenant_id: Optional[int]) -> None:
    """Raises 503 with a machine-readable header when the operator has turned a feature off."""
    if not await flag_enabled(session, name, tenant_id):
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail=f"The '{name}' feature is currently disabled by the platform operator",
                            headers={"X-Feature-Disabled": name})


# --- Phase V1: risk ceilings ----------------------------------------------------------------------
# The Risk Guardian spec keeps every default admin-configurable *with hard ceilings*: a tenant may
# set its own risk per trade, daily loss, portfolio risk and drawdown pause level, but never above
# these, and never a stop cool-down shorter than the minimum. Enforced at the settings API and,
# belt and braces, clamped at runtime on every entry.
KEY_RISK_CEILINGS = "risk_ceilings"
RISK_CEILINGS_DEFAULT: Dict[str, float] = {
    "risk_per_trade_pct": 2.0,        # spec R2: hard ceiling 2%
    "max_daily_loss_pct": 5.0,        # spec R5 default 2%; the ceiling leaves room for a wider tenant setting
    "max_portfolio_risk_pct": 10.0,   # spec R4 default 6%
    "dd_level_2_pct": 25.0,           # the pause level cannot be pushed beyond a quarter of the peak
    "min_stop_cooldown_minutes": 0.0, # spec R10: an operator may force a minimum cool-down
}
CEILING_MAX_KEYS = ("risk_per_trade_pct", "max_daily_loss_pct", "max_portfolio_risk_pct", "dd_level_2_pct")


async def risk_ceilings(session: AsyncSession) -> Dict[str, float]:
    stored = await _get(session, KEY_RISK_CEILINGS)
    out = dict(RISK_CEILINGS_DEFAULT)
    for key in out:
        if key in stored:
            try:
                out[key] = float(stored[key])
            except (TypeError, ValueError):
                continue
    return out


async def set_risk_ceilings(session: AsyncSession, user: User, values: Dict[str, float]) -> Dict[str, float]:
    current = await risk_ceilings(session)
    for key, value in values.items():
        if key not in RISK_CEILINGS_DEFAULT:
            raise ValueError(f"Unknown risk ceiling '{key}' - known: {sorted(RISK_CEILINGS_DEFAULT)}")
        number = float(value)
        if number < 0 or (key in CEILING_MAX_KEYS and number <= 0):
            raise ValueError(f"{key} must be a positive number")
        current[key] = number
    await _set(session, KEY_RISK_CEILINGS, current, user)
    await write_audit_log(session, None, user.id, "risk_ceilings_set", ", ".join(f"{k}={v:g}" for k, v in sorted(current.items())))
    await session.commit()
    return current


def ceiling_violations(cfg, ceilings: Dict[str, float]) -> List[str]:
    """Why a tenant's RiskConfig may not be saved as given."""
    errors: List[str] = []
    for key in CEILING_MAX_KEYS:
        value = float(getattr(cfg, key, 0.0) or 0.0)
        if value > ceilings[key]:
            errors.append(f"{key} {value:g} exceeds the platform ceiling {ceilings[key]:g}")
    minimum = ceilings.get("min_stop_cooldown_minutes", 0.0)
    if float(getattr(cfg, "stop_cooldown_minutes", 0) or 0) < minimum:
        errors.append(f"stop_cooldown_minutes must be at least {minimum:g}")
    return errors


def clamp_config(cfg, ceilings: Dict[str, float]):
    """(config within the ceilings, notes saying what was clamped). Runtime safety net for
    settings saved before a ceiling was lowered."""
    updates = {}
    notes: List[str] = []
    for key in CEILING_MAX_KEYS:
        value = float(getattr(cfg, key, 0.0) or 0.0)
        if value > ceilings[key]:
            updates[key] = ceilings[key]
            notes.append(f"{key} {value:g} capped at the platform ceiling {ceilings[key]:g}")
    minimum = ceilings.get("min_stop_cooldown_minutes", 0.0)
    if float(getattr(cfg, "stop_cooldown_minutes", 0) or 0) < minimum:
        updates["stop_cooldown_minutes"] = int(minimum)
        notes.append(f"stop_cooldown_minutes raised to the platform minimum {minimum:g}")
    return (cfg.model_copy(update=updates) if updates else cfg), notes


# --- Phase X: marketplace terms ---------------------------------------------------------------------

KEY_MARKETPLACE_TERMS = "marketplace_terms"
MARKETPLACE_TERMS_DEFAULT: Dict[str, float] = {
    "platform_fee_pct": 20.0,        # the platform's share of every paid listing, frozen per listing at publish
    "min_payout": 500.0,             # a creator can request a payout once this much is available (INR)
    "max_listing_price": 50000.0,    # the highest one-time price a creator may ask (INR)
}


async def marketplace_terms(session: AsyncSession) -> Dict[str, float]:
    stored = await _get(session, KEY_MARKETPLACE_TERMS)
    out = dict(MARKETPLACE_TERMS_DEFAULT)
    for key in out:
        if key in stored:
            try:
                out[key] = float(stored[key])
            except (TypeError, ValueError):
                continue
    return out


async def set_marketplace_terms(session: AsyncSession, user: User, values: Dict[str, float]) -> Dict[str, float]:
    current = await marketplace_terms(session)
    for key, value in values.items():
        if key not in MARKETPLACE_TERMS_DEFAULT:
            raise ValueError(f"Unknown marketplace term '{key}' - known: {sorted(MARKETPLACE_TERMS_DEFAULT)}")
        number = float(value)
        if key == "platform_fee_pct" and not 0 <= number <= 90:
            raise ValueError("platform_fee_pct must be between 0 and 90")
        if key != "platform_fee_pct" and number < 0:
            raise ValueError(f"{key} cannot be negative")
        current[key] = number
    await _set(session, KEY_MARKETPLACE_TERMS, current, user)
    await write_audit_log(session, None, user.id, "marketplace_terms_set", ", ".join(f"{k}={v:g}" for k, v in sorted(current.items())))
    await session.commit()
    return current
