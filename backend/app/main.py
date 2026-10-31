import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.accounts.routes import router as accounts_router
from app.admin.bootstrap import promote_configured_super_admins
from app.admin.console import router as admin_console_router
from app.admin.routes import router as admin_router
from app.admin.view_as_routes import admin_router as view_as_admin_router
from app.admin.view_as_routes import router as view_as_router
from app.ai.byok_routes import router as ai_byok_router
from app.ai.chat_routes import router as ai_chat_router
from app.ai.connect_routes import router as ai_connect_router
from app.ai.routes import router as ai_router
from app.alerts.links_routes import router as alert_links_router
from app.alerts.outbox_admin import router as alert_outbox_admin_router
from app.alerts.platform_bot_routes import router as telegram_connect_router
from app.alerts.routes import router as alerts_router
from app.alerts.rules_routes import router as alert_rules_router
from app.alerts.webhook_routes import router as alert_webhooks_router
from app.auth.google_routes import router as google_auth_router
from app.auth.routes import router as auth_router
from app.auth.view_as_middleware import ViewSessionReadOnly
from app.backtest.console_routes import router as backtest_console_router
from app.backtest.routes import router as backtest_router
from app.beta.routes import admin_router as beta_admin_router
from app.beta.routes import feedback_router, pwa_router
from app.billing.routes import admin_router as billing_admin_router
from app.billing.routes import router as billing_router
from app.billing.routes import webhook_router as billing_webhook_router
from app.brokers.available_routes import router as broker_available_router
from app.brokers.connect import router as broker_connect_router
from app.brokers.routes import router as broker_router
from app.chart_analyze.routes import admin_router as chart_analyze_admin_router
from app.chart_analyze.routes import router as chart_analyze_router
from app.charts.contracts import router as chart_contracts_router
from app.charts.drawings import router as chart_drawings_router
from app.charts.futures_basis import router as chart_futures_basis_router
from app.charts.layouts import router as chart_layouts_router
from app.charts.price_action_layer import router as chart_price_action_layer_router
from app.charts.read_chart import router as chart_read_router
from app.charts.studies import router as chart_studies_router
from app.charts.trades_layer import router as chart_trades_layer_router
from app.charts.zones_layer import router as chart_zones_layer_router
from app.compliance.routes import router as compliance_router
from app.context_panel.routes import router as context_panel_router
from app.contract_notes.routes import router as contract_notes_router
from app.core.config import (
    ALLOWED_ORIGINS,
    APP_VERSION,
    ENVIRONMENT,
    MAX_REQUEST_BODY_BYTES,
    api_docs_enabled,
    tables_created_at_startup,
    validate_production_config,
)
from app.core.logging_config import configure_logging
from app.core.route_cache import install as install_route_cache
from app.core.validation_errors import request_validation_handler
from app.custom_strategies.routes import router as custom_strategies_router
from app.db.session import _session_factory as _startup_session_factory
from app.db.session import init_models
from app.decision_tools.routes import router as decision_tools_router
from app.deployments.routes import router as deployments_router
from app.execution.incomplete_routes import router as incomplete_orders_router
from app.expiry_checklist.routes import router as expiry_checklist_router
from app.exports.routes import router as exports_router
from app.fundamentals.routes import router as fundamentals_router
from app.fx.routes import admin_router as fx_admin_router
from app.fx.routes import router as fx_router
from app.growth.routes import admin_router as growth_admin_router
from app.growth.routes import router as growth_router
from app.idea_library.routes import router as idea_library_router
from app.incidents.routes import router as incidents_router
from app.instruments.reference_routes import router as instrument_reference_router
from app.instruments.routes import router as instrument_master_router
from app.journal.routes import router as journal_router
from app.journal.stats_routes import router as journal_stats_router
from app.kill_switch.routes import router as kill_switch_router
from app.lab.pine.routes import router as lab_pine_router
from app.learn.routes import router as learn_router
from app.legal.routes import router as legal_router
from app.live.routes import router as live_router
from app.market_data.candles_routes import router as market_candles_router
from app.market_data.probe import admin_router as market_probe_admin_router
from app.market_data.quotes_routes import router as market_quotes_router
from app.market_data.routes import router as market_holidays_router
from app.market_data.strip_routes import router as market_strip_router
from app.market_lake.chain_history_routes import admin_router as chain_history_admin_router
from app.market_lake.chain_history_routes import router as chain_history_router
from app.market_lake.import_routes import backfill_router as history_backfill_admin_router
from app.market_lake.import_routes import intraday_router as history_intraday_admin_router
from app.market_lake.import_routes import router as history_import_admin_router
from app.market_lake.routes import router as market_lake_router
from app.marketplace.routes import admin_router as marketplace_admin_router
from app.marketplace.routes import router as marketplace_router
from app.markets.routes import router as markets_router
from app.me.account import router as me_account_router
from app.me.notices import router as me_notices_router
from app.me.prefs import router as me_prefs_router
from app.me.setup import router as me_setup_router
from app.mobile.devices import router as mobile_devices_router
from app.news_events.routes import router as news_events_router
from app.news_feed.routes import router as news_feed_router
from app.notifications.routes import router as notifications_router
from app.observability.middleware import ObservabilityMiddleware
from app.observability.ops_routes import router as ops_alerts_router
from app.observability.routes import router as observability_router
from app.observability.status_history import router as status_sessions_router
from app.observability.status_page import router as public_status_router
from app.option_chain.analyses_routes import router as oc_analyses_router
from app.option_chain.analyze_routes import router as option_chain_analyze_router
from app.option_chain.ask_routes import router as oc_ask_router
from app.option_chain.charts_routes import router as oc_charts_router
from app.option_chain.futures_routes import router as fno_hub_router
from app.option_chain.probe import admin_router as oc_probe_admin_router
from app.option_chain.routes import router as oi_banner_router
from app.options_builder.routes import router as options_builder_router
from app.options_builder.saved import router as options_builder_saved_router
from app.paper.fno_insights_routes import router as paper_fno_insights_router
from app.paper.fno_journal_routes import router as paper_fno_journal_router
from app.paper.fno_routes import router as paper_fno_router
from app.paper.report_card_routes import router as paper_report_card_router
from app.paper.routes import router as paper_router
from app.paper.ticket_routes import router as paper_ticket_router
from app.performance.routes import router as performance_router
from app.platform.deployment_phase import on_startup as deployment_phase_startup
from app.platform.health_routes import router as health_router
from app.platform.readiness_routes import router as readiness_router
from app.platform.routes import admin_router as controls_admin_router
from app.platform.routes import status_router as system_status_router
from app.platform.routes import users_router as trading_disable_router
from app.portfolio.routes import router as portfolio_router
from app.price_action.routes import router as price_action_router
from app.public_api.routes import keys_router as api_keys_router
from app.public_api.routes import public_router as public_api_router
from app.quant.routes import router as quant_router
from app.reconciliation.routes import router as reconciliation_router
from app.rhythm.routes import router as rhythm_router
from app.risk_engine.guardian_routes import router as risk_guardian_router
from app.risk_engine.hierarchy_routes import router as risk_hierarchy_router
from app.risk_engine.routes import router as risk_settings_router
from app.screener.board import router as screener_board_router
from app.screener.breakouts.routes import router as screener_breakouts_router
from app.screener.etf_routes import router as screener_etf_router
from app.screener.evidence_routes import router as screener_evidence_router
from app.screener.history_routes import router as screener_history_router
from app.screener.library_routes import router as screener_library_router
from app.screener.ranking_routes import router as screener_rankings_router
from app.screener.routes import router as screener_router
from app.screener.scanner.routes import router as scanner_ai_router
from app.screener.scanner.run_routes import router as scanner_run_router
from app.screener.then.routes import router as scan_then_router
from app.screener.universes import router as screener_universes_router
from app.screener.version_routes import router as screener_version_router
from app.secrets_store.envelope import warm_all as warm_tenant_keys
from app.setup_recall.routes import router as setup_recall_router
from app.signals_feed.day_one_routes import router as signals_day_one_router
from app.signals_feed.routes import router as signals_feed_router
from app.strategy_engine.chart_routes import router as strategy_chart_router
from app.strategy_engine.console_routes import router as strategy_console_router
from app.strategy_templates.routes import router as strategy_templates_router
from app.support.desk_routes import admin_router as support_desk_admin_router
from app.support.desk_routes import router as support_desk_router
from app.support.routes import router as support_router
from app.support_resistance.routes import router as support_resistance_router
from app.tax.routes import router as tax_router
from app.team.routes import router as team_router
from app.telegram_inbound.routes import router as telegram_inbound_router
from app.terminal.routes import router as terminal_router
from app.today.routes import router as today_router
from app.trading.cost_meter import router as cost_meter_router
from app.trading.routes import router as trading_router
from app.watchlists.routes import router as watchlists_router
from app.watchlists.system_routes import admin_router as system_watchlists_admin_router
from app.watchlists.system_routes import router as system_watchlists_router
from app.webhooks.routes import router as webhooks_router
from app.workers.routes import router as workers_router


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    validate_production_config()
    from app.observability import error_tracker
    error_tracker.init("api")      # A2: off unless ERROR_TRACKER_DSN is set; events are scrubbed of personal data
    if tables_created_at_startup(ENVIRONMENT):
        await init_models()         # dev/test only; production and staging are Alembic's alone (P0.1 / S12)
    async with _startup_session_factory() as session:
        await promote_configured_super_admins(session)
        await warm_tenant_keys(session)  # Phase N1: tenant data keys into the process key ring
        # Phase 0 deployment (ATP_DEPLOYMENT_PHASE=phase0): seed live_trading OFF / phase0_paper_only ON where unset, then
        # log why LIVE entry paths are (not) started. Never stops the API from coming up.
        try:
            await deployment_phase_startup(session, "api")
        except Exception:  # noqa: BLE001 - never stops the API from coming up (Phase 0 defaults still apply)
            logging.getLogger(__name__).exception("Phase 0 start-up check failed")
    yield


