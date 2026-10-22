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
    # N2-5 (P1 spec 1.5, docs/design/N2_P1FGH.md): the market context panel - regime (Dow structure of the NIFTY 50 on
    # daily and weekly bars), breadth, India VIX with its 1-year rank, FII / DII (seam, off until a source is set),
    # global cues and this week's events, each tile with its source and as-of and NA on its own failure. Shared data
    # only (plus the organisation's own events). OFF: /api/context/panel answers 404.
    "context_panel": "Market context panel: regime, breadth, India VIX and its rank, FII / DII, global cues and this week's events (information only)",
    "options_builder": "Options Strategy Builder: payoff, T+0 model, PoP and strike suggestions (research only, places no orders)",
    "options_builder_saved": "Options Builder: save strategies and load them again, shared within the organisation (research data only)",
    # P0.8-D compliance switches, OFF until the operator has the SEBI RA/IA position cleared.
    "marketplace_ai_listings": "Allow AI-originated strategies (AI drafts, strategist adoptions) to be listed on the marketplace",
    "thesis_stock_targets": "Show price targets and a confidence % in the thesis of a single stock (indices always show them)",
    # S1d (ADR-0021): the ScreenQL screener API (validate, saved screens, runs on server data). OFF: the scanner is unchanged.
    "screener_v2": "Screener v2: ScreenQL validation, saved screens and runs on the organisation's broker data",
    "screener_intrabar": "Intrabar alerts: rules may fire on the bar still forming (at most once per bar); bar-close stays the default",
    # U2 remaining blocks (SCREENER_SPEC §3 builder; Phase 0: the blocks the shipped screens use): the formula block
    # (ScreenQL text with offsets and arithmetic, edited in place) and "Open" for Legends and breakout screens in the
    # builder through POST /api/screener/stages. OFF: /stages answers 503, the builder offers no formula block and
    # those screens' Open stays disabled. Research only - places no orders.
    "screener_builder_u2": "Screener builder: formula blocks and opening Legends / breakout screens in the builder (research only)",
    # H-C2 (ADR-0019): the Copilot agent (typed read tools, bounded loop). OFF: the single-shot Copilot is unchanged.
    "ai_agent": "Copilot agent: the AI reads typed, read-only tools (market memory, news, positions, P&L, risk limits) before answering",
    # H-C3 (spec C3): the strategy research loop - AI drafts backtested on server bars, every trial in a ledger, a
    # deflated report. Nothing deploys. OFF until the operator turns it on.
    "ai_research": "Strategy research loop: AI drafts are backtested in-sample on server bars, recorded as trials, and reported deflated",
    # Settings redesign A (docs/design/SETTINGS_REDESIGN_REVIEW.md R2, SR-1; needs `ui_v2`): one AI key for every
    # user-started AI feature - the Connect AI panel (paste, detect, consent, save, test) and app/ai/resolve.py (the
    # user's own key, else the organisation's, else the rules / `ai_not_connected`). OFF: every AI call and page behaves
    # exactly as before and /api/ai/byok/connect* answers 404. Background jobs stay on the organisation's key. No orders.
    "ai_one_key": "One AI key for every AI feature: the Connect AI panel and the user's own key first for Copilot, chart analysis and the other AI tools",
    # Master Order 4 slice 1 (app/rhythm; needs `ui_v2`): the daily rhythm - the pre-market brief, the mid-session pulse,
    # the closing wrap and the weekly review as a card on the Dashboard, and on the user's own Telegram when the user
    # turns it on (Settings > Alerts, OFF by default). Built from the user's own data and the market data ATP holds;
    # structure words only, no buy / sell / target / stop wording. OFF: /api/rhythm/* answers 404 and nothing is sent.
    "daily_rhythm": "Daily rhythm: the pre-market brief, mid-session pulse, closing wrap and weekly review on the Dashboard and, by choice, on Telegram",
    # PW6 (beta spec 4a, Phase 0): the closed-beta gate. ON: sign-up needs a SUPER_ADMIN-issued invite code and stops at
    # BETA_COHORT_CAP accounts. OFF: registration is exactly as before (self_signup still decides). OFF by default.
    "beta_cohort_gate": "Closed beta: sign-up needs an invite code and stops at the cohort cap (BETA_COHORT_CAP, default 500)",
    # BYOK-V1 (docs/BYOK.md): each user's own AI provider key for Vision chart review. OFF: the endpoints answer 404 and
    # the Settings section says it is not enabled. Vision only comments; it never places, blocks or changes an order.
    "vision_byok": "Vision with your own AI key: users store their own provider API key (encrypted), test it and set spend caps",
    # BYOK-V3 (docs/BYOK.md): the same Vision audit (each user's own key, comment only) for the screen alerts of rules the
    # user created and a daily after-close review of the user's own symbols. Needs `vision_byok` too. OFF: no scan-alert or
    # daily-review row is queued and no call is made; V2 proposal audits are unchanged.
    "vision_byok_v3": "Vision V3: your own AI key also reviews your scan alerts and a daily after-close chart review (comment only, never an order)",
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
    # AMW-3 (docs/design/AMW_PORT.md): the ScreenQL functions ported from the AMW screener's predicates - StDev,
    # RsiDivergence, SwingLabel, TimeOfDay, the v2 candles (strong body, gap reversal) in Pattern and the next-bar
    # confirmation status on Pattern / PatternBullish / PatternBearish. OFF: hidden from the registry, refused in screens,
    # studies and alert rules (an active rule pauses with the reason). A confirmation status, RsiDivergence and
    # SwingLabel fire on bar close only (intrabar rules refused); places no orders.
    "screenql_amw_functions": "AMW screener functions in ScreenQL: StDev, RSI divergence, swing labels, time of day, strong-body and gap-reversal candles, next-bar candle confirmation (research only, places no orders)",
    # Handover 11c-1 (AMW-20 first slice, docs/design/PINE_SUBSET.md): the Pine Script subset - pasted Pine translated to
    # ScreenQL signal conditions with line-numbered diagnostics, and "Run as screen" through the Screener's own run path.
    # The Pine text is parsed, never executed (a host mode: no runner, ADR-0027). OFF: /api/lab/pine/* answers 404 and the
    # Screener shows no Pine button. Places no orders.
    "strategy_lab_pine": "Pine Script subset: paste a TradingView Pine script, see what translates, and run its signal condition as a screen (never executed; places no orders)",
    # Handover 11l (docs/OPEN_QUESTIONS.md LAB-11..): the one signal language (app/signals/language.py) on the texts users
    # read - the Signals inbox's "why" lines, scan alert / Telegram texts, the brief's day headline and the Copilot
    # teacher's "on your chart" lines: structure words, no buy / sell / target / stop wording, no single price (the
    # user's own area as a range only). OFF: every one of these texts is exactly as before. Text only - places no orders.
    "signal_language_v2": "Signal language v2: structure words only on signals, alerts, the brief and the teacher (no buy / sell / target / stop wording, no single prices)",
    # Idea library slice 1 (owner order ATP_SESSION_IDEA_LIBRARY.md, T1-T8): the Ideas page (v2 shell) - generic method
    # ideas as white-box cards (plain words, structure words, the user's own settings, honest provenance; education, no
    # performance claims) with "Try as a scan" built from the ScreenQL registry. OFF: /api/ideas answers 404 and the page
    # and its navigation entry are hidden. Places no orders.
    "idea_library": "Idea library: well-known chart-reading methods as plain-words cards, each with a ready scan to try (education only, places no orders)",
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
    # U6 BK4 (spec §8 BK4): trendline / channel (B5), flag / pennant / triangle (B6), gap (B8) and retest (B9) breakouts.
    # Needs `screener_breakouts` too. OFF: their ScreenQL functions are hidden and refused, their templates and screens are
    # left out of the catalog and the run, and alert rules using them pause. Research only - matches, never orders.
    "screener_breakouts_bk4": "Breakout screens, set 3: trendline / channel, flag / pennant / triangle, gap-and-go and retest breakouts (needs screener_breakouts; research only, places no orders)",
    # U6 BK5 (spec §6): breakout screen fitness - a nightly pass computes, for every daily / weekly breakout template, the
    # forward returns and failed-break rate of its past matches on the SHARED lake's daily bars (by grade, timeframe and
    # volatility regime; small samples marked). Needs `screener_breakouts` too. OFF: the job does not run and the endpoint
    # answers 503. Informational - describes past matches, never recommends one, places no orders.
    "screener_breakouts_fitness": "Breakout screen fitness: what followed each breakout template's past matches on shared daily data, by grade, timeframe and volatility regime (informational, places no orders)",
    # U8 EV1 + EV2 (docs/specs/ATP_SCREEN_EVIDENCE_CHARTS_ADDENDUM.md): evidence packs stored with screen runs and alert
    # events, "Show on chart" with the verify strip, and the evidence image on Telegram (the user's own linked chat).
    # OFF: no pack is built or stored, the evidence endpoints answer 404 and alerts go out as text only. Informational.
    # U8 EV3 (docs/design/EVIDENCE_CHARTS.md): on top of `evidence_charts` - option-chain conditions draw the chain's OI
    # walls / max pain, every pack carries the multi-timeframe trend strip, and a pack can be replayed bar by bar from the
    # shared lake. OFF: packs as before, the replay route answers 404. Informational only.
    "evidence_context": "Evidence context: option-chain OI walls on evidence charts, the W / D / 1H trend strip and bar-by-bar replay (informational, places no orders)",
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
    # MO4 slice 2 (ATP_SESSION_GROWTH_MASTER.md PART 2 items 4 and 6; needs `ui_v2`): the Sector & index view at /markets
    # (every index and sector of the system watchlists with its day / week / month move, breadth, strongest and weakest
    # stocks and the move against the NIFTY 50, from the shared end-of-day history; app/markets) and the Option chain
    # page's "What the chain shows" summary (app/option_chain/dish.py). OFF: /api/markets/* answers 404, the page is not
    # in the navigation and the option chain shows no summary. Information only, places no orders.
    "markets_view": "Sector & index view (index and sector moves, breadth, strongest and weakest stocks) and the option chain's plain-words summary (information only)",
    # U7 LG1 (docs/specs/ATP_LEGENDS_SCREENS_ADDENDUM.md): the Legends cards - publicly described methods with a screen
    # modelled on each. OFF: /api/screener/legends answers 503 and the Legends screens are hidden from the taxonomy.
    # Educational only - never a recommendation, never an endorsement.
    "screener_legends": "Legends: cards on publicly described trading methods, each with an editable screen (educational only)",
    # U5 D3a: the result board's heatmap (by sector, sized by market cap) and chart-grid views, with POST
    # /api/screener/board/context (company reference data + shared daily closes). OFF: the table view only (404).
    "screener_board_views": "Screener result views: heatmap by sector and a chart grid of the matched symbols (research only, places no orders)",
    # N1 X2: Auto PAPER from scans. OFF (the default, and while the owner has not decided N1-3): an AUTO_PAPER template
    # stays a proposal. ON: a template its owner armed places one PAPER order per confirmed setup (approval-only mode
    # off, within the scan limits, re-quoted) - PAPER only, never a broker order. Only the platform operator sets it.
    "scan_auto_paper": "Auto PAPER from armed scan templates (N1 X2): a confirmed setup is placed as a PAPER order without a proposal (approval-only mode off; PAPER only, never a broker order)",
    # U7 LG2: the second slice of Legends cards (trend, pullback and intraday methods; data/legends.json `slice: lg2`), on
    # top of screener_legends. OFF: the LG2 cards are left out of /api/screener/legends and their screens out of the
    # taxonomy. Educational only - never a recommendation, never an endorsement; places no orders.
    "screener_legends_lg2": "Legends LG2: more cards on publicly described trading methods, each with an editable screen (educational only, places no orders)",
    # U7 LG3: the point-in-time fundamentals functions in ScreenQL (EPSGrowthYoY, SalesGrowthYoY, EarningsYield,
    # ReturnOnCapital, GrahamNumber, NCAVPerShare) - the reported figures as known by each bar's close, never a later
    # restatement. OFF: the registry hides them and a screen using one is refused with the reason.
    "screener_fundamentals": "Fundamentals in screens: point-in-time reported figures (growth, earnings yield, return on capital, Graham number, NCAV) - research only, places no orders",
    # U7 LG3: the third slice of Legends cards (Graham, Greenblatt, O'Neil; `slice: lg3`), on top of screener_legends.
    "screener_legends_lg3": "Legends LG3: value and growth cards on publicly described methods (needs screener_fundamentals to run; educational only, places no orders)",
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
    # N3 S6b: the rolling screen backtest - rebalance weekly / 4-weekly / monthly, top N by a rank expression, equal or rank
    # weights, next-open fills with the part C slippage and India's delivery charges, against the equal-weight universe and
    # NIFTY 50; deflated through the research trial ledger. Needs screener_history. OFF: 503. A simulation only.
    # N3 S7a: ranking systems - weighted factor trees (weights, higher / lower is better, sector-neutral percentiles) that
    # rank a universe 0-100 with every component shown; a column, a sort key (the S6b backtest's rank_system) and a filter
    # (ScreenQL RankScore("name") > 80). Platform systems with documented formulas. OFF: the routes answer 503 and a screen
    # using RankScore is refused with the reason. Information only - places nothing.
    # N3 S8a: ownership and events in ScreenQL (category E) - Holding / HoldingChange from the shareholding patterns and
    # DaysToEvent from the results / board-meeting / AGM / buyback / dividend calendar and the lake's ex-dates, each as
    # known by the bar's close (point in time). OFF: a screen using them is refused with the reason. Information only.
    # N3 S8c: the per-venue field-availability matrix - a screen run on a venue whose data lacks a field (oi) or a data
    # family (reference lists, ownership, option chains) is refused with where it is available; GET /api/screener/venues
    # gives the matrix. OFF: runs behave as before and the route answers 503.
    # N3 S9: the screener in the Copilot - the agent's build_screen / run_screen (as_of) / explain_results tools and the
    # propose_alert proposal (an alert rule created only when the person who asked approves it), and the plain-language
    # box (POST /api/screener/copilot/nl: a ScreenQL draft the trader checks and edits; needs ai_copilot). OFF: the tools
    # are not offered and the route answers 503. Information only - nothing places an order.
    "screener_copilot": "Screener in the Copilot: build, run, explain screens and propose alerts from the agent, plus the plain-language screen box",
    "screener_venues": "Screener venue matrix: refuse fields and functions a venue's data does not carry, and list what each venue supports",
    "screener_ownership": "Ownership and event screens: promoter / pledge / FII / DII stakes and their changes, days to results, board meetings and ex-dates (point in time; information only)",
    # Wave templates W1 (docs/design/WAVE_TEMPLATES_REVIEW.md WB13, owner question WAVE-16): the causal wave count in
    # ScreenQL - WaveState / WaveType / WaveAlt / WaveClarity from confirmed swings (degrees 1-3), recomputed only on a
    # newly confirmed swing. OFF: the registry hides them and a screen, study or alert rule using one is refused with the
    # reason; wave alerts fire on bar close only. Information only - places no orders.
    "screener_waves": "Wave count in screens: the running wave, its type, the alternate count and the clarity, the wave levels, ratios and "
                      "anchored wave lines and their retest, from confirmed swings (information only)",
    # Strategy templates step 2 (docs/design/STRATEGY_TEMPLATES_IMPL.md): the built-in signal recipes (D1, D2, B1, B3) as
    # PAPER strategies - the template list, "Adopt as a paper strategy" (a pre-fill of the paper bot form; the user reviews
    # and saves, nothing starts by itself) and template ids in the deployment / backtest routes and the worker. OFF: the
    # routes answer 404 and a template id resolves to nothing. Signals only; LIVE is refused for a template.
    "strategy_templates": "Strategy templates: ready signal recipes (Supertrend flip, moving-average cross, range and channel breakouts) adopted as paper strategies with the user's own settings",
    "screener_rankings": "Ranking systems: weighted multi-factor trees ranking a universe 0-100 with explainable components (information only)",
    "screener_backtest": "Rolling screen backtest: top N matches by a rank, rebalanced, with costs and slippage, against benchmarks, deflated (a simulation; places nothing)",
    "screener_history": "Screen history: who passed on a past date (point in time) and screen fitness - forward returns of matches vs the universe, deflated (information only)",
    # N1 F1: the user's own PAPER book as one risk view - exposure, open risk at the stops by sector bucket (all indices
    # in one), beta to NIFTY 50 from the lake's Daily closes, and the one-sided direction warning. OFF:
    # GET /api/portfolio/risk answers 503. Read-only: it refuses nothing and places no orders.
    "portfolio_risk": "Portfolio risk view of the user's own PAPER book: exposure, open risk by sector bucket, beta to NIFTY 50, one-sided warning (read-only)",
    # N1 F2: the user's own portfolio caps (max exposure in one sector bucket, max open risk at the stops in one
    # bucket, max open positions; each 0 = no limit, set in the PAPER risk settings) refuse new PAPER entries before a
    # proposal and at Approve. Acts only while portfolio_risk is on too. Exits are never refused (ADR-0004).
    "portfolio_risk_caps": "Portfolio caps on new PAPER entries: sector exposure, open risk per bucket, open positions (entries only; exits never refused)",
    # Analyze a chart (docs/design/CHART_ANALYZE.md, PR-A): a user uploads a chart image or pastes a chart link and gets
    # an educational reading of it (the situation and the possibilities, never advice), from the analysis engine in the
    # private package. Beta tenants only (the allow-list). OFF: every /api/chart-analyze route answers 503 and the page
    # and the Charts-page button are hidden. Places no orders.
    "chart_analyze": "Analyze a chart: upload a chart image or paste a chart link for an educational reading (beta; places no orders)",
    # Analyze a chart PR-A3 (the result screen's "Option strikes · from your settings"): what the organisation's OWN option
    # deployment settings on the chart's symbol work out to at the setup's trigger level; the analysis never picks a strike.
    # Acts only while chart_analyze is on too; owner's tenant first. OFF: GET /api/chart-analyze/items/{id}/strikes answers
    # 503 and the block is hidden. Reads only - places no orders.
    "chart_analyze_strikes": "Analyze a chart: option strikes worked out from your own deployment settings at the setup's trigger level (reads only, places no orders)",
    # Analyze a chart PR-A4 (PR-D's share link, CA-5): a private link to the owner's own analysis that only logged-in
    # members of the same organisation can open, expiring (admin setting share_expiry_days) and revocable. The shared
    # copy carries NO security name, NO price, NO date and NOT the chart picture (CLAUDE.md, SEBI): the structure only.
    # Acts only while chart_analyze is on too. OFF: the share routes answer 503 and the Share button is hidden.
    "chart_analyze_share": "Analyze a chart: share a structure-only copy of your analysis with your organisation (no names, no prices; places no orders)",
    # N4 CH6 (docs/design/CHARTING.md): ScreenQL expressions drawn as chart studies, computed by the screener's own runtime
    # from the shared lake bars as of a time (lines for numbers, marks for conditions). OFF: GET /api/charts/studies
    # answers 503 and the chart shows no study panel. Reference only - places no orders.
    "chart_studies": "Chart studies: ScreenQL expressions drawn on the chart by the screener's own engine, as of any bar (reference only, places no orders)",
    "chart_price_action": "Price-action chart layer: swings, legs, breaks, swing levels and trend per degree, as of any bar, with replay (reference only, places no orders)",
    # H-C4 (Copilot spec C4): "analyze <symbol>" - technical, options, news and macro views from the read tools and the
    # rules' summary (readings that lean up / down, risks, possibilities). Rules-only wording: no model is called and no
    # AI text is shown (docs/OPEN_QUESTIONS.md HC4-5); no order, no proposal (ADR-0006). OFF: POST /api/ai/analyze is 503.
    "ai_multiview": "Copilot multi-view analysis: technical, options, news and macro views of a symbol with a rules-generated summary (no advice, no orders)",
    # N4 CH4a: the trades layer - the caller's own PAPER trades (entries, exits, open stop / target) on the chart of
    # their instrument, as of a time (replay never shows a trade or an exit before it happened). OFF: GET
    # /api/charts/layers/trades answers 503 and the chart shows no trades. Read-only - places no orders.
    "chart_trades_layer": "Trades chart layer: your own PAPER trades on the chart, as of any bar (read-only, places no orders)",
    # N4 CH4b: option / future contract charts and synthetic spreads - daily bars and OI from the shared lake's F&O
    # bhavcopy rows, contract search from the instrument master, chain -> chart links, a COMPUTED daily IV (labelled).
    # OFF: /api/charts/contracts/* answer 503 and the option chain shows no chart links. Reference only - places no orders.
    "chart_contracts": "Contract charts: daily option / future and spread charts with OI from the shared history (reference only, places no orders)",
    # N4 CH4c: on top of chart_contracts - an underlying's futures basis, annualised carry, calendar spread and rollover %
    # per trade date from the shared daily history (shared-licensed sources only). OFF: the route answers 503 and the
    # contract chart shows no link to it. Reference only, places no orders.
    "chart_futures_basis": "Futures basis charts: basis, cost of carry, calendar spread and rollover from the shared history (reference only, places no orders)",
    # U9 TX2 (docs/specs/ATP_SCREENER_TAXONOMY_ADDENDUM.md §4): the screen library's confluence view (symbols ranked by
    # how many methods flag them), generated short mirrors of the long pre-built screens and watch-then-trigger lists,
    # on top of `screener_v2` and `screener_gallery`. OFF: /api/screener/library/* answer 503 and the Screener page shows
    # none of it. Research only - closed bars, no recommendation, places no orders.
    "screener_tx2": "Screen library tools: confluence view, short mirror screens and watch-then-trigger lists on the Screener page (research only)",
    # UI2-0 (UI v2 proposal §12, owner-approved): the redesigned navigation and pages, switched on page by page as each
    # UI2-n PR lands. OFF: every page renders exactly what it renders today. Layout only - places no orders and changes
    # no trading behaviour; the frontend reads it once through the shared useFeatures() hook.
    "ui_v2": "UI v2: the redesigned navigation and pages (layout only; places no orders)",
    # AMW-2 (docs/design/AMW_PORT.md §4.1): personal named watchlists - saved symbol lists the Screener ("My watchlists")
    # and the Scanner ("Load watchlist") load as their universe. OFF: /api/watchlists/* answers 404 and neither page
    # shows them. A list of names only: nothing scans, alerts or places an order.
    "watchlists": "Watchlists: your own named symbol lists, loadable as the Screener and Scanner universe",
    # N2-3 (P1 spec 1.5, docs/design/N2_P1FGH.md): decision tools - the risk-of-ruin / Kelly calculator on typed inputs
    # (prefilled from the user's own figures) and the what-if for the user's own open positions (P&L now, on a later
    # day with an IV shift, and at expiry, at the spot and IVs typed in). OFF: /api/decision/* answers 404. Information
    # only: nothing sizes, places or closes an order.
    # N2-4: also the exposure view (own positions by underlying and sector, beta, correlated pairs, from the daily lake)
    # and the expected-move cone on the chart.
    "decision_tools": "Decision tools: risk-of-ruin and Kelly calculator, what-if for your own open positions, exposure and correlation view, expected-move cone on the chart (information only)",
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
    # AMW-1 (docs/design/AMW_PORT.md §4.1): the plan limits of the AMW port's features (watchlists, symbols per screener
    # run, reports a day, report retention and storage, report schedules and their shortest interval, Strategy Lab
    # strategies and CPU seconds). OFF: none of them is enforced and Billing shows none of them; the B2 limits are unchanged.
    "plan_limits_v2": "Plan limits v2: watchlists, symbols per screener run, reports, report schedules and Strategy Lab limits per plan",
    # New flags: add ONE entry, in its alphabetical position (not at the end), so parallel branches do not conflict on
    # the same line. A registered flag is OFF by default automatically (DEFAULT_OFF_FLAGS below is computed).
}
# Flags that start ON (kill-flag semantics: a feature is on until the operator turns it off). This list is CLOSED: it
# holds exactly the flags that were ON by default before DEFAULT_OFF_FLAGS was computed. Do not add to it - never turn a
# flag on; tests/test_flags_default_off_computed.py pins both sets.
DEFAULT_ON_FLAGS: frozenset[str] = frozenset({
    "ai_copilot",          # Phase N kill switch for AI strategy drafts; the AI never places orders (ADR-0006)
    "backtest_optimizer",  # Phase N kill switch for optimisation grid runs; a simulation, places no orders
    "live_trading",        # Phase N kill switch for new LIVE deployments only; LIVE itself is gated by the trading:live
                           # scope, the plan, step-up and the phase0_paper_only lock (Phase 0 mode seeds this OFF)
    "marketplace",         # Phase N kill switch for marketplace listings and subscriptions
    "options_builder",     # P1-c1 research-only payoff tool, places no orders; saving is options_builder_saved (OFF)
    "public_api",          # Phase N kill switch for public API keys and /api/public/v1
    "self_signup",         # Phase N kill switch for public registration (OFF = invite-only); beta_cohort_gate is separate
})
# Phase BB/BE: every other registered flag starts OFF until the operator turns it on. Computed, so a new flag is OFF by
# default as soon as it is registered in FEATURE_FLAGS - there is no second list to edit.
DEFAULT_OFF_FLAGS: frozenset[str] = frozenset(FEATURE_FLAGS) - DEFAULT_ON_FLAGS


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


