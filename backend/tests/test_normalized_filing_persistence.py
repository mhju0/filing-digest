"""PostgreSQL behavior tests for the public Normalized Filing persistence seam."""

import asyncio
import datetime
import os
from dataclasses import replace
from decimal import Decimal

import pytest
from sqlalchemy import text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DataError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base, Filing
from app.embeddings.backfill import index_filing_embeddings
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
from app.filings.persistence import load_normalized_filing, persist_normalized_filing
from app.financials.vocabulary import PeriodKind, ReportedMetric
from app.search.service import search_chunks

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if TEST_DATABASE_URL and not (make_url(TEST_DATABASE_URL).database or "").endswith("_test"):
    raise RuntimeError(
        "refusing to drop tables: TEST_DATABASE_URL must name an isolated *_test database"
    )
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL persistence tests",
)


def _snapshot(*, include_eps: bool, include_second_chunk: bool) -> NormalizedFiling:
    period = ReportingPeriod(
        label="2025-annual",
        kind=PeriodKind.duration,
        start_date=datetime.date(2025, 1, 1),
        end_date=datetime.date(2025, 12, 31),
    )
    facts = [
        FinancialFact(
            metric=ReportedMetric.revenue,
            period=period,
            value=Decimal("1000000"),
            unit="KRW",
            currency="KRW",
        )
    ]
    if include_eps:
        facts.append(
            FinancialFact(
                metric=ReportedMetric.eps,
                period=period,
                value=Decimal("12.34"),
                unit="KRW_PER_SHARE",
                currency="KRW",
            )
        )
    chunks = [FilingChunk(0, "Current evidence", {"section_title": "Business"})]
    if include_second_chunk:
        chunks.append(FilingChunk(1, "Stale evidence", {"section_title": "Risk"}))
    return NormalizedFiling(
        company=RegulatedCompany(
            identity=CompanyIdentity(RegulatorySource.dart, "00126380"),
            name="Samsung Electronics",
            ticker="005930",
            market="KOSPI",
        ),
        identity=FilingIdentity(RegulatorySource.dart, "20260312000736"),
        filing_type="business_report",
        title="Business Report (2025.12)",
        reporting_period=period,
        financial_facts=tuple(facts),
        filing_chunks=tuple(chunks),
        filed_at=datetime.date(2026, 3, 12),
        url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260312000736",
    )


