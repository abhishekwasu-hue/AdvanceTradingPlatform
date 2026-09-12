"""Revenue, profitability, earnings-quality, balance-sheet, cash-flow, and quarter-over-quarter
analysis - all pure computation over `FinancialPeriod` figures the caller supplied. No number
here is invented; every ratio is derived on demand from the raw statement fields so there is
exactly one place a figure could be wrong (the cited source it was entered from).
"""
from typing import List, Optional

from app.core.enums import Bias, PeriodType, QualityLabel, RiskLevel, TrendLabel
from app.fundamentals.models import (
    BalanceSheetAnalysis,
    CashFlowAnalysis,
    EarningsQualityResult,
    FinancialPeriod,
    GrowthAnalysis,
    ProfitabilityAnalysis,
    QuarterlyComparison,
    QuarterlyResultAnalysis,
)


def _sorted(periods: List[FinancialPeriod]) -> List[FinancialPeriod]:
    return sorted(periods, key=lambda p: p.period_end_date)


def _cagr(start: Optional[float], end: Optional[float], years: float) -> Optional[float]:
    if start is None or end is None or start <= 0 or years <= 0:
        return None
    return (((end / start) ** (1.0 / years)) - 1.0) * 100.0


class RevenueAnalysisEngine:
    """Growth analysis over a company's annual (or quarterly) history."""

    def analyze(self, periods: List[FinancialPeriod]) -> GrowthAnalysis:
        annual = _sorted([p for p in periods if p.period_type == PeriodType.ANNUAL])
        if not annual:
            annual = _sorted(periods)
        if not annual:
            raise ValueError("At least one financial period is required")

        latest = annual[-1]
        yoy = None
        if len(annual) >= 2:
            prior = annual[-2]
            if prior.revenue:
                yoy = (latest.revenue - prior.revenue) / prior.revenue * 100.0

        cagr_3y = _cagr(annual[-4].revenue, latest.revenue, 3) if len(annual) >= 4 else None
        cagr_5y = _cagr(annual[-6].revenue, latest.revenue, 5) if len(annual) >= 6 else None

        sustainable: Optional[bool] = None
        note = "Insufficient history to judge sustainability - need at least 3 annual periods."
        if len(annual) >= 3:
            recent_growth = [
                (annual[i].revenue - annual[i - 1].revenue) / annual[i - 1].revenue * 100.0
                for i in range(max(1, len(annual) - 3), len(annual))
                if annual[i - 1].revenue
            ]
            if recent_growth:
                avg = sum(recent_growth) / len(recent_growth)
                spread = max(recent_growth) - min(recent_growth)
                sustainable = avg > 0 and spread < max(abs(avg), 10.0) * 1.5
                note = (
                    f"Average growth over the last {len(recent_growth)} periods is {avg:.1f}% with a "
                    f"{spread:.1f}pt spread - "
                    + ("growth looks steady, not a one-off spike." if sustainable else
                       "growth is volatile/inconsistent, treat recent strength cautiously.")
                )

        return GrowthAnalysis(
            latest_period=latest.period_label, yoy_growth_pct=yoy,
            cagr_3y_pct=cagr_3y, cagr_5y_pct=cagr_5y, sustainable=sustainable, note=note,
        )


