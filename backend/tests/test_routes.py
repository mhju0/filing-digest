"""Offline unit tests for the pure /digest selection helpers in app.api.routes.

No DB, no HTTP: app.api.routes.select_target_period / select_latest_filing_id
are pure functions over already-fetched rows, so they are exercised directly
here rather than through the full FastAPI route (which needs a live DB session
-- covered instead by the live verification for this change).
"""

import datetime
import decimal
import uuid
from types import SimpleNamespace

from app.api.routes import (
    compute_yoy_deltas,
    select_latest_filing_id,
    select_previous_period,
    select_target_period,
)

_FID_2023 = uuid.UUID("11111111-1111-1111-1111-111111111111")
_FID_2024 = uuid.UUID("22222222-2222-2222-2222-222222222222")
_FID_2025 = uuid.UUID("33333333-3333-3333-3333-333333333333")


def _filing(fid: uuid.UUID, filed_at: datetime.date | None) -> SimpleNamespace:
    return SimpleNamespace(id=fid, filed_at=filed_at)


# -- select_target_period ------------------------------------------------------


def test_select_target_period_picks_lexicographic_max_year() -> None:
    # Apple's 3 ingested fiscal years, in arbitrary (non-sorted) input order.
    periods = ["2024-annual", "2023-annual", "2025-annual"]
    assert select_target_period(periods) == "2025-annual"


def test_select_target_period_single_period_is_a_noop() -> None:
    # Samsung: one DART filing, one period.
    assert select_target_period(["2023-annual"]) == "2023-annual"


def test_select_target_period_empty_yields_empty_string() -> None:
    assert select_target_period([]) == ""


def test_select_target_period_dart_and_sec_suffixes_still_sort_by_year() -> None:
    # Cross-source rows for the same company would still share the "YYYY-"
    # prefix, which dominates lexicographic comparison.
    periods = ["2022-annual", "2023-annual"]
    assert select_target_period(periods) == "2023-annual"


# -- select_latest_filing_id ----------------------------------------------------


def test_select_latest_filing_id_single_filing_returns_it_directly() -> None:
    filings = [_filing(_FID_2025, datetime.date(2025, 10, 31))]
    assert select_latest_filing_id({_FID_2025}, filings) == _FID_2025


def test_select_latest_filing_id_multiple_picks_latest_filed_at() -> None:
    # Unexpected case: the target period's own rows disagree on their filing.
    filings = [
        _filing(_FID_2023, datetime.date(2023, 11, 3)),
        _filing(_FID_2024, datetime.date(2024, 11, 1)),
    ]
    assert (
        select_latest_filing_id({_FID_2023, _FID_2024}, filings) == _FID_2024
    )


def test_select_latest_filing_id_falls_back_when_no_filed_at() -> None:
    # No parseable dates at all: falls back to the first filing rather than
    # raising -- deterministic given the same input list order.
    filings = [_filing(_FID_2023, None), _filing(_FID_2024, None)]
    assert select_latest_filing_id({_FID_2023, _FID_2024}, filings) == _FID_2023


# -- select_previous_period ------------------------------------------------------


def test_select_previous_period_picks_next_highest_year() -> None:
    periods = ["2024-annual", "2023-annual", "2025-annual"]
    assert select_previous_period(periods, "2025-annual") == "2024-annual"


def test_select_previous_period_none_when_only_target_period_exists() -> None:
    # Samsung: one DART filing, one period -- nothing to compare against.
    assert select_previous_period(["2023-annual"], "2023-annual") is None


# -- compute_yoy_deltas ----------------------------------------------------------


def _financial_row(period: str, metric: str, value: str) -> SimpleNamespace:
    return SimpleNamespace(period=period, metric=metric, value=decimal.Decimal(value))


def test_compute_yoy_deltas_normal_case_both_periods_present() -> None:
    rows = [
        _financial_row("2025-annual", "revenue", "416161000000"),
        _financial_row("2024-annual", "revenue", "391035000000"),
    ]
    deltas = compute_yoy_deltas(rows, "2025-annual", "2024-annual")
    expected = (
        (decimal.Decimal("416161000000") - decimal.Decimal("391035000000"))
        / decimal.Decimal("391035000000")
        * 100
    )
    assert deltas["revenue"] == float(expected)


def test_compute_yoy_deltas_missing_previous_year_metric_is_none() -> None:
    # eps_diluted only reported starting this year -- no prior value to diff.
    rows = [
        _financial_row("2025-annual", "eps_diluted", "7.5"),
        _financial_row("2024-annual", "revenue", "391035000000"),
    ]
    deltas = compute_yoy_deltas(rows, "2025-annual", "2024-annual")
    assert deltas["eps_diluted"] is None


def test_compute_yoy_deltas_previous_value_non_positive_is_guarded() -> None:
    rows = [
        _financial_row("2025-annual", "net_income", "1000"),
        _financial_row("2024-annual", "net_income", "-500"),
    ]
    deltas = compute_yoy_deltas(rows, "2025-annual", "2024-annual")
    assert deltas["net_income"] is None


def test_compute_yoy_deltas_single_period_company_yields_all_none() -> None:
    # Samsung: previous_period is None -- no per-metric lookup needed.
    rows = [
        _financial_row("2023-annual", "revenue", "1000"),
        _financial_row("2023-annual", "net_income", "100"),
    ]
    deltas = compute_yoy_deltas(rows, "2023-annual", None)
    assert deltas == {"revenue": None, "net_income": None}