app = FastAPI(
    title="AMW Algorithmic Trading Platform API",
    description="Strategies, backtesting, autonomous PAPER/LIVE deployments, risk, brokers, AI copilot - the platform's own API. "
                "Educational tooling; nothing here is investment advice.",
    version=APP_VERSION,
    lifespan=_lifespan,
    # AUD-G1.3: no interactive docs or schema outside development.
    docs_url="/docs" if api_docs_enabled() else None,
    redoc_url="/redoc" if api_docs_enabled() else None,
    openapi_url="/openapi.json" if api_docs_enabled() else None,
)
# 422s keep FastAPI's shape but never echo the rejected input (it may be a credential).
app.add_exception_handler(RequestValidationError, request_validation_handler)

# The Vite dev server proxies /api to this service in development, but CORS is still enabled
# for direct access (a separately-hosted frontend build, API docs "try it out", etc). Defaults to
# any origin for zero-config local dev; set ALLOWED_ORIGINS (comma-separated) to your real
# frontend domain(s) in production - validate_production_config() refuses to boot with the "*"
# default when ENVIRONMENT=production.
_BODY_TOO_LARGE = f"Request body larger than {MAX_REQUEST_BODY_BYTES} bytes"


class _BodyTooLarge(HTTPException):
    """Raised from inside `receive` once a streamed body passes the limit. An HTTPException, so FastAPI's body parsing
    passes it on as it is (any other error there becomes a 400) and the app's handler answers 413."""

    def __init__(self) -> None:
        super().__init__(status_code=413, detail=_BODY_TOO_LARGE)


