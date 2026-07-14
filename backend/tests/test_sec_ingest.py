"""Offline tests for the SEC adapter and ingestion orchestration.

Two layers, no network / no DB / no model load:

- **Normalized adapter and selectors** (``build_sec_normalized_filing`` /
  ``sec_period`` / ``sec_unit_for`` / ``select_target_filing`` /
  ``_filing_fiscal_year``) are driven with fixture objects only.
- **``ingest_sec_filing`` orchestration** is exercised with a mocked ``SecClient``
  and fakes at its two public side-effect seams: atomic Normalized Filing
  persistence and filing-scoped indexing. The real HTML parser, database, and
  KURE model stay out of these offline tests.
"""

import asyncio
import datetime
import uuid
from decimal import Decimal

import pytest

from app.clients.dart import ProseSection
from app.clients.sec import (
    SecCompanyMatch,
    SecDocumentPayload,
    SecFilingItem,
    SecFinancialItem,
    find_company_by_cik,
    format_cik,
)
from app.clients.sec_document import SecDocumentParseError
from app.filings.persistence import PersistedFiling
from app.financials.vocabulary import PeriodKind, ReportedMetric
from app.ingest import sec_ingest
from app.ingest.chunking import chunk_document
from app.ingest.sec_ingest import (
    SecIngestError,
    _filing_fiscal_year,
    build_sec_normalized_filing,
    sec_period,
    sec_unit_for,
    select_target_filing,
)

# -- fixtures (Apple Inc.-shaped) ---------------------------------------------

_CIK = "0000320193"
_ACCN_2023 = "0000320193-23-000106"
_ACCN_2022 = "0000320193-22-000108"

_FILING_2023 = SecFilingItem(
    accession_number=_ACCN_2023,
    form="10-K",
    filing_date=datetime.date(2023, 11, 3),
    report_date=datetime.date(2023, 9, 30),
    primary_document="aapl-20230930.htm",
)
_FILING_2022 = SecFilingItem(
    accession_number=_ACCN_2022,
    form="10-K",
    filing_date=datetime.date(2022, 10, 28),
    report_date=datetime.date(2022, 9, 24),
    primary_document="aapl-20220924.htm",
)

_MATCH = SecCompanyMatch(cik=_CIK, ticker="AAPL", title="Apple Inc.")

def _fact(
    metric,
    value,
    *,
    accn,
    fiscal_year,
    unit="USD",
    tag="X",
    period_start=None,
    period_end=None,
) -> SecFinancialItem:
    return SecFinancialItem(
        tag=tag,
        metric=metric,
        accession_number=accn,
        fiscal_year=fiscal_year,
        filed_fiscal_year=fiscal_year,
        fiscal_period="FY",
        period_start=period_start,
        period_end=period_end or datetime.date(fiscal_year, 9, 30),
        value=value,
        unit=unit,
        form="10-K",
        filed=None,
    )


# Five own-period facts for the 2023 10-K + one prior-filing fact that accession
# selection must filter out.
_FACTS = [
    _fact("revenue", 383_285_000_000, accn=_ACCN_2023, fiscal_year=2023),
    _fact("operating_income", 114_301_000_000, accn=_ACCN_2023, fiscal_year=2023),
    _fact("net_income", 96_995_000_000, accn=_ACCN_2023, fiscal_year=2023),
    _fact("eps", Decimal("6.16"), accn=_ACCN_2023, fiscal_year=2023, unit="USD/shares"),
    _fact("eps_diluted", Decimal("6.13"), accn=_ACCN_2023, fiscal_year=2023, unit="USD/shares"),
    _fact("revenue", 394_328_000_000, accn=_ACCN_2022, fiscal_year=2022),
]

# Multi-paragraph prose so chunk_document yields >= 1 chunk per section.
_SECTIONS = [
    ProseSection(
        section_title="Item 1. Business",
        content="\n".join(
            f"Business paragraph {i} describing the company's products and operations."
            for i in range(20)
        ),
        order=0,
    ),
    ProseSection(
        section_title="Item 7. Management's Discussion and Analysis",
        content="\n".join(
            f"MD&A paragraph {i} discussing results of operations and liquidity." for i in range(20)
        ),
        order=1,
    ),
]


# -- sec_period / sec_unit_for ------------------------------------------------


def test_sec_period_annual() -> None:
    assert sec_period(2023) == "2023-annual"


