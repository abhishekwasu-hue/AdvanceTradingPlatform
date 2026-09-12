"""Bull/Base/Bear one-year-ahead projections (spec section 34) - simple, transparent arithmetic
off the latest reported period plus caller-supplied growth/margin deltas per scenario. Not a
multi-year DCF (see engines/valuation.py DCFEngine for that); this is the quick "what does next
year look like under three assumptions" view for the dashboard.
"""
from app.fundamentals.models import FinancialPeriod, ScenarioProjection


class ScenarioEngine:
    def project(
        self,
        latest: FinancialPeriod,
        bull_revenue_growth_pct: float = 15.0, bull_margin_delta_pct: float = 2.0,
        base_revenue_growth_pct: float = 8.0, base_margin_delta_pct: float = 0.0,
        bear_revenue_growth_pct: float = -5.0, bear_margin_delta_pct: float = -3.0,
        tax_rate_pct: float = 25.0,
    ):
        current_margin_pct = (latest.ebitda / latest.revenue * 100.0) if latest.revenue else 0.0
        shares = latest.shares_outstanding

        # The latest actual period implies how much of post-tax EBITDA survives D&A and interest
        # down to PAT; projections apply that same ratio forward rather than assuming a fresh
        # D&A/interest schedule that wasn't supplied.
        post_tax_ebitda = latest.ebitda * (1 - tax_rate_pct / 100.0)
        conversion_ratio = (latest.pat / post_tax_ebitda) if post_tax_ebitda else 0.7
        conversion_ratio = max(0.1, min(1.0, conversion_ratio))

        def _project(name: str, growth_pct: float, margin_delta_pct: float) -> ScenarioProjection:
            revenue = latest.revenue * (1 + growth_pct / 100.0)
            margin_pct = current_margin_pct + margin_delta_pct
            ebitda = revenue * margin_pct / 100.0
            pat = ebitda * (1 - tax_rate_pct / 100.0) * conversion_ratio
            eps = (pat / shares) if shares else None
            return ScenarioProjection(
                scenario=name, revenue=round(revenue, 2), ebitda=round(ebitda, 2), pat=round(pat, 2),
                eps=round(eps, 2) if eps is not None else None,
                assumptions_note=f"Revenue growth {growth_pct:+.1f}%, EBITDA margin {margin_pct:.1f}% (vs {current_margin_pct:.1f}% currently).",
            )

        return {
            "bull": _project("Bull", bull_revenue_growth_pct, bull_margin_delta_pct),
            "base": _project("Base", base_revenue_growth_pct, base_margin_delta_pct),
            "bear": _project("Bear", bear_revenue_growth_pct, bear_margin_delta_pct),
        }
