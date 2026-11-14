"""Runner S1: the module boundary of the runner (docs/design/USER_RUNNER.md section 2, step S1).

`RUNNER_MODULES` are the entry points the runner may import: the DSL and the public inbuilt templates, indicators, the
broker adapters (Angel One first, RUN-Q3), the router, the OPS throttle, tagging, market protection, the compliance rules,
the PAPER broker and the one entry / close pipeline. Everything they import at module level (their closure) ships with
them.

`FORBIDDEN_PREFIXES` must never be in that closure: the private package and its loaders (never to users), AI (provider
keys), admin, marketplace, the Strategy Lab, the server's multi-tenant worker and app, and server-only data / billing.

`KNOWN_EDGES` are the imports of a forbidden module that the closure holds TODAY, each reviewed, with why it is not on the
runner's path or which build step removes it. The test fails on any edge not listed here (a new coupling) and on any
listed edge that is gone (keep the list exact). `RUNTIME_FORBIDDEN` must not be loaded at all when the entry pipeline
runs (the S1 spike checks the process's modules).
"""
from __future__ import annotations

RUNNER_MODULES: tuple[str, ...] = (
    # strategy evaluation: the DSL (ADR-0005, the same code as backtest / paper / worker) and the inbuilt templates
    "app.strategy_engine.base",
    "app.strategy_engine.declarative",
    "app.strategy_engine.indicator_strategies",
    "app.strategy_engine.combo_strategies",
    "app.strategy_engine.swing_strategies",
    "app.strategy_engine.mtf_strategy",
    "app.strategy_engine.registry",
    "app.indicators",
    # broker adapters, from the user's own machine and key (RUN-Q3: Angel One first)
    "app.brokers.base",
    "app.brokers.models",
    "app.brokers.exceptions",
    "app.brokers.timestamps",
    "app.brokers.circuit_breaker",
    "app.brokers.rate_budget",
    "app.brokers.totp",
    "app.brokers.token_lifecycle",
    "app.brokers.angel_one",
    "app.brokers.upstox",
    "app.brokers.zerodha",
    "app.brokers.dhan",
    # order routing, OPS throttle, algo tag, market protection, rules as data
    "app.execution.router",
    "app.execution.ops_throttle",
    "app.execution.tagging",
    "app.execution.order_safety",
    "app.compliance.order_policy",
    "app.compliance.rules",
    # PAPER on the runner (the same code path as LIVE)
    "app.execution.paper_broker",
    "app.execution.paper_charges",
    "app.execution.india_costs",
    # the one entry pipeline and the one close path (ADR-0004: exits are never blocked)
    "app.risk_engine.risk_manager",
    "app.risk_engine.by_default",
    "app.risk_engine.hierarchy",
    "app.risk_engine.guardian",
    "app.kill_switch.checks",
    "app.execution.signal_execution",
    "app.trading.position_monitor",
    # Runner S2: the loop's own reads - the local store, the strategy lookup, closed bars, his windows / defaults, the
    # PAPER-only lock, the shared square-off and the runner-mode seam (billing meter off, the ban list from ATP)
    "app.core.runtime_mode",
    "app.core.enums",
    "app.core.models",
    "app.db.base",
    "app.db.models",
    "app.db.session",
    "app.custom_strategies.resolver",
    "app.market_data.calendar",
    "app.market_data.freshness",
    "app.execution.exit_settings",
    "app.execution.order_defaults",
    "app.platform.phase0_lock",
    "app.trading.square_off",
)

FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "atp_private",          # the private package: Trade engine snapshot, chart reader method, KB, Method Pack
    "app.engine_provider",  # its loader (M66 EN-1)
    "app.chart_reader",     # its loader (M86 W1)
    "app.chart_analyze",    # the analysis engine loader (private package)
    "app.price_action",     # the reader port: not needed to run a DSL deployment (design section 2)
    "app.ai",               # AI features and the platform's AI provider keys
    "app.admin",
    "app.marketplace",
    "app.lab",              # Strategy Lab code modes: research only, never trade (ADR-0027)
    "app.workers",          # the server's multi-tenant worker (the runner gets its own slim loop, S2)
    "app.main",             # the server's web app
    "app.billing",          # plans / payments are server features
    "app.market_lake",      # the server's data lake and its jobs
)

