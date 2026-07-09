"""Offline tests for the pure mapping layer of app.ingest.persist (docs §6).

These exercise the "cleaned object -> row dict" transforms and the canonical
vocabulary helpers with fixture objects only -- no network, no database. The
full ``ingest_filing`` transaction (network fetch + all 4 tables) is covered by
the live end-to-end verification, not here. The ``_upsert_company``/
``_upsert_filing`` DART/SEC conflict-column branching IS covered here, against
a fake in-memory session (see ``_FakeUpsertSession`` below) -- no real Postgres.
"""

import asyncio
import datetime
import re
import uuid
from decimal import Decimal

import pytest

from app.clients.dart import FilingItem, FinancialItem
from app.ingest.chunking import Chunk
from app.ingest.persist import (
    SOURCE_DART,
    SOURCE_SEC,
    UNIT_KRW,
    UNIT_KRW_PER_SHARE,
    PeriodDescriptor,
    _upsert_company,
    _upsert_filing,
    chunk_rows,
    company_row,
    filing_row,
    filing_type_for,
    financial_rows,
    market_for,
    period_descriptor,
    unit_for,
)

_COMPANY_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
_FILING_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


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


def _fin_item(metric, amount, *, sj_div="IS", account_id="x", currency="KRW") -> FinancialItem:
    return FinancialItem(
        rcept_no="20240312000736",
        reprt_code="11011",
        bsns_year="2023",
        sj_div=sj_div,
        sj_nm="",
        account_id=account_id,
        account_nm="",
        thstrm_amount=amount,
        frmtrm_amount=None,
        ord=1,
        currency=currency,
        metric=metric,
    )


# -- period_descriptor / filing_type_for --------------------------------------


def test_period_descriptor_annual() -> None:
    d = period_descriptor("2023", "11011")
    assert d == PeriodDescriptor(period="2023-annual", fiscal_year=2023, fiscal_quarter=None)


@pytest.mark.parametrize(
    "reprt_code,period,quarter",
    [
        ("11013", "2023-Q1", 1),
        ("11012", "2023-H1", 2),
        ("11014", "2023-Q3", 3),
    ],
)
def test_period_descriptor_interim(reprt_code, period, quarter) -> None:
    d = period_descriptor("2023", reprt_code)
    assert (d.period, d.fiscal_year, d.fiscal_quarter) == (period, 2023, quarter)


def test_period_descriptor_is_stable_across_calls() -> None:
    # Idempotency hinges on this: same inputs -> byte-identical period string.
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


# -- market_for / unit_for ----------------------------------------------------


def test_market_for() -> None:
    assert market_for("Y") == "KOSPI"
    assert market_for("k") == "KOSDAQ"  # case-insensitive
    assert market_for("") is None
    assert market_for(None) is None
    assert market_for("Z") is None  # unknown class -> None, not a guess


def test_unit_for_splits_eps_from_amounts() -> None:
    assert unit_for("net_income") == UNIT_KRW
    assert unit_for("revenue") == UNIT_KRW
    assert unit_for("eps") == UNIT_KRW_PER_SHARE
    assert unit_for("eps_diluted") == UNIT_KRW_PER_SHARE


# -- company_row --------------------------------------------------------------


def test_company_row_maps_required_and_optional_fields() -> None:
    row = company_row(_filing_item(), corp_code="00126380")
    assert row["name"] == "삼성전자"  # NOT NULL
    assert row["source"] == SOURCE_DART  # satisfies companies_source_check
    assert row["dart_corp_code"] == "00126380"  # idempotent conflict key
    assert row["ticker"] == "005930"
    assert row["market"] == "KOSPI"  # corp_cls Y -> KOSPI
    assert row["name_en"] is None  # no enrichment supplied -> NULL, never invented
    assert row["sec_cik"] is None


def test_company_row_populates_name_en_when_supplied() -> None:
    # company.json enrichment (corp_name_eng) flows in via the name_en kwarg.
    row = company_row(
        _filing_item(), corp_code="00126380", name_en="SAMSUNG ELECTRONICS CO,.LTD"
    )
    assert row["name_en"] == "SAMSUNG ELECTRONICS CO,.LTD"


def test_company_row_blank_ticker_becomes_null() -> None:
    row = company_row(_filing_item(stock_code=""), corp_code="00126380")
    assert row["ticker"] is None


# -- filing_row ---------------------------------------------------------------