def test_sec_unit_for_splits_eps_from_amounts() -> None:
    assert sec_unit_for("revenue") == "USD"
    assert sec_unit_for("net_income") == "USD"
    assert sec_unit_for("eps") == "USD_PER_SHARE"
    assert sec_unit_for("eps_diluted") == "USD_PER_SHARE"


# -- public normalized adapter ------------------------------------------------


def test_sec_adapter_maps_company_filing_and_chunk_metadata() -> None:
    filing = build_sec_normalized_filing(
        company_match=_MATCH,
        cik10=_CIK,
        filing=_FILING_2023,
        fiscal_year=2023,
        document_url="https://www.sec.gov/Archives/aapl-20230930.htm",
        financial_items=[_FACTS[0]],
        chunks=chunk_document(_SECTIONS, rcept_no=None),
    )

    assert filing.identity.stable_id == f"sec:{_ACCN_2023}"
    assert filing.company.identity.source_company_id == _CIK
    assert filing.company.name == "Apple Inc."
    assert filing.company.name_en == "Apple Inc."
    assert filing.company.ticker == "AAPL"
    assert filing.company.market is None
    assert filing.filing_type == "10-K"
    assert filing.title == "Form 10-K (FY2023)"
    assert filing.reporting_period.label == "2023-annual"
    assert filing.reporting_period.kind is PeriodKind.duration
    assert filing.filed_at == datetime.date(2023, 11, 3)
    assert filing.url == "https://www.sec.gov/Archives/aapl-20230930.htm"
    assert [fact.metric.value for fact in filing.financial_facts] == ["revenue"]
    assert filing.filing_chunks[0].metadata["rcept_no"] is None
    assert filing.filing_chunks[0].metadata["section_title"] == "Item 1. Business"
    assert filing.filing_chunks[0].metadata["section_order"] == 0


def test_sec_adapter_uses_deterministic_company_fallback() -> None:
    filing = build_sec_normalized_filing(
        company_match=None,
        cik10=_CIK,
        filing=_FILING_2023,
        fiscal_year=2023,
        document_url="https://www.sec.gov/Archives/aapl-20230930.htm",
        financial_items=[],
        chunks=[],
    )

    assert filing.company.name == f"CIK {_CIK}"
    assert filing.company.name_en == f"CIK {_CIK}"
    assert filing.company.ticker is None


def test_sec_adapter_maps_usd_facts_and_deduplicates_by_metric() -> None:
    filing = build_sec_normalized_filing(
        company_match=_MATCH,
        cik10=_CIK,
        filing=_FILING_2023,
        fiscal_year=2023,
        document_url="https://www.sec.gov/Archives/aapl-20230930.htm",
        financial_items=[
            _fact(
                "revenue",
                383_285_000_000,
                accn=_ACCN_2023,
                fiscal_year=2023,
                period_start=datetime.date(2022, 9, 25),
                period_end=datetime.date(2023, 9, 30),
            ),
            _fact("revenue", 999, accn=_ACCN_2023, fiscal_year=2023),
            _fact(
                "eps",
                Decimal("6.16"),
                accn=_ACCN_2023,
                fiscal_year=2023,
                unit="USD/shares",
            ),
        ],
        chunks=[],
    )

    assert [
        (fact.metric, fact.value, fact.unit, fact.currency)
        for fact in filing.financial_facts
    ] == [
        (ReportedMetric.revenue, Decimal(383_285_000_000), "USD", "USD"),
        (ReportedMetric.eps, Decimal("6.16"), "USD_PER_SHARE", "USD"),
    ]
    revenue_period = filing.financial_facts[0].period
    assert revenue_period.kind is PeriodKind.duration
    assert revenue_period.start_date == datetime.date(2022, 9, 25)
    assert revenue_period.end_date == datetime.date(2023, 9, 30)
    # A half-known range is not represented as if it were complete.
    assert filing.financial_facts[1].period.start_date is None
    assert filing.financial_facts[1].period.end_date is None


# -- select_target_filing / _filing_fiscal_year -------------------------------


def test_select_target_filing_picks_latest_by_filing_date() -> None:
    target = select_target_filing([_FILING_2022, _FILING_2023], None)
    assert target.accession_number == _ACCN_2023


def test_select_target_filing_honors_explicit_accession() -> None:
    target = select_target_filing([_FILING_2022, _FILING_2023], _ACCN_2022)
    assert target.accession_number == _ACCN_2022


def test_select_target_filing_unknown_accession_raises() -> None:
    with pytest.raises(SecIngestError):
        select_target_filing([_FILING_2023], "9999999999-99-999999")


