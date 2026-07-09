"""Offline tests for app.ingest.sec_ingest (SEC 10-K -> 4 tables + backfill).

Two layers, no network / no DB / no model load:

- **Pure builders/selectors** (``sec_company_row`` / ``sec_filing_row`` /
  ``sec_financial_rows`` / ``sec_period`` / ``sec_unit_for`` /
  ``select_target_filing`` / ``_filing_fiscal_year``) are driven with fixture
  objects only -- mirrors test_persist.py's pure-mapping style.
- **``ingest_sec_filing`` orchestration** is exercised with a mocked ``SecClient``
  and a fake ``AsyncSession`` factory (extending test_persist.py's
  ``_FakeUpsertSession`` idea: the natural-key ON CONFLICT column is read off the
  compiled statement so re-runs return stable ids). ``extract_10k_prose`` and
  ``backfill_embeddings`` are monkeypatched so the real HTML parser / KURE model
  stay out of these offline tests (they are covered by test_sec_document.py and
  the live end-to-end gate respectively).
"""

import asyncio
import datetime
import re
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
from app.ingest import sec_ingest
from app.ingest.chunking import chunk_document
from app.ingest.persist import chunk_rows
from app.ingest.sec_ingest import (
    SecIngestError,
    _filing_fiscal_year,
    sec_company_row,
    sec_filing_row,
    sec_financial_rows,
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

_COMPANY_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
_FILING_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _fact(metric, value, *, accn, fiscal_year, unit="USD", tag="X") -> SecFinancialItem:
    return SecFinancialItem(
        tag=tag,
        metric=metric,
        accession_number=accn,
        fiscal_year=fiscal_year,
        filed_fiscal_year=fiscal_year,
        fiscal_period="FY",
        period_start=None,
        period_end=datetime.date(fiscal_year, 9, 30),
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
            f"MD&A paragraph {i} discussing results of operations and liquidity."
            for i in range(20)
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


# -- sec_company_row ----------------------------------------------------------


def test_sec_company_row_fills_sec_cik_and_source() -> None:
    row = sec_company_row(_MATCH, _CIK)
    assert row["source"] == "sec"  # satisfies companies_source_check
    assert row["sec_cik"] == _CIK  # idempotent conflict key
    assert row["dart_corp_code"] is None
    assert row["name"] == "Apple Inc."
    assert row["ticker"] == "AAPL"
    assert row["market"] is None
    # SEC filer names are already English -> name_en mirrors name for bilingual search.
    assert row["name_en"] == "Apple Inc."


def test_sec_company_row_falls_back_to_cik_name_when_unresolved() -> None:
    # A filer absent from company_tickers.json: deterministic CIK-derived name,
    # never fabricated, ticker NULL -- identity is still the sec_cik natural key.
    row = sec_company_row(None, _CIK)
    assert row["name"] == f"CIK {_CIK}"
    assert row["ticker"] is None
    assert row["name_en"] == f"CIK {_CIK}"  # mirrors the fallback name
    assert row["sec_cik"] == _CIK
    assert row["source"] == "sec"


# -- sec_filing_row -----------------------------------------------------------


def test_sec_filing_row_fills_accession_and_nulls_rcept_no() -> None:
    row = sec_filing_row(_FILING_2023, _COMPANY_ID, fiscal_year=2023, url="https://sec.gov/x.htm")
    assert row["company_id"] == _COMPANY_ID
    assert row["source"] == "sec"
    assert row["sec_accession_no"] == _ACCN_2023  # idempotent conflict key
    assert row["rcept_no"] is None  # SEC has no DART receipt number
    assert row["filing_type"] == "10-K"
    assert row["title"] == "Form 10-K (FY2023)"
    assert row["period"] == "2023-annual"
    assert row["filed_at"] == datetime.date(2023, 11, 3)
    assert row["url"] == "https://sec.gov/x.htm"


# -- sec_financial_rows -------------------------------------------------------


def test_sec_financial_rows_source_currency_unit_and_filing_id() -> None:
    items = [
        _fact("revenue", 383_285_000_000, accn=_ACCN_2023, fiscal_year=2023),
        _fact("eps", Decimal("6.16"), accn=_ACCN_2023, fiscal_year=2023, unit="USD/shares"),
    ]
    rows = sec_financial_rows(items, _COMPANY_ID, _FILING_ID)
    assert len(rows) == 2
    for r in rows:
        assert r["company_id"] == _COMPANY_ID
        assert r["filing_id"] == _FILING_ID  # citation: always populated
        assert r["source"] == "sec"
        assert r["currency"] == "USD"
        assert r["period"] == "2023-annual"
        assert r["fiscal_year"] == 2023
        assert r["fiscal_quarter"] is None  # 10-K is annual
    by_metric = {r["metric"]: r for r in rows}
    assert by_metric["revenue"]["unit"] == "USD"
    assert by_metric["revenue"]["value"] == 383_285_000_000
    assert by_metric["eps"]["unit"] == "USD_PER_SHARE"
    assert isinstance(by_metric["eps"]["value"], Decimal)  # never float


def test_sec_financial_rows_dedups_by_metric_first_wins() -> None:
    items = [
        _fact("revenue", 100, accn=_ACCN_2023, fiscal_year=2023),
        _fact("revenue", 999, accn=_ACCN_2023, fiscal_year=2023),
    ]
    rows = sec_financial_rows(items, _COMPANY_ID, _FILING_ID)
    assert len(rows) == 1
    assert rows[0]["value"] == 100  # first occurrence wins


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
        accession_number="x", form="10-K", filing_date=None, report_date=None,
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


# -- meta/rcept_no decision: SEC chunks leave meta.rcept_no None ---------------


def test_sec_chunks_carry_null_rcept_no_in_meta() -> None:
    # The decision under test: the accession is NOT stuffed into the DART-specific
    # meta.rcept_no field; it stays None while section anchors survive. Provenance
    # rides on filing_id -> filings.sec_accession_no downstream.
    chunks = chunk_document(_SECTIONS, rcept_no=None)
    assert chunks  # non-empty
    rows = chunk_rows(chunks, _FILING_ID)
    assert all(r["meta"]["rcept_no"] is None for r in rows)
    assert all(r["filing_id"] == _FILING_ID for r in rows)
    assert rows[0]["meta"]["section_title"] == "Item 1. Business"
    assert rows[0]["meta"]["section_order"] == 0


# -- ingest_sec_filing orchestration (mocked client + fake session) -----------


class _FakeResult:
    def __init__(self, value=None) -> None:
        self._value = value

    def scalar_one(self):
        return self._value


class _FakeTxn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    """Records upsert natural keys; returns stable ids keyed on ON CONFLICT column.

    Only statements carrying a RETURNING clause (the company/filing upserts) need
    an id, so only those are compiled and keyed -- the financials upsert and the
    chunk delete/insert (which carry JSONB and need no id) are just recorded.
    """

    def __init__(self, store: dict) -> None:
        self._store = store
        self.upserts: list[tuple[str, dict]] = []  # (conflict_column, params)
        self.other: list = []  # non-returning statements

    def begin(self) -> _FakeTxn:
        return _FakeTxn()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        if getattr(stmt, "_returning", ()):  # company/filing upsert
            compiled = stmt.compile()
            col = re.search(r"ON CONFLICT \(([^)]+)\)", compiled.string).group(1)
            params = dict(compiled.params)
            self.upserts.append((col, params))
            key = (col, params[col])
            return _FakeResult(self._store.setdefault(key, uuid.uuid4()))
        self.other.append(stmt)
        return _FakeResult(None)


class _FakeSessionFactory:
    def __init__(self) -> None:
        self.store: dict = {}  # shared across sessions -> idempotent re-run
        self.sessions: list[_FakeSession] = []
        self.call_count = 0

    def __call__(self) -> _FakeSession:
        self.call_count += 1
        session = _FakeSession(self.store)
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
    """Stub backfill_embeddings (keep KURE out); record the session it got."""
    calls: list = []

    async def fake_backfill(session, **kwargs):
        calls.append(session)
        return count

    monkeypatch.setattr(sec_ingest, "backfill_embeddings", fake_backfill)
    return calls


def test_ingest_sec_filing_happy_path_wires_all_stages(monkeypatch) -> None:
    received = _patch_parse(monkeypatch)
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
    # backfill reused, on a second (fresh) session scope
    assert len(backfill_calls) == 1
    assert result.embeddings_backfilled == 4
    assert factory.call_count == 2
    # natural keys + source flowed into the upserts (session[0] = ingest txn)
    company = next(p for c, p in factory.sessions[0].upserts if c == "sec_cik")
    assert company["source"] == "sec"
    assert company["sec_cik"] == _CIK
    assert company.get("dart_corp_code") is None
    filing = next(p for c, p in factory.sessions[0].upserts if c == "sec_accession_no")
    assert filing["source"] == "sec"
    assert filing["sec_accession_no"] == _ACCN_2023
    assert filing.get("rcept_no") is None


def test_ingest_sec_filing_filters_facts_to_chosen_accession(monkeypatch) -> None:
    _patch_parse(monkeypatch)
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
    _patch_backfill(monkeypatch)
    client = _FakeSecClient(filings=[_FILING_2023], facts=_FACTS, match=_MATCH)
    factory = _FakeSessionFactory()  # shared store across both runs

    first = asyncio.run(sec_ingest.ingest_sec_filing(client, factory, _CIK))
    second = asyncio.run(sec_ingest.ingest_sec_filing(client, factory, _CIK))

    assert first.company_id == second.company_id  # upsert returns the same id
    assert first.filing_id == second.filing_id
    # exactly one company + one filing natural key in the store (no duplicates)
    assert len(factory.store) == 2
    assert first.financials_written == second.financials_written == 5


def test_ingest_sec_filing_parse_failure_propagates_and_persists_nothing(monkeypatch) -> None:
    _patch_parse(
        monkeypatch,
        raiser=SecDocumentParseError("Item 1 (Business): could not locate a heading"),
    )
    backfill_calls = _patch_backfill(monkeypatch)
    client = _FakeSecClient(filings=[_FILING_2023], facts=_FACTS, match=_MATCH)
    factory = _FakeSessionFactory()

    with pytest.raises(SecDocumentParseError):
        asyncio.run(sec_ingest.ingest_sec_filing(client, factory, _CIK))

    # Fail-loud BEFORE any DB scope opens: no session, no backfill, no facts fetch.
    assert factory.call_count == 0
    assert backfill_calls == []
    assert client.facts_calls == 0
