"""Contract tests for backend-owned digest metric eligibility."""

import pytest
from pydantic import ValidationError

from app.digests.metrics import DIGEST_METRICS
from app.financials import DerivedMetric, ReportedMetric
from app.schemas import MetricCard


def test_digest_metrics_are_ordered_reported_metrics() -> None:
    assert DIGEST_METRICS == (
        ReportedMetric.revenue,
        ReportedMetric.operating_income,
        ReportedMetric.net_income,
        ReportedMetric.eps,
    )


def test_metric_card_keeps_reported_and_derived_metrics_distinct() -> None:
    card = MetricCard(
        key=DerivedMetric.operating_margin,
        unit="PERCENT",
        source="dart",
        filing_source_id="dart:20240312000736",
    )

    assert card.key is DerivedMetric.operating_margin
    assert set(card.model_dump()) == {
        "key",
        "value",
        "unit",
        "yoy_delta_pct",
        "source",
        "filing_source_id",
    }

    with pytest.raises(ValidationError):
        MetricCard(
            key="unknown_metric",
            unit="KRW",
            source="dart",
            filing_source_id="dart:20240312000736",
        )
