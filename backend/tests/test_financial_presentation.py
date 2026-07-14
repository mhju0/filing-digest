"""Contract tests for backend-owned digest metric presentation."""

import pytest
from pydantic import ValidationError

from app.financials import DerivedMetric, ReportedMetric
from app.financials.presentation import DIGEST_METRICS
from app.schemas import MetricCard


def test_digest_presentation_is_ordered_and_contains_only_reported_metrics() -> None:
    assert [item.metric for item in DIGEST_METRICS] == [
        ReportedMetric.revenue,
        ReportedMetric.operating_income,
        ReportedMetric.net_income,
        ReportedMetric.eps,
    ]
    assert [(item.label_ko, item.label_en) for item in DIGEST_METRICS] == [
        ("매출액", "Revenue"),
        ("영업이익", "Operating Income"),
        ("당기순이익", "Net Income"),
        ("주당순이익", "EPS"),
    ]


def test_metric_card_keeps_reported_and_derived_metrics_distinct() -> None:
    card = MetricCard(
        key=DerivedMetric.operating_margin,
        label_ko="영업이익률",
        label_en="Operating Margin",
        unit="PERCENT",
        source="dart",
        filing_source_id="dart:20240312000736",
    )

    assert card.key is DerivedMetric.operating_margin

    with pytest.raises(ValidationError):
        MetricCard(
            key="unknown_metric",
            label_ko="알 수 없음",
            label_en="Unknown",
            unit="KRW",
            source="dart",
            filing_source_id="dart:20240312000736",
        )
