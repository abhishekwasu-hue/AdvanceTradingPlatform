"""Phase V2: the compliance validator on AI drafts - checklist, deterministic fixes, one AI
auto-fix round, backtest evidence, the "user must accept" statement and the approval gate."""
import asyncio
import json

from app.ai import generator
from app.ai.compliance import MIN_STOP_ATR_MULT, assess_evidence, evaluate_config
from app.core.models import RiskConfig
from app.db.models import Tenant, User
from app.strategy_engine.declarative import CustomStrategyConfig
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import VALID_ANSWER, _ScriptedProvider, _candles, _owner
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
    cfg = RiskConfig(capital=1_000_000.0, risk_per_trade_pct=1.0)
    ceilings = {"risk_per_trade_pct": 2.0}
    _, report = evaluate_config(_config(stop_loss_atr_mult=0.5, target_rr=(1.0, 0.8)), cfg, ceilings)
    assert report.failed == ["M2", "M1"] and not report.ok
    assert "inside normal noise" in report.failure_text() and "must be ordered" in report.failure_text()
    fixed, report = evaluate_config(_config(stop_loss_atr_mult=0.5, target_rr=(1.0, 0.8)), cfg, ceilings, autofix=True)
    assert report.ok and fixed.stop_loss_atr_mult == MIN_STOP_ATR_MULT and fixed.target_rr == (1.2, 1.2)
    assert report.fixes == ["stop_loss_atr_mult 0.5 -> 1 (M2)", "target_rr (1, 0.8) -> (1.2, 1.2) (M1)"]
    assert [c for c in report.checks if c.rule == "M2"][0].fixed is True
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

    # Round 1 violates M2; the errors go back to the model; round 2 is compliant.
    provider = _ScriptedProvider([_answer(stop_loss_atr_mult=0.4), VALID_ANSWER])
    draft = client.get(f"/api/ai/drafts/{_run(gen(provider))}", headers=headers).json()
    assert len(provider.calls) == 2 and "violated the risk rules" in provider.calls[1] and "M2" in provider.calls[1]
    assert draft["status"] == "DRAFT" and draft["config"]["stop_loss_atr_mult"] == 1.5 and draft["compliance"]["ok"] and draft["compliance"]["fixes"] == []

    # Both rounds violate: the deterministic fix applies on the last one and is on the record.
    provider = _ScriptedProvider([_answer(stop_loss_atr_mult=0.4), _answer(stop_loss_atr_mult=0.3, target_rr=[1.0, 0.9])])
    draft = client.get(f"/api/ai/drafts/{_run(gen(provider))}", headers=headers).json()
    assert draft["status"] == "DRAFT" and draft["config"]["stop_loss_atr_mult"] == 1.0 and draft["config"]["target_rr"] == [1.2, 1.2]
    assert draft["compliance"]["ok"] and len(draft["compliance"]["fixes"]) == 2 and "M2" in draft["compliance"]["fixes"][0]
    assert "max_loss_per_trade_text" in draft["compliance"]["user_must_accept"]


def test_approval_requires_evidence_and_explicit_acceptance():
    headers, me = _owner("v2-approve@example.com")

    async def gen():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, me["tenant_id"])
            user = await session.get(User, me["id"])
            return (await generator.generate(session, tenant, user, "Buy pullbacks in an uptrend on 5 minute bars", provider=_ScriptedProvider([VALID_ANSWER]))).id
    draft_id = _run(gen())
    bt = client.post(f"/api/ai/drafts/{draft_id}/backtest", headers=headers, json={"symbol": "TEST", "base_timeframe": "1min", "candles": _candles(noisy_uptrend(400))})
    assert bt.status_code == 200, bt.text
    compliance = bt.json()["draft"]["compliance"]
    assert compliance["evidence"] is not None and "E1" in (compliance["warnings"] + compliance["passed"])
    refused = client.post(f"/api/ai/drafts/{draft_id}/approve", headers=headers, json={})
    assert refused.status_code == 400 and "accept the risk" in refused.json()["detail"] and "can lose up to" in refused.json()["detail"]
    ok = client.post(f"/api/ai/drafts/{draft_id}/approve", headers=headers, json={"accept_risk": True})
    assert ok.status_code == 200, ok.text
    assert ok.json()["draft"]["status"] == "APPROVED" and ok.json()["draft"]["compliance"]["ok"]
