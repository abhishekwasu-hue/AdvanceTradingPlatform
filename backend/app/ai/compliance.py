"""Phase V2: the compliance validator the Risk Guardian spec puts behind the AI (section 3:
"do not trust the AI alone").

Every AI draft is re-checked here before it can be backtested or approved. The checks mirror
the spec's rule list (R1-R10, M1-M9, P1-P5) against what the platform can actually verify on a
`CustomStrategyConfig` plus the tenant's effective `RiskConfig`:

* rules the draft itself decides (a stop before entry, a stop outside normal noise, targets
  consistent with the minimum R:R) are checked on the config and, on the final attempt,
  **fixed deterministically** ("recompute & warn" in the spec's table) so a compliant strategy
  is what gets saved;
* rules the engine enforces on every entry (size from risk, portfolio cap, daily kill switch,
  cool-down, drawdown ladder, event blackout, defined-risk structures) are reported as PASS
  with the tenant's live values, so the user sees the whole checklist, not just the draft's
  part;
* rules that live on the deployment (break-even, trailing, regime filter) are N/A with a
  pointer, never silently passed.

The report also carries the spec's **"user must accept"** statement - the maximum loss per
trade in currency and % of capital, and the worst case (a gap through the stop, the daily loss
limit, the drawdown ladder) - which the approval step requires the human to confirm. When a
backtest is attached, the evidence is judged too (few trades, weak profit factor, a losing
result) as warnings that stay on the record: the AI never flatters a strategy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from app.core.models import RiskConfig
from app.strategy_engine.declarative import CustomStrategyConfig

MIN_STOP_ATR_MULT = 1.0                 # M2: a stop inside one ATR sits inside normal noise
GAP_MULTIPLE = 3.0                      # worst case: a gap through the stop costs about this many planned losses
WEAK_EVIDENCE_MIN_TRADES = 30
WEAK_EVIDENCE_MIN_PROFIT_FACTOR = 1.1
WEAK_EVIDENCE_MAX_DRAWDOWN_PCT = 15.0   # of capital


@dataclass
class CheckResult:
    rule: str
    status: str          # PASS / FAIL / WARN / N/A
    detail: str
    fixed: bool = False

    def as_dict(self) -> dict:
        return {"rule": self.rule, "status": self.status, "detail": self.detail, "fixed": self.fixed}


@dataclass
class ComplianceReport:
    checks: List[CheckResult] = field(default_factory=list)
    fixes: List[str] = field(default_factory=list)
    user_must_accept: Dict[str, str] = field(default_factory=dict)
    evidence: Optional[dict] = None

    @property
    def passed(self) -> List[str]:
        return [c.rule for c in self.checks if c.status == "PASS"]

    @property
    def failed(self) -> List[str]:
        return [c.rule for c in self.checks if c.status == "FAIL"]

    @property
    def warnings(self) -> List[str]:
        return [c.rule for c in self.checks if c.status == "WARN"]

    @property
    def ok(self) -> bool:
        return not self.failed

    def failure_text(self) -> str:
        return "; ".join(f"{c.rule}: {c.detail}" for c in self.checks if c.status == "FAIL")

    def as_dict(self) -> dict:
        return {"ok": self.ok, "checks": [c.as_dict() for c in self.checks], "passed": self.passed, "failed": self.failed,
                "warnings": self.warnings, "fixes": self.fixes, "user_must_accept": self.user_must_accept, "evidence": self.evidence}


def _money(value: float) -> str:
    return f"{value:,.0f}"


def evaluate_config(config: CustomStrategyConfig, cfg: RiskConfig, ceilings: Optional[Dict[str, float]] = None, *,
                    backtest: Optional[dict] = None, autofix: bool = False, currency: str = "INR",
                    suggestion=None) -> Tuple[CustomStrategyConfig, ComplianceReport]:
    """Run the checklist. With `autofix`, the draft-level failures are corrected in the returned
    config and reported as PASS with `fixed=True` plus a line in `fixes`. `suggestion` (Phase V3,
    a `DeploymentSuggestion`) lets R8, M4, M5, M7 and M9 judge what the model proposed to deploy."""
    report = ComplianceReport()
    ceilings = ceilings or {}
    updates: dict = {}

    def check(rule: str, ok: bool, detail: str, *, fix: Optional[Tuple[str, object, str]] = None, warn_only: bool = False) -> None:
        if ok:
            report.checks.append(CheckResult(rule, "PASS", detail))
            return
        if fix is not None and autofix:
            key, value, note = fix
            updates[key] = value
            report.fixes.append(note)
            report.checks.append(CheckResult(rule, "PASS", f"{detail} - fixed: {note}", fixed=True))
            return
        report.checks.append(CheckResult(rule, "WARN" if warn_only else "FAIL", detail))

    # --- the draft's own rules --------------------------------------------------------------
    check("R1", config.stop_loss_atr_mult > 0,
          f"stop defined before entry: {config.stop_loss_atr_mult:g} x ATR({config.atr_period}) from the entry price")
    check("M2", config.stop_loss_atr_mult >= MIN_STOP_ATR_MULT,
          f"stop distance {config.stop_loss_atr_mult:g} x ATR is inside normal noise (minimum {MIN_STOP_ATR_MULT:g} x ATR)"
          if config.stop_loss_atr_mult < MIN_STOP_ATR_MULT else f"stop distance {config.stop_loss_atr_mult:g} x ATR is outside normal noise (>= {MIN_STOP_ATR_MULT:g} x ATR)",
          fix=("stop_loss_atr_mult", MIN_STOP_ATR_MULT, f"stop_loss_atr_mult {config.stop_loss_atr_mult:g} -> {MIN_STOP_ATR_MULT:g} (M2)"))
    t1, t2 = float(config.target_rr[0]), float(config.target_rr[1])
    lo, hi = sorted((t1, t2))
    first_ok = lo >= config.min_rr
    ordered_ok = t1 <= t2
    fixed_targets = (max(lo, config.min_rr), max(hi, config.min_rr))
    check("M1", first_ok and ordered_ok,
          f"targets {t1:g}R / {t2:g}R are ordered and at or above the minimum R:R {config.min_rr:g}"
          if first_ok and ordered_ok else f"targets {t1:g}R / {t2:g}R must be ordered and at or above the minimum R:R {config.min_rr:g}",
          fix=("target_rr", fixed_targets, f"target_rr ({t1:g}, {t2:g}) -> ({fixed_targets[0]:g}, {fixed_targets[1]:g}) (M1)"))
    check("RR", config.min_rr >= 1.0, f"minimum R:R {config.min_rr:g}" + ("" if config.min_rr >= 1.0 else " is below 1:1 - the strategy accepts trades that risk more than they target"),
          warn_only=True)
    check("M3", True, "entries are all-or-nothing at the sized quantity; there is no add-to-position path, so nothing is ever added to a loser (also R6)")

    # --- rules the engine enforces on every entry -------------------------------------------
    ceiling = ceilings.get("risk_per_trade_pct")
    risk_pct = float(cfg.risk_per_trade_pct)
    check("R2", ceiling is None or risk_pct <= float(ceiling),
          f"risk per trade {risk_pct:g}% of capital" + (f" (platform ceiling {float(ceiling):g}%)" if ceiling is not None else "")
          if ceiling is None or risk_pct <= float(ceiling) else f"risk per trade {risk_pct:g}% exceeds the platform ceiling {float(ceiling):g}% - the engine clamps it at runtime",
          warn_only=True)
    check("R3", True, "size = capital x risk% / (stop distance per unit), floored to whole lots - the engine derives it, the strategy never chooses it")
    check("R9", True, "structures are sized on max loss (or the loss the stop accepts), never on premium collected or margin blocked")
    check("R4", True, f"open risk at the stops + this trade <= {float(cfg.max_portfolio_risk_pct):g}% of capital; indices count as one correlated bucket")
    check("R5", True, f"daily loss limit {float(cfg.max_daily_loss_pct):g}% of capital engages the kill switch - no new trades that day")
    check("R6", True, "never add to a losing position - by construction (one open position per deployment)")
    check("R7", True, "a stop only moves the favourable way (exit rules only tighten; break-even and trailing never widen)")
    if suggestion is not None and suggestion.instrument_kind.value == "OPTION":
        if suggestion.undefined_risk:
            report.checks.append(CheckResult("R8", "WARN", f"proposed {suggestion.option_strategy.value.lower().replace('_', ' ')} has undefined risk on one side: "
                                             "the engine sizes it off the loss the stop accepts and the structure card says UNDEFINED - accept that explicitly or pick a defined-risk structure"))
        elif suggestion.option_strategy.value == "SINGLE":
            report.checks.append(CheckResult("R8", "PASS", "proposed a bought option: the premium paid is the maximum loss"))
        else:
            report.checks.append(CheckResult("R8", "PASS", f"proposed {suggestion.option_strategy.value.lower().replace('_', ' ')}: defined risk, sized on max loss"))
    else:
        report.checks.append(CheckResult("R8", "N/A", "directional rule set on the underlying; option structures are chosen on the deployment, where undefined-risk ones are flagged and sized off the stop"))
    check("R10", True, f"no re-entry in an underlying for {int(cfg.stop_cooldown_minutes)} min after a stop-out")
    check("M8", True, f"market-events calendar: BLOCK days refuse entries, SIZE_CUT days cut risk per trade (default {float(cfg.event_size_cut_pct):g}%)")
    check("P2", True, f"drawdown ladder: risk per trade halved {float(cfg.dd_level_1_pct):g}% below the equity peak")
    check("P3", True, f"drawdown ladder: new entries paused {float(cfg.dd_level_2_pct):g}% below the equity peak")
    check("P4", True, "the size multiplier never exceeds 1 - a winning streak never raises size on its own")
    exits = getattr(suggestion, "exit_rules", None)
    if exits is not None and exits.break_even_at_r:
        report.checks.append(CheckResult("M4", "PASS", f"proposed break-even at {exits.break_even_at_r:g}R: once ahead by that much the trade cannot turn into a loss"))
    else:
        report.checks.append(CheckResult("M4", "N/A", "break-even at R is an exit rule set on the deployment (Autopilot form), default off"))
    if exits is not None and exits.trailing_stop_pct:
        report.checks.append(CheckResult("M5", "PASS", f"proposed trailing stop {exits.trailing_stop_pct:g}% to let winners run"))
    else:
        report.checks.append(CheckResult("M5", "N/A", "trailing stop is an exit rule set on the deployment, default off"))
    if suggestion is not None and suggestion.is_credit_structure:
        target, stop = suggestion.target_credit_pct, suggestion.stop_credit_pct
        target_ok = target is None or 30.0 <= float(target) <= 80.0
        stop_ok = stop is None or float(stop) <= 200.0
        detail = (f"credit structure: profit capture {target if target is not None else 'default 50'}% of the credit, "
                  f"stop at {stop if stop is not None else 'default 100'}% loss of the credit (short premium x{1 + (float(stop) if stop is not None else 100.0) / 100.0:g})")
        report.checks.append(CheckResult("M7", "PASS" if target_ok and stop_ok else "WARN",
                                         detail if target_ok and stop_ok else detail + " - the spec wants 50-70% capture and a stop at 2-3x the premium"))
    if suggestion is not None and suggestion.regime_filter:
        report.checks.append(CheckResult("M9", "PASS", f"proposed regime filter: enter only in {'/'.join(suggestion.regime_filter)}"))
    else:
        report.checks.append(CheckResult("M9", "N/A", "regime filter is set on the deployment; the regime engine reads the market, the deployment decides"))

    # --- the statement the human must accept -------------------------------------------------
    capital = float(cfg.capital)
    risk_amount = capital * risk_pct / 100.0
    daily_amount = capital * float(cfg.max_daily_loss_pct) / 100.0
    gap_amount = risk_amount * GAP_MULTIPLE
    report.user_must_accept = {
        "max_loss_per_trade_text": (f"Every trade this strategy takes can lose up to {_money(risk_amount)} {currency} ({risk_pct:g}% of the {_money(capital)} capital) "
                                    f"at its stop, which sits {updates.get('stop_loss_atr_mult', config.stop_loss_atr_mult):g} x ATR({config.atr_period}) from the entry. Accept this before going live."),
        "worst_case_text": (f"Worst case: a gap or a 3-5 sigma move through the stop costs about {GAP_MULTIPLE:g} x the planned loss, ~{_money(gap_amount)} {currency} "
                            f"({gap_amount / capital * 100.0:.1f}% of capital). The daily loss limit stops trading at {_money(daily_amount)} ({float(cfg.max_daily_loss_pct):g}%); "
                            f"the drawdown ladder halves size {float(cfg.dd_level_1_pct):g}% below the equity peak and pauses entries at {float(cfg.dd_level_2_pct):g}%."),
    }

    # --- backtest evidence -------------------------------------------------------------------
    if backtest:
        report.evidence = assess_evidence(backtest, capital)
        for line in report.evidence["warnings"]:
            report.checks.append(CheckResult("E1", "WARN", line))
        if not report.evidence["warnings"]:
            report.checks.append(CheckResult("E1", "PASS", report.evidence["summary"]))

    fixed_config = config.model_copy(update=updates) if updates else config
    return fixed_config, report


def assess_evidence(metrics: dict, capital: float) -> dict:
    """Plain words about a backtest's strength; never a promise."""
    trades = int(metrics.get("total_trades") or 0)
    pf = metrics.get("profit_factor")
    net = float(metrics.get("net_pnl") or 0.0)
    dd = float(metrics.get("max_drawdown") or 0.0)
    win = float(metrics.get("win_rate") or 0.0)
    warnings: List[str] = []
    if trades < WEAK_EVIDENCE_MIN_TRADES:
        warnings.append(f"only {trades} backtest trade(s) - too few to trust the statistics (need {WEAK_EVIDENCE_MIN_TRADES}+)")
    if net <= 0:
        warnings.append(f"the backtest lost money (net {net:,.0f}) - this rule set has shown no edge on this data")
    if pf is not None and float(pf) < WEAK_EVIDENCE_MIN_PROFIT_FACTOR and net > 0:
        warnings.append(f"profit factor {float(pf):.2f} is thin (below {WEAK_EVIDENCE_MIN_PROFIT_FACTOR}); costs and slippage can erase it")
    if capital > 0 and abs(dd) / capital * 100.0 > WEAK_EVIDENCE_MAX_DRAWDOWN_PCT:
        warnings.append(f"max drawdown {abs(dd):,.0f} is {abs(dd) / capital * 100.0:.1f}% of capital - deeper than the {WEAK_EVIDENCE_MAX_DRAWDOWN_PCT:g}% comfort line")
    summary = (f"{trades} trades, win rate {win * 100 if win <= 1 else win:.0f}%, net {net:,.0f}, profit factor "
               f"{float(pf):.2f}" if pf is not None else f"{trades} trades, win rate {win * 100 if win <= 1 else win:.0f}%, net {net:,.0f}")
    return {"total_trades": trades, "win_rate": win, "net_pnl": net, "profit_factor": pf, "max_drawdown": dd,
            "summary": summary, "warnings": warnings, "strength": "weak" if warnings else "adequate"}