class _LimitRequestBody:
    """P0.1 / S3: refuse oversized bodies before anything parses them. A declared Content-Length over the limit is
    refused at once (the fast path); M55 O1: a body without one (chunked) is counted as it is received, and the read
    that passes the limit ends the request with 413 - the edge's own cap (deploy/Caddyfile `request_body max_size`)
    is no longer the only one.
    Registered before CORS/observability so those wrap it: the 413 carries CORS headers and a request id.
    LD-7: a pure ASGI middleware - the `@app.middleware("http")` form ran every request through Starlette's
    BaseHTTPMiddleware (an extra task and a memory stream per request) for one header check."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers") or ():
            if name == b"content-length":
                declared = value.decode("latin-1")
                if declared.isdigit() and int(declared) > MAX_REQUEST_BODY_BYTES:
                    await JSONResponse(status_code=413, content={"detail": _BODY_TOO_LARGE})(scope, receive, send)
                    return
                break

        received = 0
        started = False

        async def counted_receive() -> Any:
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body") or b"")
                if received > MAX_REQUEST_BODY_BYTES:
                    raise _BodyTooLarge()
            return message

        async def tracked_send(message: Any) -> None:
            nonlocal started
            if message.get("type") == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counted_receive, tracked_send)
        except _BodyTooLarge:
            # Read outside FastAPI's body parsing (a route reading the stream itself): answer here unless the
            # response has already begun.
            if started:
                raise
            await JSONResponse(status_code=413, content={"detail": _BODY_TOO_LARGE})(scope, receive, send)


app.add_middleware(_LimitRequestBody)
# MO3-A1: a read-only "View as user" token can never write - refused here, inside the /api/v1 rewrite (it sees the
# routers' paths) and before any route (app/auth/view_as_middleware.py).
app.add_middleware(ViewSessionReadOnly)


# A4: the API's own security headers, so they hold even where no Caddy stands in front (the IP edge, a dev host, the CI
# stack). Caddy's edge values replace these in production (deploy/Caddyfile); a header a route set itself is kept.
API_SECURITY_HEADERS = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    # JSON needs nothing; the one HTML page (the e-mail unsubscribe form) needs its inline style and its own form target.
    (b"content-security-policy", b"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; "
                                 b"base-uri 'none'"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=(), usb=()"),
)
HSTS_HEADER = (b"strict-transport-security", b"max-age=31536000; includeSubDomains")


class _SecurityHeaders:
    """Pure ASGI (LD-7): adds the headers to every `/api` response; HSTS only over HTTPS (the proxy's X-Forwarded-Proto,
    trusted only from FORWARDED_ALLOW_IPS)."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or not str(scope.get("path", "")).startswith("/api"):
            await self.app(scope, receive, send)
            return
        extra = API_SECURITY_HEADERS + ((HSTS_HEADER,) if scope.get("scheme") == "https" else ())

        async def _send(message: Any) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                present = {name.lower() for name, _ in headers}
                headers.extend((name, value) for name, value in extra if name not in present)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, _send)