async def flag_listing(session: AsyncSession) -> Dict[str, Dict]:
    """UX1: the admin Feature flags list - `feature_flags` plus each flag's catalog entry (app/platform/flag_catalog.py)
    and its default (`default_on`: the value with nothing stored, the Phase 0 seed included)."""
    from app.platform.flag_catalog import info_for

    flags = await feature_flags(session)
    for name, entry in flags.items():
        phase_default = deployment_phase.default_for(name)
        entry.update(info_for(name), default_on=(name not in DEFAULT_OFF_FLAGS) if phase_default is None else phase_default)
    return flags


def _entry_enabled(entry: Optional[Dict], tenant_id: Optional[int]) -> bool:
    """One flag's answer from its resolved entry: unknown flags are ON; otherwise ON, or the tenant is allow-listed."""
    if entry is None:
        return True
    if entry["on"]:
        return True
    return tenant_id is not None and tenant_id in entry["tenants"]


async def flag_enabled(session: AsyncSession, name: str, tenant_id: Optional[int] = None) -> bool:
    flags = await feature_flags(session)
    return _entry_enabled(flags.get(name), tenant_id)


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
    # UI2-0 review M3: the stored flags are read and resolved once, then every flag is answered from that one result
    # (same answer as flag_enabled per flag, which re-read and re-resolved all flags for each of them: N x N work on
    # every GET /api/system/features, which the UI v2 shell re-reads as the trader moves between pages).
    flags = await feature_flags(session)
    return {name: _entry_enabled(flags.get(name), tenant_id) for name in FEATURE_FLAGS}


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
