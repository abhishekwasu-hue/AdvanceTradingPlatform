"""Relative valuation (P/E, P/B, EV/EBITDA, PEG, historical/peer comparison) and a real DCF
calculator with bull/base/bear sensitivity. Every multiple here is computed from a supplied
market price and the supplied financial statement - nothing is looked up automatically (no live
market-data/analyst-consensus feed is wired in for this module - see app/fundamentals/providers/).
"""
from typing import List, Optional

from app.core.enums import ValuationLabel
from app.fundamentals.models import (
    DCFAssumptions,
    DCFResult,
    DCFScenarioResult,
    FinancialPeriod,
    ValuationResult,
)


class ValuationEngine:
    def analyze(
        self,
        market_price: float,
        shares_outstanding: float,
        latest: FinancialPeriod,
        book_value_per_share: Optional[float] = None,
        net_debt: Optional[float] = None,
        dividend_per_share: Optional[float] = None,
        forward_eps: Optional[float] = None,
        eps_cagr_pct: Optional[float] = None,
        historical_pe_avg_5y: Optional[float] = None,
        peer_pe_avg: Optional[float] = None,
    ) -> ValuationResult:
        market_cap = market_price * shares_outstanding
        eps = latest.eps if latest.eps is not None else (latest.pat / shares_outstanding if shares_outstanding else None)

        pe = (market_price / eps) if eps and eps > 0 else None
        forward_pe = (market_price / forward_eps) if forward_eps and forward_eps > 0 else None
        peg = (pe / eps_cagr_pct) if (pe is not None and eps_cagr_pct and eps_cagr_pct > 0) else None
        pb = (market_price / book_value_per_share) if book_value_per_share else None
        ev = market_cap + (net_debt or 0.0)
        ev_ebitda = (ev / latest.ebitda) if latest.ebitda else None
        ev_sales = (ev / latest.revenue) if latest.revenue else None
        price_to_sales = (market_cap / latest.revenue) if latest.revenue else None
        dividend_yield = (dividend_per_share / market_price * 100.0) if dividend_per_share else None
        fcf_yield = (latest.fcf / market_cap * 100.0) if (latest.fcf is not None and market_cap) else None

        pe_vs_5y = ((pe - historical_pe_avg_5y) / historical_pe_avg_5y * 100.0) if (pe is not None and historical_pe_avg_5y) else None
        pe_vs_peers = ((pe - peer_pe_avg) / peer_pe_avg * 100.0) if (pe is not None and peer_pe_avg) else None

        notes: List[str] = []
        # Weigh whichever comparisons are available; default to a neutral read if none supplied.
        deviations = [d for d in (pe_vs_5y, pe_vs_peers) if d is not None]
        if deviations:
            avg_dev = sum(deviations) / len(deviations)
            if avg_dev <= -25:
                classification = ValuationLabel.DEEPLY_UNDERVALUED
            elif avg_dev <= -10:
                classification = ValuationLabel.UNDERVALUED
            elif avg_dev <= 15:
                classification = ValuationLabel.FAIRLY_VALUED
            elif avg_dev <= 40:
                classification = ValuationLabel.EXPENSIVE
            else:
                classification = ValuationLabel.EXTREMELY_EXPENSIVE
            if pe_vs_5y is not None:
                notes.append(f"P/E is {pe_vs_5y:+.0f}% vs its own 5Y average.")
            if pe_vs_peers is not None:
                notes.append(f"P/E is {pe_vs_peers:+.0f}% vs peer average.")
        else:
            classification = ValuationLabel.FAIRLY_VALUED
            notes.append("No historical or peer P/E supplied - classification defaults to Fairly Valued pending a comparison point.")

        return ValuationResult(
            pe=pe, forward_pe=forward_pe, peg=peg, pb=pb, ev_ebitda=ev_ebitda, ev_sales=ev_sales,
            price_to_sales=price_to_sales, dividend_yield_pct=dividend_yield, fcf_yield_pct=fcf_yield,
            pe_vs_5y_avg_pct=pe_vs_5y, pe_vs_peers_pct=pe_vs_peers, classification=classification, notes=notes,
        )


class DCFEngine:
    """A straightforward FCFF DCF: project revenue -> EBITDA -> unlevered FCF for N years using
    supplied growth/margin assumptions, discount at the supplied WACC, add a Gordon-growth
    terminal value, subtract net debt for equity value. Every input is supplied by the caller
    (spec section 15) - this is real arithmetic, not a fabricated number.
    """

    def _run_scenario(self, name: str, assumptions: DCFAssumptions, growth_delta_pct: float, margin_delta_pct: float) -> DCFScenarioResult:
        revenue = assumptions.base_revenue
        wacc = assumptions.wacc_pct / 100.0
        margin = (assumptions.ebitda_margin_pct + margin_delta_pct) / 100.0
        tax_rate = assumptions.tax_rate_pct / 100.0
        capex_pct = assumptions.capex_pct_of_revenue / 100.0
        wc_pct = assumptions.working_capital_pct_of_revenue / 100.0

        projected_fcf: List[float] = []
        pv_fcf = 0.0
        prior_revenue = assumptions.base_revenue
        for i, growth in enumerate(assumptions.revenue_growth_pct, start=1):
            revenue = revenue * (1 + (growth + growth_delta_pct) / 100.0)
            ebitda = revenue * margin
            nopat = ebitda * (1 - tax_rate)
            capex = revenue * capex_pct
            incremental_working_capital = (revenue - prior_revenue) * wc_pct
            fcf = nopat - capex - incremental_working_capital
            projected_fcf.append(fcf)
            pv_fcf += fcf / ((1 + wacc) ** i)
            prior_revenue = revenue

        terminal_growth = assumptions.terminal_growth_pct / 100.0
        terminal_fcf = projected_fcf[-1] * (1 + terminal_growth)
        terminal_value = terminal_fcf / (wacc - terminal_growth) if wacc > terminal_growth else 0.0
        pv_terminal = terminal_value / ((1 + wacc) ** len(projected_fcf))

        enterprise_value = pv_fcf + pv_terminal
        equity_value = enterprise_value - assumptions.net_debt
        per_share = equity_value / assumptions.shares_outstanding if assumptions.shares_outstanding else 0.0

        return DCFScenarioResult(
            scenario=name, intrinsic_value_per_share=round(per_share, 2), projected_fcf=[round(f, 2) for f in projected_fcf],
            terminal_value=round(terminal_value, 2), enterprise_value=round(enterprise_value, 2), equity_value=round(equity_value, 2),
        )

    def run(self, assumptions: DCFAssumptions, current_market_price: Optional[float] = None) -> DCFResult:
        base = self._run_scenario("Base", assumptions, growth_delta_pct=0.0, margin_delta_pct=0.0)
        bull = self._run_scenario("Bull", assumptions, growth_delta_pct=5.0, margin_delta_pct=2.0)
        bear = self._run_scenario("Bear", assumptions, growth_delta_pct=-5.0, margin_delta_pct=-2.0)

        upside = None
        margin_of_safety = None
        if current_market_price and current_market_price > 0:
            upside = (base.intrinsic_value_per_share - current_market_price) / current_market_price * 100.0
            margin_of_safety = (base.intrinsic_value_per_share - current_market_price) / base.intrinsic_value_per_share * 100.0

        return DCFResult(
            current_market_price=current_market_price, base=base, bull=bull, bear=bear,
            upside_pct=upside, margin_of_safety_pct=margin_of_safety,
        )