def test_select_target_filing_empty_raises() -> None:
    with pytest.raises(SecIngestError):
        select_target_filing([], None)


def test_filing_fiscal_year_prefers_facts_own_period() -> None:
    facts = [_fact("revenue", 1, accn=_ACCN_2023, fiscal_year=2023)]
    assert _filing_fiscal_year(_FILING_2023, facts) == 2023


def test_filing_fiscal_year_falls_back_to_report_date() -> None:
    assert _filing_fiscal_year(_FILING_2022, []) == 2022


def test_filing_fiscal_year_raises_when_undeterminable() -> None:
    filing = SecFilingItem(
        accession_number="x",
        form="10-K",
        filing_date=None,
        report_date=None,
        primary_document="p.htm",
    )
    with pytest.raises(SecIngestError):
        _filing_fiscal_year(filing, [])


# -- find_company_by_cik (sec.py) ---------------------------------------------


def test_find_company_by_cik_matches_normalized_cik() -> None:
    records = [
        SecCompanyMatch(cik="0000320193", ticker="AAPL", title="Apple Inc."),
        SecCompanyMatch(cik="0000789019", ticker="MSFT", title="Microsoft Corp"),
    ]
    assert find_company_by_cik(records, 320193).ticker == "AAPL"  # int normalized
    assert find_company_by_cik(records, "0000789019").ticker == "MSFT"
    assert find_company_by_cik(records, 1) is None  # absent -> None, not a raise


# -- ingest_sec_filing orchestration (mocked client + fake session) -----------


