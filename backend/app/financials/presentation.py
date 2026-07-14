"""Backend-owned presentation definitions for canonical financial metrics."""

from dataclasses import dataclass

from app.financials.vocabulary import ReportedMetric


@dataclass(frozen=True)
class MetricPresentation:
    """Bilingual labels for one canonical Reported Metric."""

    metric: ReportedMetric
    label_ko: str
    label_en: str


DIGEST_METRICS: tuple[MetricPresentation, ...] = (
    MetricPresentation(ReportedMetric.revenue, "매출액", "Revenue"),
    MetricPresentation(
        ReportedMetric.operating_income, "영업이익", "Operating Income"
    ),
    MetricPresentation(ReportedMetric.net_income, "당기순이익", "Net Income"),
    MetricPresentation(ReportedMetric.eps, "주당순이익", "EPS"),
)
