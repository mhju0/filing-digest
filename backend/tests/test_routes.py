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

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api import routes
from app.api.routes import (
    escape_ilike_literal,
    select_latest_filing_id,
    select_previous_period,
    select_target_period,
)
from app.db.session import get_db_session
from app.financials.calculations import compute_yoy_deltas
from app.llm.deps import get_llm_client
from app.main import app
from app.schemas import AnswerRequest, SearchRequest

_FID_2023 = uuid.UUID("11111111-1111-1111-1111-111111111111")
_FID_2024 = uuid.UUID("22222222-2222-2222-2222-222222222222")
_FID_2025 = uuid.UUID("33333333-3333-3333-3333-333333333333")


def test_escape_ilike_literal_treats_wildcards_as_text() -> None:
    assert escape_ilike_literal(r"50%_off\today") == r"50\%\_off\\today"


def test_search_request_rejects_oversized_query() -> None:
    with pytest.raises(ValidationError):
        SearchRequest(query="x" * 501)


def test_answer_request_rejects_oversized_query_and_period() -> None:
    company_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    with pytest.raises(ValidationError):
        AnswerRequest(query="x" * 1_001, company_id=company_id)
    with pytest.raises(ValidationError):
        AnswerRequest(query="valid", company_id=company_id, period="x" * 33)


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


def test_select_previous_period_requires_adjacent_year_and_matching_scope() -> None:
    periods = ["2025-Q1", "2024-annual", "2024-Q1", "2023-Q1"]
    assert select_previous_period(periods, "2025-Q1") == "2024-Q1"
    assert select_previous_period(["2025-annual", "2023-annual"], "2025-annual") is None


# -- compute_yoy_deltas ----------------------------------------------------------


def _financial_row(
    period: str, metric: str, value: str, **over: object
) -> SimpleNamespace:
    row = {
        "period": period,
        "metric": metric,
        "value": decimal.Decimal(value),
        "fiscal_year": int(period[:4]),
        "period_kind": "duration",
        "period_start": None,
        "period_end": None,
        "fiscal_quarter": None,
        "currency": "KRW",
        "unit": "KRW",
        "scale": 1,
    }
    row.update(over)
    return SimpleNamespace(**row)


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


def test_compute_yoy_deltas_rejects_a_multi_year_gap() -> None:
    rows = [
        _financial_row("2025-annual", "revenue", "120"),
        _financial_row("2023-annual", "revenue", "100"),
    ]

    assert compute_yoy_deltas(rows, "2025-annual", "2023-annual") == {
        "revenue": None
    }


@pytest.mark.parametrize(
    ("current_override", "previous_override"),
    [
        ({"period_kind": "instant"}, {}),
        ({"currency": "USD"}, {}),
        ({"unit": "KRW_PER_SHARE"}, {}),
        ({"scale": 1_000}, {}),
        ({"fiscal_quarter": 1}, {"fiscal_quarter": 4}),
    ],
    ids=["period-kind", "currency", "unit", "scale", "fiscal-quarter"],
)
def test_compute_yoy_deltas_rejects_incompatible_financial_facts(
    current_override: dict[str, object], previous_override: dict[str, object]
) -> None:
    rows = [
        _financial_row("2025-annual", "revenue", "120", **current_override),
        _financial_row("2024-annual", "revenue", "100", **previous_override),
    ]

    assert compute_yoy_deltas(rows, "2025-annual", "2024-annual") == {
        "revenue": None
    }


def test_compute_yoy_deltas_allows_leap_year_duration_difference() -> None:
    rows = [
        _financial_row(
            "2024-annual",
            "revenue",
            "120",
            period_start=datetime.date(2024, 1, 1),
            period_end=datetime.date(2024, 12, 31),
        ),
        _financial_row(
            "2023-annual",
            "revenue",
            "100",
            period_start=datetime.date(2023, 1, 1),
            period_end=datetime.date(2023, 12, 31),
        ),
    ]

    assert compute_yoy_deltas(rows, "2024-annual", "2023-annual") == {
        "revenue": 20.0
    }


def test_compute_yoy_deltas_rejects_materially_different_known_durations() -> None:
    rows = [
        _financial_row(
            "2025-annual",
            "revenue",
            "120",
            period_start=datetime.date(2025, 1, 1),
            period_end=datetime.date(2025, 12, 31),
        ),
        _financial_row(
            "2024-partial",
            "revenue",
            "100",
            period_start=datetime.date(2024, 1, 1),
            period_end=datetime.date(2024, 11, 30),
        ),
    ]

    assert compute_yoy_deltas(rows, "2025-annual", "2024-partial") == {
        "revenue": None
    }


