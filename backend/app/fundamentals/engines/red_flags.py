"""Aggregates red flags (spec section 33) across everything the other engines already computed,
plus a few checks of its own (promoter pledge trend, shareholding changes, corporate-action
types that are inherently red-flag-worthy). Every flag traces back to a real supplied number or
a cited qualitative note - nothing here is guessed.
"""
from typing import List, Optional

from app.core.enums import RiskLevel
from app.fundamentals.models import (
    BalanceSheetAnalysis,
    CashFlowAnalysis,
    CorporateAction,
    EarningsQualityResult,
    RedFlag,
    ShareholdingSnapshot,
)

_RED_FLAG_ACTION_TYPES = {
    "AUDITOR_CHANGE": ("Auditor change announced - always worth understanding why.", RiskLevel.MEDIUM),
    "MANAGEMENT_CHANGE": ("Key management change (CEO/CFO) announced.", RiskLevel.MEDIUM),
    "REGULATORY_ACTION": ("Regulatory action/investigation disclosed.", RiskLevel.HIGH),
    "EQUITY_DILUTION": ("Equity dilution announced - existing shareholders' stake shrinks.", RiskLevel.MEDIUM),
}


class RedFlagEngine:
    def analyze(
        self,
        earnings_quality: Optional[EarningsQualityResult] = None,
        balance_sheet: Optional[BalanceSheetAnalysis] = None,
        cash_flow: Optional[CashFlowAnalysis] = None,
        shareholding_history: Optional[List[ShareholdingSnapshot]] = None,
        corporate_actions: Optional[List[CorporateAction]] = None,
    ) -> List[RedFlag]:
        flags: List[RedFlag] = []

        if earnings_quality:
            for warning in earnings_quality.warnings:
                flags.append(RedFlag(code="EARNINGS_QUALITY", severity=RiskLevel.MEDIUM, description=warning))

        if balance_sheet:
            if balance_sheet.debt_risk in (RiskLevel.HIGH, RiskLevel.EXTREME):
                for note in balance_sheet.notes:
                    flags.append(RedFlag(code="DEBT_RISK", severity=balance_sheet.debt_risk, description=note))
            if balance_sheet.liquidity_risk in (RiskLevel.HIGH, RiskLevel.EXTREME):
                flags.append(RedFlag(
                    code="LIQUIDITY_RISK", severity=balance_sheet.liquidity_risk,
                    description=f"Current ratio {balance_sheet.current_ratio:.2f} indicates liquidity strain."
                    if balance_sheet.current_ratio else "Liquidity risk elevated.",
                ))

        if cash_flow and cash_flow.warning:
            flags.append(RedFlag(code="CASH_FLOW", severity=RiskLevel.MEDIUM, description=cash_flow.warning))

        if shareholding_history and len(shareholding_history) >= 2:
            ordered = sorted(shareholding_history, key=lambda s: s.as_of_date)
            latest, prior = ordered[-1], ordered[-2]
            pledge_delta = latest.promoter_pledge_pct - prior.promoter_pledge_pct
            if pledge_delta > 2:
                flags.append(RedFlag(
                    code="PROMOTER_PLEDGE_INCREASE", severity=RiskLevel.HIGH,
                    description=f"Promoter pledge rose from {prior.promoter_pledge_pct:.1f}% to {latest.promoter_pledge_pct:.1f}%.",
                ))
            promoter_delta = latest.promoter_pct - prior.promoter_pct
            if promoter_delta < -2:
                flags.append(RedFlag(
                    code="PROMOTER_HOLDING_DECREASE", severity=RiskLevel.MEDIUM,
                    description=f"Promoter holding fell from {prior.promoter_pct:.1f}% to {latest.promoter_pct:.1f}%.",
                ))

        if corporate_actions:
            for action in corporate_actions:
                mapping = _RED_FLAG_ACTION_TYPES.get(action.action_type)
                if mapping:
                    description, severity = mapping
                    flags.append(RedFlag(code=f"ACTION_{action.action_type}", severity=severity, description=f"{description} ({action.headline})"))

        return flags
