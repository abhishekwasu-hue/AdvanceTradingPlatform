"""Cross-company peer/competitor ranking (spec section 20) - computed purely from financial
periods already persisted for companies in this platform. No live peer-universe feed exists, so
this only ever ranks companies someone has actually entered, never the full NSE/BSE sector.
"""
from typing import List, Optional, Tuple

from app.fundamentals.engines.statement_analysis import ProfitabilityEngine, RevenueAnalysisEngine
from app.fundamentals.models import FinancialPeriod, PeerMetrics


class PeerComparisonEngine:
    def compare(self, companies: List[Tuple[str, str, List[FinancialPeriod]]]) -> List[PeerMetrics]:
        """`companies` is a list of (symbol, name, periods) tuples. Companies with no periods
        are skipped rather than shown with fabricated zeros.
        """
        results: List[PeerMetrics] = []
        for symbol, name, periods in companies:
            if not periods:
                continue

            yoy_growth: Optional[float] = None
            try:
                yoy_growth = RevenueAnalysisEngine().analyze(periods).yoy_growth_pct
            except ValueError:
                pass

            profitability = ProfitabilityEngine().analyze(periods)
            latest = sorted(periods, key=lambda p: p.period_end_date)[-1]
            debt_to_equity = (
                (latest.total_debt / latest.shareholders_equity)
                if (latest.total_debt is not None and latest.shareholders_equity) else None
            )

            results.append(PeerMetrics(
                symbol=symbol, name=name, revenue_yoy_growth_pct=yoy_growth,
                ebitda_margin_pct=profitability.ebitda_margin_pct, roe_pct=profitability.roe_pct,
                roce_pct=profitability.roce_pct, debt_to_equity=debt_to_equity,
            ))

        # Rank by ROCE (capital efficiency) when available, else fall back to EBITDA margin -
        # a reasonable default ordering, not the only lens a user might want.
        results.sort(key=lambda r: (r.roce_pct is None, -(r.roce_pct or r.ebitda_margin_pct or 0)))
        return results