class _FakeSession:
    """Async context-manager token passed through to the public side effects."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSessionFactory:
    def __init__(self) -> None:
        self.sessions: list[_FakeSession] = []
        self.call_count = 0

    def __call__(self) -> _FakeSession:
        self.call_count += 1
        session = _FakeSession()
        self.sessions.append(session)
        return session


class _FakeSecClient:
    def __init__(self, *, filings, facts, match, doc_bytes=b"<html>doc</html>") -> None:
        self._filings = filings
        self._facts = facts
        self._match = match
        self._doc_bytes = doc_bytes
        self.document_request: tuple | None = None
        self.last_filing_types = None
        self.facts_calls = 0

    async def resolve_company_by_cik(self, cik):
        return self._match

    async def list_filings(self, cik, filing_types=None):
        self.last_filing_types = filing_types
        return list(self._filings)

    async def fetch_document(self, cik, accession_number, primary_document):
        self.document_request = (accession_number, primary_document)
        return SecDocumentPayload(
            cik=format_cik(cik),
            accession_number=accession_number,
            primary_document=primary_document,
            url=f"https://www.sec.gov/Archives/edgar/data/320193/x/{primary_document}",
            raw_bytes=self._doc_bytes,
        )

    async def fetch_company_facts(self, cik):
        self.facts_calls += 1
        return list(self._facts)


def _patch_parse(monkeypatch, *, sections=_SECTIONS, raiser=None) -> dict:
    """Stub extract_10k_prose; capture the bytes it received (or raise ``raiser``)."""
    received: dict = {}

    def fake_extract(raw_bytes):
        received["raw_bytes"] = raw_bytes
        if raiser is not None:
            raise raiser
        return list(sections)

    monkeypatch.setattr(sec_ingest, "extract_10k_prose", fake_extract)
    return received


def _patch_backfill(monkeypatch, *, count=4) -> list:
    """Stub filing-scoped indexing (keep KURE out); record its public inputs."""
    calls: list = []

    async def fake_backfill(session, filing_id, **kwargs):
        calls.append((session, filing_id))
        return count

    monkeypatch.setattr(sec_ingest, "index_filing_embeddings", fake_backfill)
    return calls


def _patch_persistence(monkeypatch) -> tuple[list, dict]:
    """Stub the public atomic persistence seam with stable identity-keyed ids."""
    calls: list = []
    identities: dict[tuple[str, str], uuid.UUID] = {}

    async def fake_persist(session, filing):
        company_key = (
            filing.company.identity.source.value,
            filing.company.identity.source_company_id,
        )
        filing_key = (filing.identity.source.value, filing.identity.source_filing_id)
        company_id = identities.setdefault(company_key, uuid.uuid4())
        filing_id = identities.setdefault(filing_key, uuid.uuid4())
        calls.append((session, filing))
        return PersistedFiling(
            company_id=company_id,
            filing_id=filing_id,
            financial_facts_written=len(filing.financial_facts),
            filing_chunks_written=len(filing.filing_chunks),
        )

    monkeypatch.setattr(sec_ingest, "persist_normalized_filing", fake_persist)
    return calls, identities


def test_ingest_sec_filing_happy_path_wires_all_stages(monkeypatch) -> None:
    received = _patch_parse(monkeypatch)
    persistence_calls, _ = _patch_persistence(monkeypatch)
    backfill_calls = _patch_backfill(monkeypatch, count=4)
    client = _FakeSecClient(filings=[_FILING_2022, _FILING_2023], facts=_FACTS, match=_MATCH)
    factory = _FakeSessionFactory()

    result = asyncio.run(sec_ingest.ingest_sec_filing(client, factory, _CIK))

    # latest 10-K selected (filed 2023 > 2022); its primary doc fetched + parsed
    assert client.last_filing_types == ["10-K"]
    assert client.document_request == (_ACCN_2023, "aapl-20230930.htm")
    assert received["raw_bytes"] == b"<html>doc</html>"
    # facts filtered to the chosen accession only (5 of 6)
    assert result.financials_written == 5
    assert result.chunks_written >= 2
    assert result.accession_number == _ACCN_2023
    # Scoped indexing uses a second, fresh session after atomic persistence.
    assert len(persistence_calls) == 1
    persisted = persistence_calls[0][1]
    assert persisted.company.identity.source.value == "sec"
    assert persisted.company.identity.source_company_id == _CIK
    assert persisted.identity.source_filing_id == _ACCN_2023
    assert len(backfill_calls) == 1
    assert backfill_calls[0][1] == result.filing_id
    assert result.embeddings_backfilled == 4
    assert factory.call_count == 2


def test_ingest_sec_filing_filters_facts_to_chosen_accession(monkeypatch) -> None:
    _patch_parse(monkeypatch)
    _patch_persistence(monkeypatch)
    _patch_backfill(monkeypatch)
    # Explicit accession = the OLDER filing; only its single own-period fact writes.
    client = _FakeSecClient(filings=[_FILING_2022, _FILING_2023], facts=_FACTS, match=_MATCH)
    factory = _FakeSessionFactory()

    result = asyncio.run(
        sec_ingest.ingest_sec_filing(client, factory, _CIK, accession_number=_ACCN_2022)
    )

    assert client.document_request == (_ACCN_2022, "aapl-20220924.htm")
    assert result.accession_number == _ACCN_2022
    assert result.financials_written == 1  # only the 2022-accession revenue fact


def test_ingest_sec_filing_idempotent_rerun(monkeypatch) -> None:
    _patch_parse(monkeypatch)
    persistence_calls, identities = _patch_persistence(monkeypatch)
    _patch_backfill(monkeypatch)
    client = _FakeSecClient(filings=[_FILING_2023], facts=_FACTS, match=_MATCH)
    factory = _FakeSessionFactory()

    first = asyncio.run(sec_ingest.ingest_sec_filing(client, factory, _CIK))
    second = asyncio.run(sec_ingest.ingest_sec_filing(client, factory, _CIK))

    assert first.company_id == second.company_id  # upsert returns the same id
    assert first.filing_id == second.filing_id
    # Both snapshots carried the same regulator identities through the public seam.
    assert len(persistence_calls) == 2
    assert persistence_calls[0][1].identity == persistence_calls[1][1].identity
    assert len(identities) == 2  # one company identity and one filing identity
    assert first.financials_written == second.financials_written == 5


def test_ingest_sec_filing_parse_failure_propagates_and_persists_nothing(monkeypatch) -> None:
    _patch_parse(
        monkeypatch,
        raiser=SecDocumentParseError("Item 1 (Business): could not locate a heading"),
    )
    persistence_calls, _ = _patch_persistence(monkeypatch)
    backfill_calls = _patch_backfill(monkeypatch)
    client = _FakeSecClient(filings=[_FILING_2023], facts=_FACTS, match=_MATCH)
    factory = _FakeSessionFactory()

    with pytest.raises(SecDocumentParseError):
        asyncio.run(sec_ingest.ingest_sec_filing(client, factory, _CIK))

    # Fail-loud BEFORE any DB scope opens: no session, no backfill, no facts fetch.
    assert factory.call_count == 0
    assert persistence_calls == []
    assert backfill_calls == []
    assert client.facts_calls == 0
