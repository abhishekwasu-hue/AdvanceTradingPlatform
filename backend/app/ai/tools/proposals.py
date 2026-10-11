"""H-C2b (ADR-0019 §1, §3): proposal tools and their injection guard.

A proposal tool is the only kind of tool that creates anything, and what it creates is a PROPOSED row in the monitoring
agent's action queue (`ai_actions`, ADR-0006): a person approves or rejects it under AI Copilot, and `monitor.execute`
runs only an approved row. No proposal tool sends, modifies or cancels an order, and none changes a LIVE setting.

This slice proposes de-risking only:
* propose_pause_deployment - PAUSE_DEPLOYMENT for one of this organisation's ACTIVE deployments
* propose_risk_reduction   - REDUCE_RISK: a tighter value for one risk setting. On approval it is acknowledged; the
                             person makes the change on the Risk page (no automatic change, as for the monitor's own
                             REDUCE_RISK)
* propose_strategy_review  - REVIEW_STRATEGY for a deployment

The guard (`guard`) runs in the agent loop before any proposal tool; `run_tool` refuses a proposal tool the loop has
not cleared. A call passes only when:
(a) the tool is on the allow-list for the request's intent - the trader's OWN message asks for that kind of action
    (`allowed_for`); tools off the list are not even offered to the model, so an instruction inside a headline cannot
    add one;
(b) its `quote` argument is the trader's own words: a piece of the message that contains the words which allowed the
    tool, and does not also appear in third-party (untrusted) text read during the request;
(c) no proposal was already made in this request.
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Literal, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.ai.tools import Tool, ToolContext, ToolResult, register
from app.core.models import RiskConfig
from app.db.models import StrategyDeploymentRecord

# The words in the trader's own message that put a proposal tool on the request's allow-list (English and Marathi).
INTENT_WORDS: Dict[str, Sequence[str]] = {
    "propose_pause_deployment": ("pause", "halt", "थांबव", "बंद कर", "बंद करा"),
    "propose_risk_reduction": ("reduce risk", "reduce my risk", "lower risk", "lower my risk", "cut risk", "cut my risk", "less risk",
                               "tighten risk", "risk कमी", "जोखीम कमी", "धोका कमी"),
    "propose_strategy_review": ("review the strategy", "review my strategy", "review strategy", "strategy review", "review the deployment",
                                "review deployment", "strategy तपास", "पुनरावलोकन"),
}

# Risk settings a proposal may only tighten: "lower" means a smaller value is safer, "higher" a larger one.
SAFER: Dict[str, str] = {
    "risk_per_trade_pct": "lower", "max_daily_loss_pct": "lower", "max_trades_per_day": "lower", "max_open_positions": "lower",
    "max_consecutive_losses": "lower", "max_portfolio_risk_pct": "lower", "dd_level_1_pct": "lower", "dd_level_2_pct": "lower",
    "min_risk_reward": "higher", "stop_cooldown_minutes": "higher",
}

NOTE = "A person must approve or reject this under AI Copilot; nothing changes until then."


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def allowed_for(question: str) -> List[str]:
    """The proposal tools the trader's own message asks for - the request's allow-list."""
    q = _norm(question)
    return [name for name, words in INTENT_WORDS.items() if any(w in q for w in words)]


def guard(name: str, arguments: Dict, question: str, allowed: Sequence[str], untrusted_texts: Sequence[str], proposals_made: int,
          max_proposals: int) -> Optional[str]:
    """None when the proposal call may run; otherwise why it is refused (audited, and told to the model)."""
    if name not in allowed:
        return "the trader's message does not ask for this kind of action"
    if proposals_made >= max_proposals:
        return f"at most {max_proposals} proposal per request"
    quote = _norm(str((arguments or {}).get("quote") or ""))
    if not quote or quote not in _norm(question):
        return "the quote is not the trader's own words"
    if not any(w in quote for w in INTENT_WORDS.get(name, ())):
        return "the quote does not contain the words that ask for this action"
    if any(quote in _norm(text) for text in untrusted_texts):
        return "the quoted words also appear in third-party data read in this request"
    return None


class _ProposalArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=10, max_length=300, description="Why, from the tool results - shown to the person who decides")
    quote: str = Field(min_length=2, max_length=300, description="The trader's own words that ask for this action, copied exactly from their message")


class _DeploymentProposal(_ProposalArgs):
    deployment_id: int = Field(ge=1, description="The deployment id, as returned by a tool")


class _RiskProposal(_ProposalArgs):
    setting: Literal["risk_per_trade_pct", "max_daily_loss_pct", "max_trades_per_day", "max_open_positions", "max_consecutive_losses",
                     "max_portfolio_risk_pct", "dd_level_1_pct", "dd_level_2_pct", "min_risk_reward", "stop_cooldown_minutes"]
    proposed_value: float = Field(gt=0, description="The new, tighter value")


async def _deployment(ctx: ToolContext, deployment_id: int) -> Optional[StrategyDeploymentRecord]:
    return await ctx.session.scalar(select(StrategyDeploymentRecord).where(
        StrategyDeploymentRecord.id == deployment_id, StrategyDeploymentRecord.tenant_id == ctx.tenant_id))


async def _raise(ctx: ToolContext, deployment_id: Optional[int], action: str, rule: str, sentence: str, args: _ProposalArgs, evidence: Dict) -> ToolResult:
    from app.ai import monitor
    reason = f"{sentence} Asked by the trader (\"{args.quote.strip()}\"). AI note: {args.reason.strip()}"
    evidence = {**evidence, "source": "copilot_agent", "quote": args.quote.strip(), "agent_reason": args.reason.strip(), "user_id": ctx.user.id}
    created = await monitor.raise_proposals(ctx.session, ctx.tenant_id, [monitor.Proposal(deployment_id, None, action, rule, reason, evidence)], ctx.now)
    now = ctx.now.isoformat()
    if not created:
        return ToolResult(True, {"created": False, "reason": "an open proposal of this kind already exists, or one was decided today"}, now, "ai_actions")
    row = created[0]
    return ToolResult(True, {"created": True, "proposal_id": row.id, "action": action, "status": row.status, "note": NOTE}, now, "ai_actions")


async def propose_pause_deployment(ctx: ToolContext, args: _DeploymentProposal) -> ToolResult:
    """Propose pausing one ACTIVE deployment (no new entries; open positions keep their exits). A person decides."""
    dep = await _deployment(ctx, args.deployment_id)
    if dep is None:
        return ToolResult(False, None, None, "ai_actions", error=f"deployment {args.deployment_id} not found")
    if dep.status != "ACTIVE":
        return ToolResult(False, None, None, "ai_actions", error=f"deployment {dep.id} is {dep.status}, not ACTIVE")
    return await _raise(ctx, dep.id, "PAUSE_DEPLOYMENT", "AGENT_PAUSE", f"Pause deployment #{dep.id} ({dep.strategy_id} on {dep.symbol}, {dep.mode}).",
                        args, {"deployment_status": dep.status})


async def propose_strategy_review(ctx: ToolContext, args: _DeploymentProposal) -> ToolResult:
    """Propose a review of one deployment's strategy. A person decides; approval changes nothing by itself."""
    dep = await _deployment(ctx, args.deployment_id)
    if dep is None:
        return ToolResult(False, None, None, "ai_actions", error=f"deployment {args.deployment_id} not found")
    return await _raise(ctx, dep.id, "REVIEW_STRATEGY", "AGENT_REVIEW", f"Review deployment #{dep.id} ({dep.strategy_id} on {dep.symbol}).", args, {})