class ProfitabilityEngine:
    def analyze(self, periods: List[FinancialPeriod]) -> ProfitabilityAnalysis:
        ordered = _sorted(periods)
        if not ordered:
            raise ValueError("At least one financial period is required")
        latest = ordered[-1]
        shareholders_equity = latest.shareholders_equity
        total_assets = latest.total_assets
        capital_employed = (
            (total_assets - latest.current_liabilities)
            if (total_assets is not None and latest.current_liabilities is not None) else None
        )

        gross_margin = None
        if latest.cogs is not None and latest.revenue:
            gross_margin = (latest.revenue - latest.cogs) / latest.revenue * 100.0
        ebitda_margin = (latest.ebitda / latest.revenue * 100.0) if latest.revenue else None
        ebit_margin = (latest.ebit / latest.revenue * 100.0) if (latest.ebit is not None and latest.revenue) else None
        net_margin = (latest.pat / latest.revenue * 100.0) if latest.revenue else None

        roe = (latest.pat / shareholders_equity * 100.0) if shareholders_equity else None
        roce = (
            (latest.ebit / capital_employed * 100.0)
            if (latest.ebit is not None and capital_employed) else None
        )
        roa = (latest.pat / total_assets * 100.0) if total_assets else None

        trend = TrendLabel.STABLE
        note = "Not enough history to assess margin trend against revenue growth."
        if len(ordered) >= 3:
            margins = [
                (p.ebitda / p.revenue * 100.0) if p.revenue else None
                for p in ordered[-3:]
            ]
            if all(m is not None for m in margins):
                deltas = [margins[i] - margins[i - 1] for i in range(1, len(margins))]
                revenue_growing = ordered[-1].revenue > ordered[-3].revenue
                if all(d > 0.3 for d in deltas):
                    trend = TrendLabel.IMPROVING
                elif all(d < -0.3 for d in deltas):
                    trend = TrendLabel.DETERIORATING
                elif max(deltas) - min(deltas) > 5:
                    trend = TrendLabel.HIGHLY_VOLATILE
                else:
                    trend = TrendLabel.STABLE
                note = (
                    f"Revenue is {'growing' if revenue_growing else 'declining'} while EBITDA margin is "
                    f"{trend.value.lower()} ({margins[0]:.1f}% → {margins[-1]:.1f}%)."
                )

        return ProfitabilityAnalysis(
            gross_margin_pct=gross_margin, ebitda_margin_pct=ebitda_margin, ebit_margin_pct=ebit_margin,
            net_margin_pct=net_margin, roe_pct=roe, roce_pct=roce, roa_pct=roa,
            margin_trend=trend, revenue_vs_margin_note=note,
        )


class EarningsQualityEngine:
    """CFO vs PAT, other-income dependence, exceptional-item dependence - the three classic
    "the P&L looks good but the cash doesn't back it up" checks (spec section 5).
    """

    def analyze(self, latest: FinancialPeriod) -> EarningsQualityResult:
        warnings: List[str] = []
        cfo_to_pat = None
        if latest.cfo is not None and latest.pat:
            cfo_to_pat = latest.cfo / latest.pat
            if latest.pat > 0 and cfo_to_pat < 0.5:
                warnings.append(
                    f"Operating cash flow (₹{latest.cfo:.1f}) is well below PAT (₹{latest.pat:.1f}) "
                    f"- CFO/PAT of {cfo_to_pat:.2f} suggests profit isn't converting to cash."
                )
            elif latest.pat > 0 and cfo_to_pat < 0:
                warnings.append("PAT is positive but operating cash flow is negative - a serious earnings-quality red flag.")

        other_income_pct = (abs(latest.other_income) / abs(latest.pat) * 100.0) if latest.pat else None
        if other_income_pct is not None and other_income_pct > 25:
            warnings.append(f"Other income is {other_income_pct:.0f}% of PAT - core operations may be weaker than the headline number suggests.")

        exceptional_pct = (abs(latest.exceptional_items) / abs(latest.pat) * 100.0) if latest.pat else None
        if exceptional_pct is not None and exceptional_pct > 15:
            warnings.append(f"Exceptional items are {exceptional_pct:.0f}% of PAT - a meaningful part of earnings may be non-recurring.")

        if not warnings:
            label = QualityLabel.STRONG
        elif len(warnings) == 1:
            label = QualityLabel.AVERAGE
        else:
            label = QualityLabel.WEAK

        return EarningsQualityResult(
            cfo_to_pat_ratio=cfo_to_pat, other_income_pct_of_pat=other_income_pct,
            exceptional_items_pct_of_pat=exceptional_pct, label=label, warnings=warnings,
        )


class QuarterlyComparisonEngine:
    """QoQ/YoY comparison across revenue, EBITDA, margin, PAT, EPS, CFO, debt (spec section 6)."""

    def analyze(self, periods: List[FinancialPeriod]) -> QuarterlyResultAnalysis:
        quarters = _sorted([p for p in periods if p.period_type == PeriodType.QUARTER])
        if not quarters:
            raise ValueError("No quarterly periods supplied")
        current = quarters[-1]
        qoq = quarters[-2] if len(quarters) >= 2 else None
        yoy = quarters[-5] if len(quarters) >= 5 else None

        def _compare(metric: str, value_fn) -> QuarterlyComparison:
            current_v = value_fn(current)
            qoq_v = value_fn(qoq) if qoq else None
            yoy_v = value_fn(yoy) if yoy else None
            qoq_pct = ((current_v - qoq_v) / abs(qoq_v) * 100.0) if (qoq_v not in (None, 0) and current_v is not None) else None
            yoy_pct = ((current_v - yoy_v) / abs(yoy_v) * 100.0) if (yoy_v not in (None, 0) and current_v is not None) else None
            direction = "→"
            reference_pct = yoy_pct if yoy_pct is not None else qoq_pct
            if reference_pct is not None:
                direction = "↑" if reference_pct > 1 else ("↓" if reference_pct < -1 else "→")
            return QuarterlyComparison(
                metric=metric, current=current_v or 0.0, qoq_prior=qoq_v, yoy_prior=yoy_v,
                qoq_change_pct=qoq_pct, yoy_change_pct=yoy_pct, direction=direction,
            )

        comparisons = [
            _compare("Revenue", lambda p: p.revenue),
            _compare("EBITDA", lambda p: p.ebitda),
            _compare("EBITDA Margin %", lambda p: (p.ebitda / p.revenue * 100.0) if p.revenue else None),
            _compare("PAT", lambda p: p.pat),
            _compare("EPS", lambda p: p.eps),
            _compare("Operating Cash Flow", lambda p: p.cfo),
        ]

        up = sum(1 for c in comparisons if c.direction == "↑")
        down = sum(1 for c in comparisons if c.direction == "↓")
        overall = Bias.BULLISH if up > down else (Bias.BEARISH if down > up else Bias.NEUTRAL)

        return QuarterlyResultAnalysis(period_label=current.period_label, comparisons=comparisons, overall_quality=overall)


