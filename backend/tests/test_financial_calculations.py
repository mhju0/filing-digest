"""Behavior tests for Derived Metrics over canonical Financial Facts."""

import datetime
from dataclasses import replace
from decimal import Decimal

import pytest

from app.filings.model import FinancialFact, ReportingPeriod
from app.financials.calculations import derive_operating_margin
from app.financials.vocabulary import DerivedMetric, PeriodKind, ReportedMetric


def test_operating_margin_keeps_its_reported_inputs_traceable() -> None:
    period = ReportingPeriod(
        "2025-annual",
        PeriodKind.duration,
        datetime.date(2025, 1, 1),
        datetime.date(2025, 12, 31),
    )
    revenue = FinancialFact(
        ReportedMetric.revenue,
        period,
        Decimal("400"),
        "KRW",
        "KRW",
    )
    operating_income = FinancialFact(
        ReportedMetric.operating_income,
        period,
        Decimal("50"),
        "KRW",
        "KRW",
    )

    derived = derive_operating_margin(revenue, operating_income)

    assert derived.metric is DerivedMetric.operating_margin
    assert derived.value == Decimal("12.5")
    assert derived.unit == "PERCENT"
    assert derived.inputs == (revenue, operating_income)


def _compatible_inputs() -> tuple[FinancialFact, FinancialFact]:
    period = ReportingPeriod("2025-annual", PeriodKind.duration)
    return (
        FinancialFact(ReportedMetric.revenue, period, Decimal("400"), "KRW", "KRW"),
        FinancialFact(
            ReportedMetric.operating_income,
            period,
            Decimal("50"),
            "KRW",
            "KRW",
        ),
    )


def test_operating_margin_rejects_wrong_reported_metric_roles() -> None:
    revenue, operating_income = _compatible_inputs()

    with pytest.raises(ValueError, match="revenue input"):
        derive_operating_margin(operating_income, operating_income)
    with pytest.raises(ValueError, match="operating_income input"):
        derive_operating_margin(revenue, revenue)


@pytest.mark.parametrize(
    ("income_changes", "message"),
    [
        (
            {"period": ReportingPeriod("2024-annual", PeriodKind.duration)},
            "Reporting Period",
        ),
        ({"currency": "USD"}, "currency"),
        ({"currency": None}, "currency"),
        ({"unit": "KRW_PER_SHARE"}, "unit"),
        ({"scale": 1_000}, "scale"),
    ],
    ids=["period", "currency", "missing-currency", "unit", "scale"],
)
def test_operating_margin_rejects_incompatible_inputs(
    income_changes: dict[str, object], message: str
) -> None:
    revenue, operating_income = _compatible_inputs()

    with pytest.raises(ValueError, match=message):
        derive_operating_margin(revenue, replace(operating_income, **income_changes))


def test_operating_margin_rejects_missing_currency_on_both_inputs() -> None:
    revenue, operating_income = _compatible_inputs()

    with pytest.raises(ValueError, match="currency"):
        derive_operating_margin(
            replace(revenue, currency=None),
            replace(operating_income, currency=None),
        )


def test_operating_margin_rejects_zero_revenue() -> None:
    revenue, operating_income = _compatible_inputs()

    with pytest.raises(ValueError, match="nonzero"):
        derive_operating_margin(replace(revenue, value=Decimal("0")), operating_income)