# (importer, forbidden module imported, "top" | "lazy") -> why it is tolerated today and what ends it.
KNOWN_EDGES: dict[tuple[str, str, str], str] = {
    ("app.execution.order_persistence", "app.billing.service", "lazy"):
        "usage meter (order_persistence.py create_order): server only - S2 made it a lazy import that runner mode never "
        "reaches (core/runtime_mode.py; the runner meters nothing, its orders reach ATP as copies in S4).",
    ("app.compliance.fo_limits", "app.market_lake.asof", "lazy"):
        "F&O ban-list read from the server's lake (fo_limits.ban_status): S2 made it lazy; in runner mode the list ATP "
        "sends (S4) is read instead - unknown = PAPER allowed with a note, LIVE F&O entry refused; exits never read it.",
    ("app.execution.approval", "app.ai", "lazy"):
        "approval-only mode: an entry proposal's Vision audit (approval.py:318); not on the automatic entry path. "
        "S2: decide the runner's approval mode (proposals answered in ATP, executed on the runner).",
    ("app.execution.approval", "app.ai.vision_audit", "lazy"): "as app.ai (approval.py:318).",
    ("app.execution.approval", "app.ai.monitor", "lazy"): "approval-only mode: settles an overdue proposal (approval.py:472).",
    ("app.execution.approval", "app.ai.worker_bridge", "lazy"):
        "approval-only mode: re-quote / exit-now price lookup (approval.py:512, 779, 854); not on the automatic path.",
    ("app.platform.controls", "app.engine_provider", "lazy"):
        "engine_missing_flags (controls.py:789): the admin flag list and /api/system/features only, never the entry path "
        "(the S1 spike shows it is not loaded). S6: the runner image leaves the package out; that function then needs "
        "to read an absent package as 'unavailable'.",
    ("app.platform.controls", "app.engine_provider.loader", "lazy"): "as app.engine_provider (controls.py:792).",
    ("app.platform.controls", "app.chart_reader", "lazy"): "as app.engine_provider (controls.py:798).",
    ("app.platform.controls", "app.chart_reader.loader", "lazy"): "as app.engine_provider (controls.py:798).",
    ("app.platform.controls", "app.chart_analyze", "lazy"): "as app.engine_provider (controls.py:791).",
    ("app.platform.controls", "app.chart_analyze.provider", "lazy"): "as app.engine_provider (controls.py:791).",
    ("app.alerts.signed_webhooks", "app.chart_analyze.linkfetch", "lazy"):
        "a signed webhook's chart link fetch (signed_webhooks.py:79); alerts only, not the entry path.",
}

# Never loaded in the runner's process, not even lazily, when the entry pipeline runs (the S1 spike).
RUNTIME_FORBIDDEN: tuple[str, ...] = (
    "atp_private", "app.engine_provider", "app.chart_reader", "app.chart_analyze", "app.price_action", "app.ai",
    "app.admin", "app.marketplace", "app.lab", "app.workers", "app.main",
    "app.billing", "app.market_lake",   # S2: the meter and the lake ban-list read are server-only seams now
)


# Runner S5: the local keystore, the daily broker login and their CLI, plus what the login reads (the NSE calendar's
# open / close and trading days). Their closure is walked like the rest; de-duplicated so a later step may list the same.
RUNNER_MODULES = tuple(dict.fromkeys(RUNNER_MODULES + (
    "app.market_data.calendar",
    "app.runner.keystore",
    "app.runner.broker_login",
    "app.runner.keys",
)))


# Runner S4: the link to ATP (outbound HTTPS, signed with the device key), the local rules sync / mirror and the S5 wiring.
# `app.runner_link.signing` is the ONE canonical-signing module both sides import (cryptography only; the rest of
# app.runner_link is ATP's server side and is never imported here).
RUNNER_MODULES = tuple(dict.fromkeys(RUNNER_MODULES + (
    "app.runner_link.signing",
    "app.runner.link",
    "app.runner.sync",
    "app.runner.broker_session",
)))


def is_forbidden(module: str, prefixes: tuple[str, ...] = FORBIDDEN_PREFIXES) -> bool:
    """True when `module` is one of `prefixes` or inside one of them."""
    return any(module == p or module.startswith(p + ".") for p in prefixes)


__all__ = ["RUNNER_MODULES", "FORBIDDEN_PREFIXES", "KNOWN_EDGES", "RUNTIME_FORBIDDEN", "is_forbidden"]
