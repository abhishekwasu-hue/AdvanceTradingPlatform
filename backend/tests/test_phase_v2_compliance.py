"""Phase V2: the compliance validator on AI drafts - checklist, deterministic fixes, one AI
auto-fix round, backtest evidence, the "user must accept" statement and the approval gate."""
import asyncio
import json

from app.ai import generator
from app.ai.compliance import MIN_STOP_ATR_MULT, assess_evidence, evaluate_config
from app.core.models import RiskConfig
from app.db.models import Tenant, User
from app.strategy_engine.declarative import CustomStrategyConfig
from tests.def0_legacy import legacy
from tests.server_evidence import serve_candles
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import VALID_ANSWER, _candles, _owner, _ScriptedProvider
from tests.utils import noisy_uptrend


def _run(coro):
    return asyncio.run(coro)


def _answer(**overrides) -> str:
    data = json.loads(VALID_ANSWER)
    data["config"].update(overrides)
    return json.dumps(data)


def _config(**overrides) -> CustomStrategyConfig:
    return CustomStrategyConfig.model_validate({**json.loads(VALID_ANSWER)["config"], **overrides})


def test_checklist_fails_draft_rules_and_fixes_them_deterministically():
    cfg = legacy(capital=1_000_000.0, risk_per_trade_pct=1.0)        # DEF-0: the guardian rules the organisation set
    ceilings = {"risk_per_trade_pct": 2.0}
    # P1-2b: the draft states its own minimum (1.3); a stated 1.2 or none = no minimum (no hidden filter).
    _, report = evaluate_config(_config(stop_loss_atr_mult=0.5, target_rr=(1.0, 0.8), min_rr=1.3), cfg, ceilings)
    # M68 PR-8 (TS1-078): a stop inside 1 x ATR is a suggestion (WARN), never a failure or a change of his stop
    assert report.failed == ["M1"] and "M2" in report.warnings and not report.ok
    assert "must be ordered" in report.failure_text()
    m2 = [c for c in report.checks if c.rule == "M2"][0]
    assert "inside normal noise" in m2.detail and f"suggested: {MIN_STOP_ATR_MULT:g} x ATR or wider" in m2.detail
    fixed, report = evaluate_config(_config(stop_loss_atr_mult=0.5, target_rr=(1.0, 0.8), min_rr=1.3), cfg, ceilings, autofix=True)
    assert report.ok and fixed.stop_loss_atr_mult == 0.5 and fixed.target_rr == (1.3, 1.3)       # the stop is kept as written
    assert report.fixes == ["target_rr (1, 0.8) -> (1.3, 1.3) (M1)"]
    # No minimum stated: only the order is fixed, never raised to a number nobody stated.
    unset, plain = evaluate_config(_config(stop_loss_atr_mult=0.5, target_rr=(1.0, 0.8)), cfg, ceilings, autofix=True)
    assert plain.ok and unset.min_rr is None and unset.target_rr == (0.8, 1.0)
    assert [c for c in report.checks if c.rule == "M2"][0].fixed is False and [c for c in report.checks if c.rule == "M2"][0].status == "WARN"
    # The engine-enforced rules report the tenant's live values; deployment-level ones are N/A.
    by_rule = {c.rule: c for c in report.checks}
    assert by_rule["R4"].status == "PASS" and "6%" in by_rule["R4"].detail
    assert by_rule["R10"].status == "PASS" and "30 min" in by_rule["R10"].detail
    assert by_rule["R8"].status == "N/A" and by_rule["M4"].status == "N/A" and by_rule["M9"].status == "N/A"
    assert by_rule["R6"].status == "PASS" and by_rule["R7"].status == "PASS"
    # The statement the human must accept, in currency and % of capital.
    assert "10,000 INR (1% of the 1,000,000 capital)" in report.user_must_accept["max_loss_per_trade_text"]
    assert "~30,000 INR (3.0% of capital)" in report.user_must_accept["worst_case_text"] and "pauses entries at 10%" in report.user_must_accept["worst_case_text"]
    # A risk per trade above the ceiling is a warning (the engine clamps), not the draft's failure.
    _, over = evaluate_config(_config(), RiskConfig(risk_per_trade_pct=3.0), ceilings)
    assert "R2" in over.warnings and over.ok
    _, thin = evaluate_config(_config(min_rr=0.8, target_rr=(0.8, 1.0)), cfg, ceilings)
    assert "RR" in thin.warnings