async def propose_risk_reduction(ctx: ToolContext, args: _RiskProposal) -> ToolResult:
    """Propose a TIGHTER value for one organisation risk setting. A person decides and makes the change on the Risk page."""
    from app.risk_engine.routes import get_tenant_risk_config
    config = await get_tenant_risk_config(ctx.tenant_id, ctx.session) or RiskConfig()
    current = float(getattr(config, args.setting))
    tighter = args.proposed_value < current if SAFER[args.setting] == "lower" else args.proposed_value > current
    if not tighter:
        return ToolResult(False, None, None, "ai_actions",
                          error=f"{args.setting} is {current:g}; a proposal may only make it {SAFER[args.setting]} (asked {args.proposed_value:g})")
    return await _raise(ctx, None, "REDUCE_RISK", "AGENT_RISK", f"Change {args.setting} from {current:g} to {args.proposed_value:g}.", args,
                        {"setting": args.setting, "current": current, "proposed": args.proposed_value})


for _tool in (
    Tool("propose_pause_deployment", propose_pause_deployment.__doc__ or "", _DeploymentProposal, propose_pause_deployment, kind="proposal", cost_units=2),
    Tool("propose_risk_reduction", propose_risk_reduction.__doc__ or "", _RiskProposal, propose_risk_reduction, kind="proposal", cost_units=2),
    Tool("propose_strategy_review", propose_strategy_review.__doc__ or "", _DeploymentProposal, propose_strategy_review, kind="proposal", cost_units=2),
):
    register(_tool)


def untrusted_text(data) -> str:
    """The raw third-party text of an untrusted tool result, for the guard's quote check."""
    return json.dumps(data, default=str, ensure_ascii=False)


__all__ = ["INTENT_WORDS", "SAFER", "allowed_for", "guard", "untrusted_text"]