def test_compute_yoy_deltas_rejects_known_to_unknown_duration_comparison() -> None:
    rows = [
        _financial_row(
            "2025-annual",
            "revenue",
            "120",
            period_start=datetime.date(2025, 1, 1),
            period_end=datetime.date(2025, 12, 31),
        ),
        _financial_row("2024-annual", "revenue", "100"),
    ]

    assert compute_yoy_deltas(rows, "2025-annual", "2024-annual") == {
        "revenue": None
    }


def test_compute_yoy_deltas_rejects_known_to_unknown_instant_comparison() -> None:
    rows = [
        _financial_row(
            "2025-instant",
            "net_income",
            "120",
            period_kind="instant",
            period_end=datetime.date(2025, 12, 31),
        ),
        _financial_row(
            "2024-instant",
            "net_income",
            "100",
            period_kind="instant",
        ),
    ]

    assert compute_yoy_deltas(rows, "2025-instant", "2024-instant") == {
        "net_income": None
    }


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


def test_digest_metrics_reference_explicit_filing_sources(monkeypatch) -> None:
    company_id = uuid.UUID("44444444-4444-4444-4444-444444444444")
    filing_id = uuid.UUID("55555555-5555-5555-5555-555555555555")
    company = SimpleNamespace(id=company_id, name="삼성전자")
    filing = SimpleNamespace(
        id=filing_id,
        source="dart",
        rcept_no="20240312000736",
        sec_accession_no=None,
        title="사업보고서",
        url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20240312000736",
        filed_at=datetime.date(2024, 3, 12),
    )
    financial = SimpleNamespace(
        period="2023-annual",
        metric="revenue",
        value=decimal.Decimal("1000"),
        unit="KRW",
        source="dart",
        filing_id=filing_id,
    )

    class _DigestSession:
        def __init__(self) -> None:
            self.calls = 0

        async def execute(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(scalar_one_or_none=lambda: company)
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: [filing])
            )

    async def _financials(*args, **kwargs):
        return [financial]

    async def _summary(*args, **kwargs):
        return (None, None)

    app.dependency_overrides[get_db_session] = _DigestSession
    app.dependency_overrides[get_llm_client] = lambda: object()
    monkeypatch.setattr(routes, "fetch_financials", _financials)
    monkeypatch.setattr(routes, "build_company_summary", _summary)
    try:
        response = TestClient(app).get(f"/companies/{company_id}/digest")
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_llm_client, None)

    assert response.status_code == 200
    body = response.json()
    assert body["metrics"][0]["filing_source_id"] == "dart:20240312000736"
    assert "citation_id" not in body["metrics"][0]
    assert body["filing_sources"] == [
        {
            "id": "dart:20240312000736",
            "source": "dart",
            "source_filing_id": "20240312000736",
            "title": "사업보고서",
            "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20240312000736",
            "filed_at": "2024-03-12",
        }
    ]
    assert "citations" not in body


def test_digest_omits_a_metric_without_an_openable_filing_source(monkeypatch) -> None:
    company_id = uuid.UUID("66666666-6666-6666-6666-666666666666")
    filing_id = uuid.UUID("77777777-7777-7777-7777-777777777777")
    company = SimpleNamespace(id=company_id, name="Source-less company")
    filing = SimpleNamespace(
        id=filing_id,
        source="sec",
        rcept_no=None,
        sec_accession_no="0000320193-24-000123",
        title="10-K",
        url=None,
        filed_at=datetime.date(2024, 10, 31),
    )
    financial = SimpleNamespace(
        period="2024-annual",
        metric="revenue",
        value=decimal.Decimal("1000"),
        unit="USD",
        source="sec",
        filing_id=filing_id,
    )

    class _DigestSession:
        def __init__(self) -> None:
            self.calls = 0

        async def execute(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(scalar_one_or_none=lambda: company)
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: [filing])
            )

    async def _financials(*args, **kwargs):
        return [financial]

    async def _unexpected_summary(*args, **kwargs):
        raise AssertionError("summary cannot run without an openable Filing Source")

    app.dependency_overrides[get_db_session] = _DigestSession
    app.dependency_overrides[get_llm_client] = lambda: object()
    monkeypatch.setattr(routes, "fetch_financials", _financials)
    monkeypatch.setattr(routes, "build_company_summary", _unexpected_summary)
    try:
        response = TestClient(app).get(f"/companies/{company_id}/digest")
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_llm_client, None)

    assert response.status_code == 200
    body = response.json()
    assert body["metrics"] == []
    assert body["filing_sources"] == []
    assert body["summary_ko"] is None
    assert body["summary_en"] is None
