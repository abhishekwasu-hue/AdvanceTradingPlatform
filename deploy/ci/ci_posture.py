"""CI staging stack: the feature-flag posture the Phase 0 acceptance flow runs under, in the stack's own throwaway
database. Imported by deploy/ci/acceptance.py (sets them through the admin API, as the owner would) and
deploy/ci/seed_users.py (the load test's stand-alone stack). Nothing here touches the flag defaults in the code
(DEFAULT_OFF_FLAGS) or any real host.

PHASE0_LOCK: what the owner sets on the beta host (docs/deploy/HOSTINGER_RUNBOOK.md section 4a).
FEATURES: the Phase 0 features the acceptance flow walks through (docs/specs/ATP_PHASE0_FREEZE.md section 1).
"""
from typing import Dict

PHASE0_LOCK: Dict[str, bool] = {
    "live_trading": False,            # no new LIVE deployments
    "phase0_paper_only": True,        # every new LIVE entry refused, also at the broker-order chokepoint
    "beta_cohort_gate": True,         # sign-up needs an invite code
}

FEATURES: Dict[str, bool] = {
    "byob_data_tier": True,           # live data only through each user's own broker session (Upstox, Dhan: Tier A)
    "screener_v2": True,              # screens, saved screens, alert rules
    "approval_only_entries": True,    # every entry is a proposal; Approve places a PAPER order
    "paper_account": True,            # each user's PAPER account
    "risk_by_default": True,          # the plan-before-trade PAPER ticket
    "discipline_engine": True,        # the journal
    "alerts_stale_guard": True,       # stale data -> no alert
    "alerts_reliable_outbox": True,
}

POSTURE: Dict[str, bool] = {**PHASE0_LOCK, **FEATURES}
