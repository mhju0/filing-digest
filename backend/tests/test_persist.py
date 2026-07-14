"""Offline tests for the public DART-to-Normalized-Filing adapter.

These exercise source vocabulary and the complete adapter output with fixture
objects only. Atomic database replacement is covered through the same public
Normalized Filing seam in ``test_normalized_filing_persistence.py``.
"""

import datetime
from decimal import Decimal

import pytest

from app.clients.dart import FilingItem, FinancialItem
from app.financials.vocabulary import PeriodKind, ReportedMetric
from app.ingest.chunking import Chunk
from app.ingest.persist import (
    UNIT_KRW,
    UNIT_KRW_PER_SHARE,
    PeriodDescriptor,
    build_dart_normalized_filing,
    filing_type_for,
    market_for,
    period_descriptor,
    unit_for,
)


def _filing_item(**over) -> FilingItem:
    base = dict(
        rcept_no="20240312000736",
        corp_code="00126380",
        corp_name="삼성전자",
        report_nm="사업보고서 (2023.12)",
        flr_nm="삼성전자",
        rcept_dt=datetime.date(2024, 3, 12),
        rm="",
        stock_code="005930",
        corp_cls="Y",
    )
    base.update(over)
    return FilingItem(**base)


def _fin_item(
    metric,
    amount,
    *,
    prior_amount=None,
    sj_div="IS",
    account_id="x",
    currency="KRW",
) -> FinancialItem:
    return FinancialItem(
        rcept_no="20240312000736",
        reprt_code="11011",
        bsns_year="2023",
        sj_div=sj_div,
        sj_nm="",
        account_id=account_id,
        account_nm="",
        thstrm_amount=amount,
        frmtrm_amount=prior_amount,
        ord=1,
        currency=currency,
        metric=metric,
    )


def _chunk(
    idx: int,
    *,
    part_index: int = 0,
    section_order: int = 0,
    title: str = "회사의 개요",
) -> Chunk:
    return Chunk(
        content=f"본문 {idx}",
        chunk_index=idx,
        rcept_no="20240312000736",
        section_title=title,
        section_order=section_order,
        part_index=part_index,
    )


def _normalized(
    *,
    filing_item: FilingItem | None = None,
    descriptor: PeriodDescriptor | None = None,
    financial_items: list[FinancialItem] | None = None,
    chunks: list[Chunk] | None = None,
    name_en: str | None = "Samsung Electronics Co., Ltd.",
):
    return build_dart_normalized_filing(
        filing_item=filing_item or _filing_item(),
        corp_code="00126380",
        name_en=name_en,
        descriptor=descriptor or period_descriptor("2023", "11011"),
        filing_type="business_report",
        financial_items=financial_items or [],
        chunks=chunks or [],
    )


# -- source vocabulary --------------------------------------------------------


def test_period_descriptor_annual() -> None:
    descriptor = period_descriptor("2023", "11011")
    assert descriptor == PeriodDescriptor(
        period="2023-annual",
        fiscal_year=2023,
        fiscal_quarter=None,
    )


@pytest.mark.parametrize(
    "reprt_code,period,quarter",
    [
        ("11013", "2023-Q1", 1),
        ("11012", "2023-H1", 2),
        ("11014", "2023-Q3", 3),
    ],
)
def test_period_descriptor_interim(reprt_code, period, quarter) -> None:
    descriptor = period_descriptor("2023", reprt_code)
    assert (descriptor.period, descriptor.fiscal_year, descriptor.fiscal_quarter) == (
        period,
        2023,
        quarter,
    )


def test_period_descriptor_is_stable_across_calls() -> None:
    assert period_descriptor("2023", "11011") == period_descriptor("2023", "11011")


def test_period_descriptor_rejects_unknown_code() -> None:
    with pytest.raises(ValueError):
        period_descriptor("2023", "99999")


def test_period_descriptor_rejects_bad_year() -> None:
    with pytest.raises(ValueError):
        period_descriptor("not-a-year", "11011")