class BalanceSheetEngine:
    def analyze(self, latest: FinancialPeriod) -> BalanceSheetAnalysis:
        notes: List[str] = []
        shareholders_equity = latest.shareholders_equity
        debt_to_equity = (
            (latest.total_debt / shareholders_equity) if (latest.total_debt is not None and shareholders_equity) else None
        )
        net_debt_to_ebitda = (
            (latest.net_debt / latest.ebitda) if (latest.net_debt is not None and latest.ebitda) else None
        )
        interest_coverage = (
            (latest.ebit / latest.interest_expense)
            if (latest.ebit is not None and latest.interest_expense) else None
        )
        current_ratio = (
            (latest.current_assets / latest.current_liabilities)
            if (latest.current_assets is not None and latest.current_liabilities) else None
        )
        quick_ratio = None
        if latest.current_assets is not None and latest.current_liabilities and latest.inventory is not None:
            quick_ratio = (latest.current_assets - latest.inventory) / latest.current_liabilities

        debt_risk = RiskLevel.LOW
        if net_debt_to_ebitda is not None:
            if net_debt_to_ebitda > 4:
                debt_risk = RiskLevel.EXTREME
                notes.append(f"Net Debt/EBITDA of {net_debt_to_ebitda:.1f}x is very high.")
            elif net_debt_to_ebitda > 2.5:
                debt_risk = RiskLevel.HIGH
            elif net_debt_to_ebitda > 1.0:
                debt_risk = RiskLevel.MEDIUM
        if interest_coverage is not None and interest_coverage < 2:
            debt_risk = RiskLevel.HIGH if debt_risk in (RiskLevel.LOW, RiskLevel.MEDIUM) else debt_risk
            notes.append(f"Interest coverage of {interest_coverage:.1f}x is thin.")

        liquidity_risk = RiskLevel.LOW
        if current_ratio is not None:
            if current_ratio < 1.0:
                liquidity_risk = RiskLevel.HIGH
                notes.append(f"Current ratio of {current_ratio:.2f} is below 1 - short-term obligations exceed short-term assets.")
            elif current_ratio < 1.2:
                liquidity_risk = RiskLevel.MEDIUM

        return BalanceSheetAnalysis(
            debt_to_equity=debt_to_equity, net_debt_to_ebitda=net_debt_to_ebitda,
            interest_coverage=interest_coverage, current_ratio=current_ratio, quick_ratio=quick_ratio,
            debt_risk=debt_risk, liquidity_risk=liquidity_risk, notes=notes,
        )


class CashFlowEngine:
    def analyze(self, latest: FinancialPeriod, market_cap: Optional[float] = None) -> CashFlowAnalysis:
        fcf = latest.fcf
        cfo_to_pat = (latest.cfo / latest.pat) if (latest.cfo is not None and latest.pat) else None
        fcf_yield = (fcf / market_cap * 100.0) if (fcf is not None and market_cap) else None

        warning = None
        if latest.pat is not None and latest.pat > 0 and latest.cfo is not None and latest.cfo < latest.pat * 0.5:
            warning = "PAT is rising/positive but operating cash flow lags well behind it - classic earnings-quality warning (spec section 12)."

        return CashFlowAnalysis(cfo=latest.cfo, fcf=fcf, fcf_yield_pct=fcf_yield, cfo_to_pat_ratio=cfo_to_pat, warning=warning)