def test_evidence_is_judged_plainly():
    weak = assess_evidence({"total_trades": 8, "win_rate": 0.4, "net_pnl": -1200.0, "profit_factor": 0.7, "max_drawdown": -20000.0}, 100_000.0)
    assert weak["strength"] == "weak" and len(weak["warnings"]) == 3
    assert any("only 8 backtest trade" in w for w in weak["warnings"]) and any("lost money" in w for w in weak["warnings"]) and any("20.0% of capital" in w for w in weak["warnings"])
    fine = assess_evidence({"total_trades": 60, "win_rate": 0.55, "net_pnl": 8000.0, "profit_factor": 1.6, "max_drawdown": -3000.0}, 100_000.0)
    assert fine["strength"] == "adequate" and fine["warnings"] == [] and "60 trades" in fine["summary"]
    _, report = evaluate_config(_config(), RiskConfig(), {}, backtest={"total_trades": 8, "net_pnl": -5.0})
    assert "E1" in report.warnings and report.evidence["strength"] == "weak" and report.ok


def test_generator_gives_the_ai_one_auto_fix_round_then_fixes_deterministically():
    headers, me = _owner("v2-gen@example.com")

    async def gen(provider):
        async with _session_factory() as session:
            tenant = await session.get(Tenant, me["tenant_id"])
            user = await session.get(User, me["id"])
            return (await generator.generate(session, tenant, user, "Buy pullbacks on 5 minute bars with a tight stop", provider=provider)).id

    # M68 PR-8 (TS1-078): a tight stop (0.4 x ATR) is the draft's own and only a suggestion - no retry round, kept as written.
    provider = _ScriptedProvider([_answer(stop_loss_atr_mult=0.4), VALID_ANSWER])
    draft = client.get(f"/api/ai/drafts/{_run(gen(provider))}", headers=headers).json()
    assert len(provider.calls) == 1 and draft["config"]["stop_loss_atr_mult"] == 0.4 and draft["compliance"]["ok"]
    assert "M2" in draft["compliance"]["warnings"] and draft["compliance"]["fixes"] == []

    # Round 1 violates M1 (targets out of order); the errors go back to the model; round 2 is compliant.
    provider = _ScriptedProvider([_answer(target_rr=[1.0, 0.9]), VALID_ANSWER])
    draft = client.get(f"/api/ai/drafts/{_run(gen(provider))}", headers=headers).json()
    assert len(provider.calls) == 2 and "violated the risk rules" in provider.calls[1] and "M1" in provider.calls[1]
    assert draft["status"] == "DRAFT" and draft["config"]["stop_loss_atr_mult"] == 1.5 and draft["compliance"]["ok"] and draft["compliance"]["fixes"] == []

    # Both rounds violate: the deterministic fix applies on the last one and is on the record; the stop is never widened.
    provider = _ScriptedProvider([_answer(target_rr=[1.0, 0.9]), _answer(stop_loss_atr_mult=0.3, target_rr=[1.0, 0.9])])
    draft = client.get(f"/api/ai/drafts/{_run(gen(provider))}", headers=headers).json()
    assert draft["status"] == "DRAFT" and draft["config"]["stop_loss_atr_mult"] == 0.3 and draft["config"]["target_rr"] == [0.9, 1.0]   # P1-2b: ordered only, no minimum stated
    assert draft["compliance"]["ok"] and len(draft["compliance"]["fixes"]) == 1 and "M1" in draft["compliance"]["fixes"][0]
    assert "max_loss_per_trade_text" in draft["compliance"]["user_must_accept"]


def test_approval_requires_evidence_and_explicit_acceptance(monkeypatch):
    headers, me = _owner("v2-approve@example.com")
    # DEF-0: the statement names a loss amount only when a risk per trade is set - the organisation sets one
    assert client.put("/api/risk-settings", headers=headers, json=legacy().model_dump()).status_code == 200

    async def gen():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, me["tenant_id"])
            user = await session.get(User, me["id"])
            return (await generator.generate(session, tenant, user, "Buy pullbacks in an uptrend on 5 minute bars", provider=_ScriptedProvider([VALID_ANSWER]))).id
    draft_id = _run(gen())
    serve_candles(monkeypatch, _candles(noisy_uptrend(400)))           # H-C1 a: the server fetches the evidence
    bt = client.post(f"/api/ai/drafts/{draft_id}/backtest", headers=headers, json={"symbol": "TEST", "base_timeframe": "1min"})
    assert bt.status_code == 200, bt.text
    compliance = bt.json()["draft"]["compliance"]
    assert compliance["evidence"] is not None and "E1" in (compliance["warnings"] + compliance["passed"])
    refused = client.post(f"/api/ai/drafts/{draft_id}/approve", headers=headers, json={})
    assert refused.status_code == 400 and "accept the risk" in refused.json()["detail"] and "can lose up to" in refused.json()["detail"]
    ok = client.post(f"/api/ai/drafts/{draft_id}/approve", headers=headers, json={"accept_risk": True})
    assert ok.status_code == 200, ok.text
    assert ok.json()["draft"]["status"] == "APPROVED" and ok.json()["draft"]["compliance"]["ok"]