def test_filing_type_for() -> None:
    assert filing_type_for("11011") == "business_report"
    assert filing_type_for("11013") == "quarterly_report"
    with pytest.raises(ValueError):
        filing_type_for("00000")


def test_market_for() -> None:
    assert market_for("Y") == "KOSPI"
    assert market_for("k") == "KOSDAQ"
    assert market_for("") is None
    assert market_for(None) is None
    assert market_for("Z") is None


def test_unit_for_splits_eps_from_amounts() -> None:
    assert unit_for("net_income") == UNIT_KRW
    assert unit_for("revenue") == UNIT_KRW
    assert unit_for("eps") == UNIT_KRW_PER_SHARE
    assert unit_for("eps_diluted") == UNIT_KRW_PER_SHARE


# -- public normalized adapter ------------------------------------------------


def test_dart_adapter_maps_company_and_filing_metadata() -> None:
    filing = _normalized()

    assert filing.identity.stable_id == "dart:20240312000736"
    assert filing.company.identity.source_company_id == "00126380"
    assert filing.company.name == "삼성전자"
    assert filing.company.name_en == "Samsung Electronics Co., Ltd."
    assert filing.company.ticker == "005930"
    assert filing.company.market == "KOSPI"
    assert filing.filing_type == "business_report"
    assert filing.title == "사업보고서 (2023.12)"
    assert filing.reporting_period.label == "2023-annual"
    assert filing.reporting_period.kind is PeriodKind.duration
    assert filing.filed_at == datetime.date(2024, 3, 12)
    assert filing.url == "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20240312000736"


def test_dart_adapter_preserves_missing_optional_company_metadata() -> None:
    filing = _normalized(
        filing_item=_filing_item(stock_code="", corp_cls="Z"),
        name_en=None,
    )

    assert filing.company.name_en is None
    assert filing.company.ticker is None
    assert filing.company.market is None


def test_dart_adapter_maps_deduplicated_current_and_prior_facts() -> None:
    filing = _normalized(
        financial_items=[
            _fin_item("revenue", 258_935_494_000_000, prior_amount=302_231_360_000_000),
            _fin_item(
                "revenue",
                999,
                prior_amount=888,
                sj_div="CIS",
            ),
            _fin_item("eps", Decimal("2131.50"), currency=""),
            _fin_item(None, 123),
            _fin_item("net_income", None),
        ]
    )

    assert [
        (fact.metric, fact.period.label, fact.value, fact.unit, fact.currency)
        for fact in filing.financial_facts
    ] == [
        (
            ReportedMetric.revenue,
            "2023-annual",
            Decimal(258_935_494_000_000),
            UNIT_KRW,
            "KRW",
        ),
        (
            ReportedMetric.revenue,
            "2022-annual",
            Decimal(302_231_360_000_000),
            UNIT_KRW,
            "KRW",
        ),
        (
            ReportedMetric.eps,
            "2023-annual",
            Decimal("2131.50"),
            UNIT_KRW_PER_SHARE,
            "KRW",
        ),
    ]
    assert all(fact.period.kind is PeriodKind.duration for fact in filing.financial_facts)


def test_dart_adapter_does_not_emit_prior_fact_for_interim_report() -> None:
    filing = _normalized(
        descriptor=period_descriptor("2023", "11013"),
        financial_items=[_fin_item("revenue", 71_000, prior_amount=63_000)],
    )

    assert [(fact.metric, fact.period.label) for fact in filing.financial_facts] == [
        (ReportedMetric.revenue, "2023-Q1")
    ]


def test_dart_adapter_maps_filing_chunk_citation_anchors() -> None:
    filing = _normalized(chunks=[_chunk(0), _chunk(1, part_index=1)])

    assert [chunk.content for chunk in filing.filing_chunks] == ["본문 0", "본문 1"]
    assert [chunk.chunk_index for chunk in filing.filing_chunks] == [0, 1]
    assert filing.filing_chunks[0].metadata == {
        "rcept_no": "20240312000736",
        "section_title": "회사의 개요",
        "section_order": 0,
        "part_index": 0,
    }
    assert filing.filing_chunks[1].metadata["part_index"] == 1