app.add_middleware(_SecurityHeaders)


# Outermost: request id, /api/v1 alias, HTTP metrics (Phase E1/E2). Added before CORS so CORS
# wraps it and its headers are still applied to the rewritten request.
app.add_middleware(ObservabilityMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    # OC1: a cross-origin page (the Android app) may read these response headers - the request id support asks for, and
    # the data tier of a refused market-data read ("C": not live on this plan).
    # A4: X-Step-Up too, so the app can ask for the authenticator code when an admin change needs a fresh check.
    expose_headers=["X-Request-ID", "X-Data-Tier", "X-Step-Up"],
)


app.include_router(auth_router)
app.include_router(google_auth_router)   # GS-1: Google sign-in (404 until both credentials are set)
app.include_router(broker_router)
app.include_router(broker_connect_router)   # P2a: simple broker connect (catalog, connections, connect, renew)
app.include_router(strategy_chart_router)  # Phase AO: strategies drawn on the chart
app.include_router(trading_router)
app.include_router(performance_router)   # REV-1 M2: the Performance page - the user's own trades only
app.include_router(journal_router)  # Z6 / P1-e: journal, behaviour tags, discipline score (flag discipline_engine, off)
app.include_router(journal_stats_router)  # M62 JN-3: the journal list, its statistics and the CSV / PDF export (same flag)
app.include_router(custom_strategies_router)
app.include_router(options_builder_router)  # P1-c: Options Strategy Builder (research only, no orders)
app.include_router(options_builder_saved_router)  # Z5: saved builder strategies (flag options_builder_saved, off by default)
app.include_router(risk_settings_router)
app.include_router(risk_hierarchy_router)
app.include_router(risk_guardian_router)
app.include_router(fundamentals_router)
app.include_router(kill_switch_router)
app.include_router(reconciliation_router)
app.include_router(accounts_router)
app.include_router(backtest_router)
app.include_router(notifications_router)
app.include_router(webhooks_router)
app.include_router(news_events_router)
app.include_router(news_feed_router)  # Phase BB: live news feed
app.include_router(telegram_inbound_router)  # Phase BE: Telegram inbound
app.include_router(workers_router)
app.include_router(deployments_router)
app.include_router(alerts_router)
app.include_router(alert_webhooks_router)  # AUD-G2.5: each member's own signed webhooks (flags ui_v2 + alert_webhooks)
app.include_router(mobile_devices_router)  # B8: the caller's own phones for app notifications (flag mobile_push)
app.include_router(team_router)
app.include_router(admin_router)
app.include_router(admin_console_router)  # MO3 (a): the admin console's read-only sections (SUPER_ADMIN + two-factor)
app.include_router(view_as_admin_router)  # MO3-A1: the admin's read-only "View as user" (SUPER_ADMIN + two-factor)
app.include_router(view_as_router)        # MO3-A1: the view's banner / End, and the person's own record of views
app.include_router(ops_alerts_router)  # A2: Admin > System > Alerts - the server checks and the operator channels
app.include_router(exports_router)
app.include_router(contract_notes_router)
app.include_router(observability_router)
app.include_router(public_status_router)  # Z10-e (G7): public status page data (flag public_status_page, off)
app.include_router(status_sessions_router)  # AUD-T8: uptime per market session, SLO targets, in-app notice (flag status_page, off)
app.include_router(instrument_master_router)
app.include_router(market_holidays_router)
app.include_router(billing_router)
app.include_router(billing_admin_router)
app.include_router(billing_webhook_router)
app.include_router(marketplace_router)
app.include_router(marketplace_admin_router)
app.include_router(api_keys_router)
app.include_router(public_api_router)
app.include_router(ai_byok_router)       # BYOK-V1: the user's own AI key for Vision (flag vision_byok, off by default)
app.include_router(ai_connect_router)    # Settings redesign A: "Connect AI in one paste" (flag ai_one_key, off by default)
app.include_router(ai_router)
app.include_router(ai_chat_router)   # handover item 5: the chat-first Copilot (flag ui_v2, off)
app.include_router(scanner_ai_router)   # Phase Y: AI scanner
app.include_router(quant_router)        # Phase Z: factor and risk models
app.include_router(market_candles_router)  # Phase AA: broker candles for the research pages
app.include_router(market_probe_admin_router)  # C1: the owner's read-only broker probe (SUPER_ADMIN + two-factor)
app.include_router(market_quotes_router)  # UI2-4a2-pre: batch watchlist quotes (one multi-quote broker call)
app.include_router(market_strip_router)  # UI2-2: the v2 dashboard's market strip (read-only, one call)
app.include_router(market_lake_router)  # Part B5: point-in-time history from the market data lake
app.include_router(history_import_admin_router)  # HB0: R2 history import progress (SUPER_ADMIN, read-only)
app.include_router(history_backfill_admin_router)  # HB1: bhavcopy backfill progress + manual uploads (SUPER_ADMIN, flag)
app.include_router(history_intraday_admin_router)  # HB2: owner intraday history progress (SUPER_ADMIN, read-only)
app.include_router(chain_history_admin_router)  # TM 9.4: owner chain history status (SUPER_ADMIN, read-only)
app.include_router(chain_history_router)  # TM 9.4: the organisation's own chain history (flag chain_history)
app.include_router(oi_banner_router)       # OI Banner O2: snapshot history and settings
app.include_router(fno_hub_router)         # FNO-3: the symbol hub's F&O facts and the futures chain (flag ui_v2)
app.include_router(oc_analyses_router)     # OC2: the built-in option chain analyses, read-only (flag ui_v2, off)
app.include_router(oc_charts_router)       # OC5: the Option chain "Charts" series, read-only (flag ui_v2)
app.include_router(readiness_router)       # Phase AB: go-live checklist
app.include_router(system_status_router)
app.include_router(controls_admin_router)
app.include_router(trading_disable_router)
app.include_router(portfolio_router)
app.include_router(tax_router)
app.include_router(fx_router)
app.include_router(fx_admin_router)
app.include_router(incidents_router)
app.include_router(screener_router)
app.include_router(lab_pine_router)  # handover 11c-1: Pine Script subset -> ScreenQL, never executed (flag strategy_lab_pine, off)
app.include_router(strategy_templates_router)  # strategy templates step 2: list + adopt as a paper strategy (flag strategy_templates, off)
app.include_router(idea_library_router)  # idea library slice 1: method cards T1-T8, education only (flag idea_library, off)
app.include_router(screener_history_router)  # N3 S6a: who passed on a date, screen fitness (flag screener_history, off)
app.include_router(screener_rankings_router)  # N3 S7a: ranking systems (flag screener_rankings, off)
app.include_router(screener_breakouts_router)
app.include_router(screener_evidence_router)  # U8 EV1/EV2: evidence charts (flag evidence_charts, off)
app.include_router(screener_board_router)  # U5 D3a: result-board heatmap / chart-grid context (flag screener_board_views, off)
app.include_router(screener_library_router)  # U9 TX2: confluence, mirrors, watch-then-trigger lists (flag screener_tx2, off)
app.include_router(screener_version_router)  # LAB-18 (AMW-7): a saved screen's versions (flag screener_copilot, off)
app.include_router(screener_etf_router)  # S10 J: the ETF screen on the shared EOD history (flag screener_etf, off)
app.include_router(screener_universes_router)  # owner 23 Oct: the lists a scan runs on and the batch plan (flag screener_v2)
app.include_router(alert_rules_router)
app.include_router(scan_then_router)
app.include_router(alert_links_router)
app.include_router(telegram_connect_router)  # UX2 fix 5: Connect Telegram with the platform bot
app.include_router(alert_outbox_admin_router)  # Z10-b: alert outbox state, dead letters and replay (SUPER_ADMIN)
app.include_router(compliance_router)
app.include_router(chart_drawings_router)
app.include_router(chart_layouts_router)  # CH5: saved multi-chart workspace layouts per user (flag chart_workspace)
app.include_router(chart_zones_layer_router)  # Z7a-3: market-zones chart layer (flag market_zones, off by default)
app.include_router(chart_price_action_layer_router)  # N4 CH3: price-action chart layer as of a time (flag chart_price_action, off)
app.include_router(chart_read_router)               # AUD-T5: Read this chart as of the last closed bar, read-only (ui_v2 + read_chart)
app.include_router(chart_trades_layer_router)  # N4 CH4a: the caller's own PAPER trades on the chart (flag chart_trades_layer, off)
app.include_router(chart_contracts_router)  # N4 CH4b: daily contract / spread charts from the shared lake (flag chart_contracts, off)
app.include_router(chart_futures_basis_router)  # N4 CH4c: futures basis / carry / rollover from the shared lake (flag chart_futures_basis, off)
app.include_router(chart_studies_router)  # N4 CH6: ScreenQL studies on charts from the screener runtime (flag chart_studies, off)
app.include_router(live_router)  # PW4: live updates across Telegram, push and open tabs
app.include_router(beta_admin_router)      # PW6: closed-beta invite codes, cohort and feedback (SUPER_ADMIN)
app.include_router(pwa_router)             # PW6: PWA install/update events (counters only)
app.include_router(feedback_router)        # PW6: "Send feedback"
app.include_router(paper_router)           # Z3-b: the per-user PAPER account and progression ladder (flag paper_account)
app.include_router(decision_tools_router)  # N2-3: risk-of-ruin calculator and position what-if (flag decision_tools)
app.include_router(system_watchlists_router)  # WL1: read-only system lists (indices, index stocks, sectors, F&O); before /{id}
app.include_router(watchlists_router)  # AMW-2: the caller's own named watchlists (flag watchlists, off)
app.include_router(system_watchlists_admin_router)  # WL1: the admin's view of the system lists' refresh (SUPER_ADMIN)
app.include_router(chart_analyze_router)  # Analyze a chart: upload / link a chart image, educational reading (flag chart_analyze, off)
app.include_router(chart_analyze_admin_router)  # Analyze a chart: admin settings (SUPER_ADMIN)
app.include_router(signals_feed_router)  # UI2-3: the Signals inbox feed, read-only (flag ui_v2, off)
app.include_router(signals_day_one_router)  # Signals day one: the setup card + his adopted watch (ui_v2 + strategy_templates)
app.include_router(me_account_router)  # AUD-G1.1: the caller's own data export and "Delete my account"
app.include_router(me_prefs_router)  # UI2-4a1: the caller's own UI preferences (chart theme, grid, colours; per user)
app.include_router(cost_meter_router)  # AUD-T7: the cost-of-trading meter on My trades (flags ui_v2 + cost_meter)
app.include_router(today_router)      # AUD-T1: My day in one card (flags ui_v2 + my_day_card)
app.include_router(rhythm_router)     # MO4: the daily rhythm - pre-market brief, pulse, closing wrap, weekly review (flags ui_v2 + daily_rhythm)
app.include_router(expiry_checklist_router)   # AUD-T2: the expiry-day and margin checklist (flags ui_v2 + expiry_checklist)
app.include_router(oc_ask_router)  # OC3: Ask AI on the Option chain + saved analyses' evaluation (flag ui_v2, off)
app.include_router(oc_probe_admin_router)  # B2b: the owner's read-only option chain spot probe (SUPER_ADMIN + two-factor)
app.include_router(me_setup_router)  # UI2-7: the caller's setup wizard progress (stored marks + steps worked out; reads only)
app.include_router(me_notices_router)  # TT12-1: one-time notices (risk limits now not set; ui_v2; per user)
app.include_router(context_panel_router)   # N2-5: market context panel (flag context_panel)
app.include_router(markets_router)   # MO4 slice 2: the Sector & index view (flags ui_v2 + markets_view)
app.include_router(terminal_router)  # Trading Terminal T1: the status bar's figures, the caller's own (ui_v2 + terminal)
app.include_router(growth_router)    # MO4 slice 3: the plan page + usage meter, the discipline share card (flags ui_v2 + growth_v2)
app.include_router(growth_admin_router)  # C13: the referral reward wording (SUPER_ADMIN + two-factor)
app.include_router(learn_router)     # C13: the Learn section (ui_v2 + learn), the public marketing pages (marketing_site)
app.include_router(paper_ticket_router)    # Z5 / P1-d: plan-before-trade PAPER ticket, risk by default (flag risk_by_default)
app.include_router(paper_fno_router)       # FNO-1: trading from the option chain, PAPER only (flags ui_v2 + paper_account)
app.include_router(incomplete_orders_router)  # SE-7: incomplete multi-leg orders - his answer and his rule (ui_v2 + smart_execution)
app.include_router(paper_fno_insights_router)  # F&O 8b slice 1: risk budget + setup-aware strikes, read-only (ui_v2)
app.include_router(paper_fno_journal_router)  # F&O 8b slice 3: the auto journal drafts + the signal link (ui_v2 + PAPER account)
app.include_router(paper_report_card_router)  # AUD-T4: the paper report card, read-only (ui_v2 + paper_report_card)
app.include_router(setup_recall_router)       # AUD-T6: similar-setup recall + context vs outcome, read-only (ui_v2 + setup_recall)
app.include_router(legal_router)           # Z10-f: Privacy notice, Terms of use, Risk disclosure (public) + acceptance
app.include_router(support_router)         # Z10-f: the how-to support bot (flag support_bot, off by default)
app.include_router(support_desk_router)    # C12: Help centre, Report a problem, contact link (ui_v2 + help_centre, off by default)
app.include_router(support_desk_admin_router)   # C12: the support tickets and desk settings (SUPER_ADMIN + two-factor)

