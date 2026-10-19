"""DEFAULT_OFF_FLAGS is computed (FEATURE_FLAGS minus a closed DEFAULT_ON_FLAGS list) instead of a hand-kept literal.

The change must not move any flag's default. The snapshot below is the literal DEFAULT_OFF_FLAGS of origin/main at
60ce742, taken by importing the module. Every flag that existed then keeps exactly its old default; a flag registered
later is OFF by default without being listed anywhere else (so this file need not change when a flag is added)."""
import ast
from pathlib import Path

from app.platform import controls, deployment_phase

# origin/main 60ce742: DEFAULT_OFF_FLAGS as it was when it was still a literal (58 flags).
OLD_DEFAULT_OFF = frozenset({
    "ai_agent",
    "ai_research",
    "alerts_reliable_outbox",
    "alerts_stale_guard",
    "alerts_whatsapp_dlt",
    "approval_only_entries",
    "backtest_models",
    "beta_cohort_gate",
    "broker_app_links",
    "byob_data_tier",
    "chart_contracts",
    "chart_price_action",
    "chart_studies",
    "chart_trades_layer",
    "chart_workspace",
    "decision_tools",
    "discipline_engine",
    "evidence_charts",
    "evidence_context",
    "execution_core",
    "experience_levels",
    "history_backfill",
    "history_import",
    "history_intraday",
    "ladder_evidence",
    "legal_pages",
    "market_thesis",
    "market_zones",
    "marketplace_ai_listings",
    "news_feed",
    "options_builder_saved",
    "paper_account",
    "phase0_paper_only",
    "portfolio_risk",
    "portfolio_risk_caps",
    "public_status_page",
    "risk_by_default",
    "scan_auto_paper",
    "scan_then",
    "screener_backtest",
    "screener_breakouts",
    "screener_breakouts_bk3",
    "screener_breakouts_bk4",
    "screener_etf",
    "screener_gallery",
    "screener_history",
    "screener_intrabar",
    "screener_legends",
    "screener_legends_lg2",
    "screener_tx2",
    "screener_v2",
    "screener_vectorized",
    "support_bot",
    "telegram_inbound",
    "thesis_stock_targets",
    "vision_byok",
    "vision_byok_v3",
    "vix_gate",
})
# origin/main 60ce742: the flags in FEATURE_FLAGS but not in DEFAULT_OFF_FLAGS (kill flags, ON by default).
OLD_DEFAULT_ON = frozenset({
    "ai_copilot", "backtest_optimizer", "live_trading", "marketplace", "options_builder", "public_api", "self_signup",
})


def test_default_on_is_exactly_the_old_set():
    # Closed list: a new flag never lands here (never turn a flag on).
    assert controls.DEFAULT_ON_FLAGS == OLD_DEFAULT_ON
    assert isinstance(controls.DEFAULT_ON_FLAGS, frozenset)


def test_default_on_is_a_subset_of_feature_flags():
    assert controls.DEFAULT_ON_FLAGS <= set(controls.FEATURE_FLAGS)
    assert not controls.DEFAULT_ON_FLAGS & controls.DEFAULT_OFF_FLAGS


def test_computed_default_off_matches_the_old_literal():
    old_era = OLD_DEFAULT_OFF | OLD_DEFAULT_ON
    # Every flag registered on main at the snapshot keeps exactly its old default.
    assert controls.DEFAULT_OFF_FLAGS & old_era == OLD_DEFAULT_OFF
    # Nothing that was OFF disappeared from the registry (DEFAULT_OFF had no entry outside FEATURE_FLAGS).
    assert OLD_DEFAULT_OFF <= controls.DEFAULT_OFF_FLAGS
    assert OLD_DEFAULT_OFF <= set(controls.FEATURE_FLAGS)
    # Any flag registered after the snapshot is OFF by default.
    newer = set(controls.FEATURE_FLAGS) - old_era
    assert newer <= controls.DEFAULT_OFF_FLAGS
    assert controls.DEFAULT_OFF_FLAGS == frozenset(controls.FEATURE_FLAGS) - controls.DEFAULT_ON_FLAGS
    assert isinstance(controls.DEFAULT_OFF_FLAGS, frozenset)


def test_live_trading_keeps_kill_flag_semantics():
    # ON by default exactly as on main: the flag only switches off new LIVE deployments. The LIVE gate itself is the
    # trading:live scope, the plan and step-up (deployments/routes.py) and the phase0_paper_only lock (OFF by default);
    # Phase 0 deployment mode seeds live_trading OFF where nothing is stored (deployment_phase.py).
    assert "live_trading" in controls.FEATURE_FLAGS
    assert "live_trading" in controls.DEFAULT_ON_FLAGS and "live_trading" not in controls.DEFAULT_OFF_FLAGS
    assert "phase0_paper_only" in controls.DEFAULT_OFF_FLAGS
    assert deployment_phase.LIVE_TRADING == "live_trading"


def test_feature_flags_literal_has_no_duplicate_keys():
    # A dict literal keeps the last of two equal keys silently; a bad merge could hide a flag's first description.
    source = Path(controls.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "FEATURE_FLAGS":
            assert isinstance(node.value, ast.Dict)
            keys = [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
            assert len(keys) == len(set(keys)) == len(controls.FEATURE_FLAGS)
            return
    raise AssertionError("FEATURE_FLAGS literal not found in controls.py")
