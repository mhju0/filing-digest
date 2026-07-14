"""Behavior tests for the public Normalized Filing domain seam."""

import datetime
from decimal import Decimal

import pytest

from app.filings.model import (
    CompanyIdentity,
    FilingChunk,
    FilingIdentity,
    FinancialFact,
    NormalizedFiling,
    RegulatedCompany,
    RegulatorySource,
    ReportingPeriod,
)
from app.financials.vocabulary import PeriodKind, ReportedMetric


def test_reporting_period_never_accepts_a_partial_duration_range() -> None:
    with pytest.raises(ValueError, match="both be provided"):
        ReportingPeriod(
            label="FY2025",
            kind=PeriodKind.duration,
            start_date=datetime.date(2025, 1, 1),
        )


def test_normalized_filing_rejects_cross_source_company_identity() -> None:
    company = RegulatedCompany(
        identity=CompanyIdentity(RegulatorySource.dart, "00126380"),
        name="Samsung Electronics",
    )

    with pytest.raises(ValueError, match="same regulatory source"):
        NormalizedFiling(
            company=company,
            identity=FilingIdentity(RegulatorySource.sec, "0000320193-25-000079"),
            filing_type="10-K",
            title="Form 10-K",
            reporting_period=ReportingPeriod("2025-annual", PeriodKind.duration),
        )


def test_normalized_filing_rejects_duplicate_reported_fact_identity() -> None:
    period = ReportingPeriod("2025-annual", PeriodKind.duration)
    fact = FinancialFact(
        metric=ReportedMetric.revenue,
        period=period,
        value=Decimal("100"),
        unit="KRW",
        currency="KRW",
    )

    with pytest.raises(ValueError, match="duplicate Financial Fact"):
        NormalizedFiling(
            company=RegulatedCompany(
                identity=CompanyIdentity(RegulatorySource.dart, "00126380"),
                name="Samsung Electronics",
            ),
            identity=FilingIdentity(RegulatorySource.dart, "20260312000736"),
            filing_type="business_report",
            title="사업보고서 (2025.12)",
            reporting_period=period,
            financial_facts=(fact, fact),
        )


def test_normalized_filing_rejects_duplicate_chunk_index() -> None:
    chunk = FilingChunk(chunk_index=0, content="Evidence paragraph", metadata={})

    with pytest.raises(ValueError, match="duplicate Filing Chunk"):
        NormalizedFiling(
            company=RegulatedCompany(
                identity=CompanyIdentity(RegulatorySource.sec, "0000320193"),
                name="Apple Inc.",
            ),
            identity=FilingIdentity(RegulatorySource.sec, "0000320193-25-000079"),
            filing_type="10-K",
            title="Form 10-K (FY2025)",
            reporting_period=ReportingPeriod("2025-annual", PeriodKind.duration),
            filing_chunks=(chunk, chunk),
        )
