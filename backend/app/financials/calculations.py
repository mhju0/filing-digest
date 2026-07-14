"""Compatibility-guarded calculations over canonical Financial Facts."""

import datetime
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from app.filings.model import FinancialFact, ReportingPeriod
from app.financials.vocabulary import DerivedMetric, PeriodKind, ReportedMetric

MAX_COMPARABLE_DURATION_DIFFERENCE_DAYS = 7


@dataclass(frozen=True)
class DerivedFinancialFact:
    """A calculated value with its reported inputs retained as evidence."""

    metric: DerivedMetric
    value: Decimal
    unit: str
    period: ReportingPeriod
    inputs: tuple[FinancialFact, ...]


def derive_operating_margin(
    revenue: FinancialFact, operating_income: FinancialFact
) -> DerivedFinancialFact:
    """Calculate operating margin while preserving both reported inputs."""
    if revenue.metric is not ReportedMetric.revenue:
        raise ValueError("revenue input must use the revenue Reported Metric")
    if operating_income.metric is not ReportedMetric.operating_income:
        raise ValueError(
            "operating_income input must use the operating_income Reported Metric"
        )
    if revenue.period != operating_income.period:
        raise ValueError("operating margin inputs must use the same Reporting Period")
    if (
        revenue.currency is None
        or operating_income.currency is None
        or revenue.currency != operating_income.currency
    ):
        raise ValueError("operating margin inputs require the same known currency")
    if revenue.unit != operating_income.unit:
        raise ValueError("operating margin inputs must use the same unit")
    if revenue.scale != operating_income.scale:
        raise ValueError("operating margin inputs must use the same scale")
    if revenue.value == 0:
        raise ValueError("operating margin requires nonzero revenue")
    return DerivedFinancialFact(
        metric=DerivedMetric.operating_margin,
        value=operating_income.value / revenue.value * 100,
        unit="PERCENT",
        period=revenue.period,
        inputs=(revenue, operating_income),
    )


class FinancialFactRow(Protocol):
    """Read shape needed to compare two persisted Financial Facts."""

    period: str
    metric: str
    value: Decimal
    fiscal_year: int
    period_kind: str
    period_start: datetime.date | None
    period_end: datetime.date | None
    fiscal_quarter: int | None
    currency: str | None
    unit: str
    scale: int


def _periods_are_compatible(
    current: FinancialFactRow, previous: FinancialFactRow
) -> bool:
    try:
        current_kind = PeriodKind(current.period_kind)
        previous_kind = PeriodKind(previous.period_kind)
    except ValueError:
        return False

    if current_kind is not previous_kind:
        return False
    if previous.fiscal_year != current.fiscal_year - 1:
        return False
    if current.fiscal_quarter != previous.fiscal_quarter:
        return False
    if current.currency != previous.currency:
        return False
    if current.unit != previous.unit:
        return False
    if current.scale != previous.scale:
        return False

    current_dates_known = current.period_end is not None
    previous_dates_known = previous.period_end is not None

    if current_kind is PeriodKind.instant:
        if current.period_start is not None or previous.period_start is not None:
            return False
        return current_dates_known == previous_dates_known

    current_range_known = current.period_start is not None and current_dates_known
    previous_range_known = previous.period_start is not None and previous_dates_known
    current_range_partial = (current.period_start is None) != (
        current.period_end is None
    )
    previous_range_partial = (previous.period_start is None) != (
        previous.period_end is None
    )
    if current_range_partial or previous_range_partial:
        return False
    if current_range_known != previous_range_known:
        return False
    if not current_range_known:
        # Older DART rows honestly lack exact source dates. Matching unknown
        # ranges remain comparable when every other reporting dimension agrees.
        return True

    assert current.period_start is not None and current.period_end is not None
    assert previous.period_start is not None and previous.period_end is not None
    current_days = (current.period_end - current.period_start).days
    previous_days = (previous.period_end - previous.period_start).days
    if current_days < 0 or previous_days < 0:
        return False
    return (
        abs(current_days - previous_days)
        <= MAX_COMPARABLE_DURATION_DIFFERENCE_DAYS
    )


def compute_yoy_deltas(
    rows: Sequence[FinancialFactRow],
    target_period: str,
    previous_period: str | None,
) -> dict[str, float | None]:
    """Return YoY percentages only for compatible Financial Fact pairs.

    A missing or non-positive prior value is not comparable. Neither are facts
    whose reporting kind, known duration, fiscal quarter, currency, unit, or
    scale differs. Legacy DART duration rows remain comparable when both facts
    honestly omit exact start and end dates.
    """
    current_by_metric = {
        row.metric: row for row in rows if row.period == target_period
    }
    if previous_period is None:
        return dict.fromkeys(current_by_metric)

    previous_by_metric = {
        row.metric: row for row in rows if row.period == previous_period
    }

    deltas: dict[str, float | None] = {}
    for metric, current in current_by_metric.items():
        previous = previous_by_metric.get(metric)
        if (
            previous is None
            or previous.value <= 0
            or not _periods_are_compatible(current, previous)
        ):
            deltas[metric] = None
            continue
        deltas[metric] = float(
            (current.value - previous.value) / previous.value * 100
        )
    return deltas