def test_filing_row_maps_fields_and_injects_company_id() -> None:
    row = filing_row(_filing_item(), _COMPANY_ID, filing_type="business_report", period="2023-annual")
    assert row["company_id"] == _COMPANY_ID
    assert row["source"] == SOURCE_DART
    assert row["rcept_no"] == "20240312000736"  # idempotent conflict key
    assert row["filing_type"] == "business_report"  # NOT NULL
    assert row["title"] == "사업보고서 (2023.12)"  # NOT NULL <- report_nm
    assert row["period"] == "2023-annual"
    assert row["filed_at"] == datetime.date(2024, 3, 12)
    assert row["url"] == "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20240312000736"


# -- financial_rows -----------------------------------------------------------


def test_financial_rows_every_row_has_filing_id_and_company_id() -> None:
    items = [_fin_item("revenue", 258935494000000), _fin_item("net_income", 15487100000000)]
    desc = period_descriptor("2023", "11011")
    rows = financial_rows(items, _COMPANY_ID, _FILING_ID, desc)
    assert len(rows) == 2
    for r in rows:
        # Citation rule: filing_id is ALWAYS populated (nullable column notwithstanding).
        assert r["filing_id"] == _FILING_ID
        assert r["company_id"] == _COMPANY_ID
        assert r["period"] == "2023-annual"
        assert r["fiscal_year"] == 2023
        assert r["fiscal_quarter"] is None
        assert r["source"] == SOURCE_DART


def test_financial_rows_skips_unmapped_and_none_value() -> None:
    items = [
        _fin_item(None, 123),  # unmapped account -> metric is NOT NULL, skip
        _fin_item("revenue", None),  # empty/unparseable value -> value is NOT NULL, skip
        _fin_item("net_income", 15487100000000),  # kept
    ]
    rows = financial_rows(items, _COMPANY_ID, _FILING_ID, period_descriptor("2023", "11011"))
    assert [r["metric"] for r in rows] == ["net_income"]


def test_financial_rows_dedups_by_metric_first_wins() -> None:
    # Same metric emitted under two statements with identical value: only one row,
    # otherwise a single INSERT would make ON CONFLICT affect a row twice.
    items = [
        _fin_item("revenue", 258935494000000, sj_div="IS"),
        _fin_item("revenue", 258935494000000, sj_div="CIS"),
    ]
    rows = financial_rows(items, _COMPANY_ID, _FILING_ID, period_descriptor("2023", "11011"))
    assert len(rows) == 1
    assert rows[0]["metric"] == "revenue"


def test_financial_rows_preserves_eps_as_decimal_with_per_share_unit() -> None:
    eps = Decimal("2131.50")
    rows = financial_rows(
        [_fin_item("eps", eps)], _COMPANY_ID, _FILING_ID, period_descriptor("2023", "11011")
    )
    assert len(rows) == 1
    value = rows[0]["value"]
    assert isinstance(value, Decimal)  # never float
    assert value == Decimal("2131.50")  # exact
    assert rows[0]["unit"] == UNIT_KRW_PER_SHARE


def test_financial_rows_amount_uses_krw_unit_and_default_currency() -> None:
    rows = financial_rows(
        [_fin_item("net_income", 15487100000000, currency="")],
        _COMPANY_ID,
        _FILING_ID,
        period_descriptor("2023", "11011"),
    )
    assert rows[0]["unit"] == UNIT_KRW
    assert rows[0]["currency"] == "KRW"  # blank currency -> DEFAULT_CURRENCY


# -- chunk_rows ---------------------------------------------------------------


def _chunk(idx: int, *, part_index: int = 0, section_order: int = 0, title="회사의 개요") -> Chunk:
    return Chunk(
        content=f"본문 {idx}",
        chunk_index=idx,
        rcept_no="20240312000736",
        section_title=title,
        section_order=section_order,
        part_index=part_index,
    )


def test_chunk_rows_embedding_null_and_anchor_in_meta() -> None:
    rows = chunk_rows([_chunk(0), _chunk(1, part_index=1)], _FILING_ID)
    assert len(rows) == 2
    for i, r in enumerate(rows):
        assert r["filing_id"] == _FILING_ID
        assert r["chunk_index"] == i
        assert r["embedding"] is None  # backfilled next step
        assert r["meta"]["rcept_no"] == "20240312000736"
        assert r["meta"]["section_title"] == "회사의 개요"
        assert r["meta"]["section_order"] == 0
    assert rows[0]["meta"]["part_index"] == 0
    assert rows[1]["meta"]["part_index"] == 1


