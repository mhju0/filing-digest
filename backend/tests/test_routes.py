"""Offline unit tests for company digest selection and HTTP contracts.

The digest selection functions are pure over already-fetched rows, so they are
exercised through the company digest module interface without DB or HTTP.
"""

import datetime
import decimal
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.routes import escape_ilike_literal
from app.db.session import get_db_session
from app.digests import service as digests
from app.financials.calculations import compute_yoy_deltas, select_reporting_periods
from app.llm.deps import get_llm_client
from app.main import app
from app.schemas import AnswerRequest, SearchRequest


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


def _period_row(
    period: str,
    fiscal_year: int,
    fiscal_quarter: int | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        period=period,
        fiscal_year=fiscal_year,
        fiscal_quarter=fiscal_quarter,
        period_kind="duration",
        period_start=None,
        period_end=None,
    )


def test_select_reporting_periods_uses_temporal_fields_not_label_order() -> None:
    rows = [_period_row("z-older", 2024), _period_row("a-newer", 2025)]
    assert select_reporting_periods(rows) == ("a-newer", "z-older")


def test_select_reporting_periods_prefers_later_scope_within_a_year() -> None:
    rows = [_period_row("2025-Q3", 2025, 3), _period_row("2025-annual", 2025)]
    assert select_reporting_periods(rows) == ("2025-annual", None)


def test_select_reporting_periods_requires_adjacent_matching_scope() -> None:
    rows = [
        _period_row("2025-Q1", 2025, 1),
        _period_row("2024-annual", 2024),
        _period_row("2024-Q1", 2024, 1),
        _period_row("2023-Q1", 2023, 1),
    ]
    assert select_reporting_periods(rows) == ("2025-Q1", "2024-Q1")


def test_select_reporting_periods_handles_an_empty_corpus() -> None:
    assert select_reporting_periods([]) == ("", None)


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
        currency="KRW",
        scale=1,
        source="dart",
        filing_id=filing_id,
        fiscal_year=2023,
        fiscal_quarter=None,
        period_kind="duration",
        period_start=None,
        period_end=None,
    )

    class _DigestSession:
        def __init__(self) -> None:
            self.calls = 0
            self.statements = []

        async def execute(self, statement, *args, **kwargs):
            self.calls += 1
            self.statements.append(statement)
            if self.calls == 1:
                return SimpleNamespace(scalar_one_or_none=lambda: company)
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: [filing])
            )

    async def _financials(*args, **kwargs):
        return [financial]

    async def _summary(*args, **kwargs):
        return (None, None)

    digest_session = _DigestSession()
    app.dependency_overrides[get_db_session] = lambda: digest_session
    app.dependency_overrides[get_llm_client] = lambda: object()
    monkeypatch.setattr(digests, "fetch_financials", _financials)
    monkeypatch.setattr(digests, "build_company_summary", _summary)
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
    filing_sql = digest_session.statements[1].compile().string
    assert "ORDER BY filings.filed_at DESC NULLS LAST" in filing_sql
    assert "filings.created_at DESC" in filing_sql
    assert "filings.id DESC" in filing_sql


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
        currency="USD",
        scale=1,
        source="sec",
        filing_id=filing_id,
        fiscal_year=2024,
        fiscal_quarter=None,
        period_kind="duration",
        period_start=None,
        period_end=None,
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
    monkeypatch.setattr(digests, "fetch_financials", _financials)
    monkeypatch.setattr(digests, "build_company_summary", _unexpected_summary)
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
