"""Sector-specific fundamentals (spec's banking/IT/auto/pharma/oil & gas/cement sections) -
classifies KPI values someone has actually entered (NIM/CASA/GNPA for a bank, utilization/
attrition for an IT services firm, ...) against published-style thresholds. It never infers
which sector a company belongs to from free-text `sector`/`industry` fields - the caller passes
an explicit `sector_key` so a mismatch is the caller's mistake to fix, not a silent guess by
this engine.
"""
from dataclasses import dataclass
from typing import Dict, List

from app.core.enums import QualityLabel
from app.fundamentals.models import SectorMetric, SectorMetricResult, SectorSpecificResult


@dataclass(frozen=True)
class MetricSpec:
    label: str
    unit: str
    higher_is_better: bool
    strong: float
    good: float
    average: float


SECTOR_METRIC_SPECS: Dict[str, Dict[str, MetricSpec]] = {
    "BANKING": {
        "NIM_PCT": MetricSpec("Net Interest Margin", "%", True, 4.0, 3.2, 2.5),
        "CASA_RATIO_PCT": MetricSpec("CASA Ratio", "%", True, 45.0, 35.0, 25.0),
        "GNPA_PCT": MetricSpec("Gross NPA", "%", False, 1.5, 3.0, 5.0),
        "NNPA_PCT": MetricSpec("Net NPA", "%", False, 0.5, 1.0, 2.0),
        "PCR_PCT": MetricSpec("Provision Coverage Ratio", "%", True, 75.0, 65.0, 50.0),
        "CRAR_PCT": MetricSpec("Capital Adequacy Ratio (CRAR)", "%", True, 16.0, 13.0, 11.0),
    },
    "IT_SERVICES": {
        "UTILIZATION_PCT": MetricSpec("Utilization Rate", "%", True, 85.0, 78.0, 70.0),
        "ATTRITION_PCT": MetricSpec("Attrition Rate", "%", False, 10.0, 15.0, 20.0),
        "TCV_GROWTH_PCT": MetricSpec("TCV (Deal Wins) Growth", "%", True, 15.0, 8.0, 0.0),
        "REVENUE_PER_EMPLOYEE_USD": MetricSpec("Revenue per Employee (USD)", "$", True, 60000.0, 45000.0, 35000.0),
    },
    "AUTO": {
        "VOLUME_GROWTH_PCT": MetricSpec("Volume Growth", "%", True, 12.0, 6.0, 0.0),
        "INVENTORY_DAYS": MetricSpec("Channel Inventory Days", "days", False, 20.0, 30.0, 45.0),
        "EXPORT_MIX_PCT": MetricSpec("Export Mix", "%", True, 25.0, 15.0, 5.0),
    },
    "PHARMA": {
        "US_GENERICS_MIX_PCT": MetricSpec("US Generics Revenue Mix", "%", True, 40.0, 25.0, 10.0),
        "RND_PCT_OF_SALES": MetricSpec("R&D as % of Sales", "%", True, 8.0, 5.0, 3.0),
        "USFDA_OBSERVATIONS": MetricSpec("USFDA Inspection Observations", "count", False, 0.0, 2.0, 5.0),
    },
    "OIL_GAS": {
        "GRM_USD_PER_BBL": MetricSpec("Gross Refining Margin", "$/bbl", True, 10.0, 6.0, 3.0),
        "REFINING_UTILIZATION_PCT": MetricSpec("Refining Capacity Utilization", "%", True, 100.0, 90.0, 80.0),
    },
    "CEMENT": {
        "CAPACITY_UTILIZATION_PCT": MetricSpec("Capacity Utilization", "%", True, 85.0, 75.0, 65.0),
        "REALIZATION_PER_TONNE": MetricSpec("Realization per Tonne", "₹", True, 6000.0, 5000.0, 4200.0),
    },
}


def _classify(spec: MetricSpec, value: float) -> QualityLabel:
    if spec.higher_is_better:
        if value >= spec.strong:
            return QualityLabel.STRONG
        if value >= spec.good:
            return QualityLabel.GOOD
        if value >= spec.average:
            return QualityLabel.AVERAGE
        return QualityLabel.WEAK
    if value <= spec.strong:
        return QualityLabel.STRONG
    if value <= spec.good:
        return QualityLabel.GOOD
    if value <= spec.average:
        return QualityLabel.AVERAGE
    return QualityLabel.WEAK


class SectorSpecificEngine:
    def analyze(self, sector_key: str, metrics: List[SectorMetric]) -> SectorSpecificResult:
        key = sector_key.upper()
        specs = SECTOR_METRIC_SPECS.get(key)
        if not specs:
            raise ValueError(f"Unknown sector_key '{sector_key}' - supported: {', '.join(SECTOR_METRIC_SPECS)}")
        if not metrics:
            raise ValueError("At least one sector metric is required")

        results: List[SectorMetricResult] = []
        for m in metrics:
            spec = specs.get(m.metric_code.upper())
            if spec is None:
                continue  # not a known metric for this sector - skip rather than guess a classification
            classification = _classify(spec, m.value)
            results.append(SectorMetricResult(
                metric_code=m.metric_code.upper(), label=spec.label, value=m.value, unit=spec.unit,
                classification=classification, note=f"{spec.label}: {m.value}{spec.unit} ({classification.value})",
            ))

        if not results:
            raise ValueError(f"None of the supplied metric codes are recognized for sector '{key}'")

        strong_count = sum(1 for r in results if r.classification == QualityLabel.STRONG)
        weak_count = sum(1 for r in results if r.classification == QualityLabel.WEAK)
        overall_note = f"{strong_count}/{len(results)} metrics strong, {weak_count}/{len(results)} weak."
        return SectorSpecificResult(sector=key, metrics=results, overall_note=overall_note)