def test_chunk_rows_empty_is_empty() -> None:
    assert chunk_rows([], _FILING_ID) == []


# -- _upsert_company / _upsert_filing conflict-column branching --------------
#
# Fake in-memory AsyncSession: compiles the real statement built by
# _upsert_company/_upsert_filing, reads which column the ON CONFLICT clause
# targeted, and keys an id store on (column, value) -- a second execute() with
# the same natural key returns the same id, exactly like a real Postgres
# upsert would. This exercises the actual branching code, offline.


class _FakeUpsertResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value


class _FakeUpsertSession:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, object], uuid.UUID] = {}
        self.last_sql: str | None = None

    async def execute(self, stmt):
        compiled = stmt.compile()
        self.last_sql = compiled.string
        conflict_column = re.search(r"ON CONFLICT \(([^)]+)\)", compiled.string).group(1)
        key = (conflict_column, compiled.params[conflict_column])
        row_id = self.rows.setdefault(key, uuid.uuid4())
        return _FakeUpsertResult(row_id)


def _raw_company_row(**over) -> dict:
    base = dict(
        name="Test Co",
        name_en=None,
        ticker=None,
        market=None,
        source=SOURCE_DART,
        dart_corp_code="00126380",
        sec_cik=None,
    )
    base.update(over)
    return base


def _raw_filing_row(**over) -> dict:
    base = dict(
        company_id=_COMPANY_ID,
        source=SOURCE_DART,
        rcept_no="20240312000736",
        sec_accession_no=None,
        filing_type="business_report",
        title="title",
        period="2023-annual",
        filed_at=None,
        url=None,
    )
    base.update(over)
    return base


def test_upsert_company_dart_conflicts_on_dart_corp_code() -> None:
    session = _FakeUpsertSession()
    asyncio.run(_upsert_company(session, _raw_company_row()))
    assert ("dart_corp_code", "00126380") in session.rows


def test_upsert_company_sec_conflicts_on_sec_cik() -> None:
    session = _FakeUpsertSession()
    row = _raw_company_row(source=SOURCE_SEC, dart_corp_code=None, sec_cik="0000320193")
    asyncio.run(_upsert_company(session, row))
    assert ("sec_cik", "0000320193") in session.rows


def test_upsert_company_missing_natural_key_raises() -> None:
    session = _FakeUpsertSession()
    row = _raw_company_row(source=SOURCE_SEC, sec_cik=None)
    with pytest.raises(ValueError):
        asyncio.run(_upsert_company(session, row))


def test_upsert_company_coalesces_name_en_on_conflict() -> None:
    # Regression guard: a DART re-ingest supplies name_en=None, so the ON CONFLICT
    # update must COALESCE the incoming NULL against the stored value -- otherwise
    # it would wipe an English name backfilled from company.json. Asserting on the
    # generated SQL (not row order) makes this discriminate the actual clause.
    session = _FakeUpsertSession()
    asyncio.run(_upsert_company(session, _raw_company_row()))
    sql = session.last_sql.lower()
    assert "coalesce(excluded.name_en, companies.name_en)" in sql
    # Other columns still overwrite plainly (they are always authoritative).
    assert "name = excluded.name" in sql


def test_upsert_filing_dart_conflicts_on_rcept_no() -> None:
    session = _FakeUpsertSession()
    asyncio.run(_upsert_filing(session, _raw_filing_row()))
    assert ("rcept_no", "20240312000736") in session.rows


def test_upsert_filing_sec_conflicts_on_sec_accession_no_and_is_idempotent() -> None:
    session = _FakeUpsertSession()
    row = _raw_filing_row(
        source=SOURCE_SEC, rcept_no=None, sec_accession_no="0000320193-24-000123"
    )

    async def _run_twice():
        first = await _upsert_filing(session, row)
        second = await _upsert_filing(session, row)
        return first, second

    first_id, second_id = asyncio.run(_run_twice())
    assert first_id == second_id  # re-run upserts the same row, no duplicate
    assert len(session.rows) == 1


def test_upsert_filing_missing_natural_key_raises() -> None:
    session = _FakeUpsertSession()
    row = _raw_filing_row(source=SOURCE_SEC, sec_accession_no=None)
    with pytest.raises(ValueError):
        asyncio.run(_upsert_filing(session, row))