def test_reingestion_replaces_the_complete_authoritative_snapshot() -> None:
    async def run() -> None:
        engine = create_async_engine(TEST_DATABASE_URL)
        try:
            async with engine.begin() as connection:
                await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                await connection.run_sync(Base.metadata.drop_all)
                await connection.run_sync(Base.metadata.create_all)

            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                first_snapshot = _snapshot(include_eps=True, include_second_chunk=True)
                first_snapshot = replace(
                    first_snapshot,
                    company=replace(
                        first_snapshot.company,
                        name_en="Samsung Electronics Co., Ltd.",
                    ),
                )
                first = await persist_normalized_filing(
                    session,
                    first_snapshot,
                )
                second = await persist_normalized_filing(
                    session,
                    _snapshot(include_eps=False, include_second_chunk=False),
                )
                loaded = await load_normalized_filing(
                    session,
                    FilingIdentity(RegulatorySource.dart, "20260312000736"),
                )

            assert first.filing_id == second.filing_id
            assert loaded is not None
            assert loaded.company.name_en == "Samsung Electronics Co., Ltd."
            assert [fact.metric for fact in loaded.financial_facts] == [ReportedMetric.revenue]
            assert [chunk.content for chunk in loaded.filing_chunks] == ["Current evidence"]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_indexing_one_filing_never_exposes_an_unfinished_filing(monkeypatch) -> None:
    vector = [1.0] + [0.0] * 1023
    monkeypatch.setattr(
        "app.embeddings.backfill.embed_texts",
        lambda texts: [vector for _ in texts],
    )
    monkeypatch.setattr("app.search.service.embed_texts", lambda texts: [vector])

    async def run() -> None:
        engine = create_async_engine(TEST_DATABASE_URL)
        try:
            async with engine.begin() as connection:
                await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                await connection.run_sync(Base.metadata.drop_all)
                await connection.run_sync(Base.metadata.create_all)

            factory = async_sessionmaker(engine, expire_on_commit=False)
            first_snapshot = _snapshot(include_eps=False, include_second_chunk=True)
            second_snapshot = replace(
                first_snapshot,
                identity=FilingIdentity(RegulatorySource.dart, "20270312000736"),
                title="Business Report (2026.12)",
                reporting_period=ReportingPeriod("2026-annual", PeriodKind.duration),
                financial_facts=(),
                filing_chunks=(FilingChunk(0, "Still pending", {}),),
            )

            async with factory() as session:
                first = await persist_normalized_filing(session, first_snapshot)
                second = await persist_normalized_filing(session, second_snapshot)
                assert await index_filing_embeddings(session, first.filing_id, limit=1) == 1
                partial_hits = await search_chunks(
                    session,
                    "query",
                    top_k=10,
                    company_id=first.company_id,
                )
                assert partial_hits == []

                # Recover defensively from a stale published marker: the scoped
                # indexer must hide the whole filing before resuming batches.
                await session.execute(
                    update(Filing)
                    .where(Filing.id == first.filing_id)
                    .values(indexed_at=datetime.datetime.now(datetime.UTC))
                )
                await session.commit()
                assert await index_filing_embeddings(session, first.filing_id, limit=0) == 0
                recovery_hits = await search_chunks(
                    session,
                    "query",
                    top_k=10,
                    company_id=first.company_id,
                )
                assert recovery_hits == []

                assert await index_filing_embeddings(session, first.filing_id) == 1
                hits = await search_chunks(
                    session,
                    "query",
                    top_k=10,
                    company_id=first.company_id,
                )

            assert {hit.filing_id for hit in hits} == {first.filing_id}
            assert len(hits) == 2
            assert second.filing_id not in {hit.filing_id for hit in hits}
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_failed_replacement_rolls_back_to_the_previous_complete_snapshot() -> None:
    async def run() -> None:
        engine = create_async_engine(TEST_DATABASE_URL)
        try:
            async with engine.begin() as connection:
                await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                await connection.run_sync(Base.metadata.drop_all)
                await connection.run_sync(Base.metadata.create_all)

            factory = async_sessionmaker(engine, expire_on_commit=False)
            baseline = _snapshot(include_eps=True, include_second_chunk=True)
            oversized_fact = replace(
                baseline.financial_facts[0],
                value=Decimal("1000000000000000000000000000000"),
            )
            invalid_replacement = replace(
                baseline,
                financial_facts=(oversized_fact,),
                filing_chunks=(FilingChunk(0, "Would replace evidence", {}),),
            )

            async with factory() as session:
                await persist_normalized_filing(session, baseline)
                with pytest.raises(DataError):
                    await persist_normalized_filing(session, invalid_replacement)
                loaded = await load_normalized_filing(session, baseline.identity)

            assert loaded is not None
            assert {fact.metric for fact in loaded.financial_facts} == {
                ReportedMetric.revenue,
                ReportedMetric.eps,
            }
            assert [chunk.content for chunk in loaded.filing_chunks] == [
                "Current evidence",
                "Stale evidence",
            ]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_two_filings_can_report_the_same_period_without_reassigning_provenance() -> None:
    async def run() -> None:
        engine = create_async_engine(TEST_DATABASE_URL)
        try:
            async with engine.begin() as connection:
                await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                await connection.run_sync(Base.metadata.drop_all)
                await connection.run_sync(Base.metadata.create_all)

            factory = async_sessionmaker(engine, expire_on_commit=False)
            first = _snapshot(include_eps=False, include_second_chunk=False)
            second = replace(
                first,
                identity=FilingIdentity(RegulatorySource.dart, "20260401000001"),
                title="Corrected Business Report (2025.12)",
                financial_facts=(replace(first.financial_facts[0], value=Decimal("2000000")),),
                filing_chunks=(FilingChunk(0, "Corrected evidence", {}),),
            )

            async with factory() as session:
                first_result = await persist_normalized_filing(session, first)
                second_result = await persist_normalized_filing(session, second)
                loaded_first = await load_normalized_filing(session, first.identity)
                loaded_second = await load_normalized_filing(session, second.identity)

            assert first_result.filing_id != second_result.filing_id
            assert loaded_first is not None and loaded_second is not None
            assert loaded_first.financial_facts[0].value == Decimal("1000000.0000")
            assert loaded_second.financial_facts[0].value == Decimal("2000000.0000")
        finally:
            await engine.dispose()

    asyncio.run(run())