# M55 Q1: the endpoints that used to be defined here, in their old order and last (as before), so the match order holds.
app.include_router(strategy_console_router)    # the console's strategy list / signal / enrich / PAPER execute
app.include_router(backtest_console_router)    # POST /api/backtest
app.include_router(price_action_router)        # /api/price-action/* on supplied candles
app.include_router(support_resistance_router)  # POST /api/support-resistance/zones
app.include_router(option_chain_analyze_router)  # POST /api/option-chain/analyze and /greeks
app.include_router(scanner_run_router)         # POST /api/scanner/run
app.include_router(broker_available_router)    # GET /api/broker/available
app.include_router(instrument_reference_router)  # GET /api/instruments, /api/instruments/{symbol}
app.include_router(health_router)              # GET /api/system/health


# LD-7: a request for a path already resolved skips the scan of every route before its own (app/core/route_cache.py).
install_route_cache(app)

# AUD-G2.1: OpenTelemetry tracing (API requests, SQL, outgoing httpx calls) - off unless OTEL_EXPORTER_OTLP_ENDPOINT is
# set (app/observability/tracing.py). Here and not in the lifespan: the middleware stack is built on the first ASGI
# call (the lifespan's own), so the app is instrumented before that. Never stops the API from starting.
from app.observability import tracing as _tracing  # noqa: E402

_tracing.init("api", app=app)
