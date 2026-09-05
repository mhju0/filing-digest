"""PostgreSQL behavior tests for the public Normalized Filing persistence seam."""

import asyncio
import datetime
import os
from contextlib import asynccontextmanager
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DataError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base, Filing
from app.db.models import FilingChunk as ChunkRow
from app.embeddings.backfill import backfill_embeddings, index_filing_embeddings
from app.filings.model import (
    CompanyIdentity,
    FilingChunk,
    FilingChunkLocation,
    FilingIdentity,
    NormalizedFiling,
    RegulatedCompany,
    RegulatorySource,
)
from app.filings.persistence import load_normalized_filing, persist_normalized_filing
from app.financials.model import FinancialFact, ReportingPeriod
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


@asynccontextmanager
async def _database():
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.execute(text((Path(__file__).parents[1] / "db/init.sql").read_text()))
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


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
    chunks = [
        FilingChunk(
            0,
            "Current evidence",
            FilingChunkLocation(section_title="Business"),
        )
    ]
    if include_second_chunk:
        chunks.append(
            FilingChunk(
                1,
                "Stale evidence",
                FilingChunkLocation(section_title="Risk"),
            )
        )
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
        async with _database() as factory:
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

    asyncio.run(run())


def test_indexing_one_filing_never_exposes_an_unfinished_filing(monkeypatch) -> None:
    vector = [1.0] + [0.0] * 1023
    monkeypatch.setattr(
        "app.embeddings.backfill.embed_texts",
        lambda texts: [vector for _ in texts],
    )
    monkeypatch.setattr("app.search.service.embed_texts", lambda texts: [vector])

    async def run() -> None:
        async with _database() as factory:
            first_snapshot = _snapshot(include_eps=False, include_second_chunk=True)
            second_snapshot = replace(
                first_snapshot,
                identity=FilingIdentity(RegulatorySource.dart, "20270312000736"),
                title="Business Report (2026.12)",
                reporting_period=ReportingPeriod("2026-annual", PeriodKind.duration),
                financial_facts=(),
                filing_chunks=(FilingChunk(0, "Still pending"),),
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

    asyncio.run(run())


def test_failed_replacement_rolls_back_to_the_previous_complete_snapshot() -> None:
    async def run() -> None:
        async with _database() as factory:
            baseline = _snapshot(include_eps=True, include_second_chunk=True)
            oversized_fact = replace(
                baseline.financial_facts[0],
                value=Decimal("1000000000000000000000000000000"),
            )
            invalid_replacement = replace(
                baseline,
                financial_facts=(oversized_fact,),
                filing_chunks=(FilingChunk(0, "Would replace evidence"),),
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

    asyncio.run(run())


def test_two_filings_can_report_the_same_period_without_reassigning_provenance() -> None:
    async def run() -> None:
        async with _database() as factory:
            first = _snapshot(include_eps=False, include_second_chunk=False)
            second = replace(
                first,
                identity=FilingIdentity(RegulatorySource.dart, "20260401000001"),
                title="Corrected Business Report (2025.12)",
                financial_facts=(replace(first.financial_facts[0], value=Decimal("2000000")),),
                filing_chunks=(FilingChunk(0, "Corrected evidence"),),
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

    asyncio.run(run())


def test_failed_batch_keeps_progress_and_retry_preserves_vector_alignment(monkeypatch) -> None:
    calls = 0

    def encode(texts):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("interrupted encoder")
        return [[0.0] * int(text) + [1.0] + [0.0] * (1023 - int(text)) for text in texts]

    monkeypatch.setattr("app.embeddings.backfill.embed_texts", encode)

    async def run():
        async with _database() as factory:
            async with factory() as session:
                snapshot = replace(
                    _snapshot(include_eps=False, include_second_chunk=False),
                    filing_chunks=tuple(FilingChunk(i, str(i)) for i in range(5)),
                )
                saved = await persist_normalized_filing(session, snapshot)
                with pytest.raises(RuntimeError, match="interrupted encoder"):
                    await index_filing_embeddings(session, saved.filing_id, batch_size=2)
                published = (await session.execute(select(Filing.indexed_at))).scalar_one()
                rows = (await session.execute(select(ChunkRow.embedding))).scalars().all()
                assert published is None
                assert sum(vector is not None for vector in rows) == 2
                assert await backfill_embeddings(session, batch_size=2) == 3
                rows = (await session.execute(select(ChunkRow.content, ChunkRow.embedding))).all()
                for content, vector in rows:
                    assert list(vector) == encode([content])[0]
                assert (await session.execute(select(Filing.indexed_at))).scalar_one() is not None
                assert await backfill_embeddings(session, batch_size=2) == 0

    asyncio.run(run())


@pytest.mark.parametrize("vector_count", [1, 3])
def test_vector_count_mismatch_writes_none_of_the_batch(monkeypatch, vector_count) -> None:
    monkeypatch.setattr(
        "app.embeddings.backfill.embed_texts",
        lambda texts: [[1.0] + [0.0] * 1023 for _ in range(vector_count)],
    )

    async def run():
        async with _database() as factory:
            async with factory() as session:
                saved = await persist_normalized_filing(
                    session, _snapshot(include_eps=False, include_second_chunk=True)
                )
                with pytest.raises(ValueError):
                    await index_filing_embeddings(session, saved.filing_id)
                rows = (await session.execute(select(ChunkRow.embedding))).scalars().all()
                assert rows == [None, None]
                assert (await session.execute(select(Filing.indexed_at))).scalar_one() is None

    asyncio.run(run())


def test_reingestion_during_encoding_cannot_publish_replacement_chunks(monkeypatch) -> None:
    from threading import Event

    started, release = Event(), Event()

    def encode(texts):
        started.set()
        assert release.wait(5), "event loop blocked while encoding"
        return [[1.0] + [0.0] * 1023 for _ in texts]

    monkeypatch.setattr("app.embeddings.backfill.embed_texts", encode)

    async def run():
        async with _database() as factory:
            snapshot = _snapshot(include_eps=False, include_second_chunk=True)
            async with factory() as session:
                saved = await persist_normalized_filing(session, snapshot)
                indexing = asyncio.create_task(index_filing_embeddings(session, saved.filing_id))
                try:
                    assert await asyncio.to_thread(started.wait, 5)
                    async with factory() as replacement_session:
                        await persist_normalized_filing(
                            replacement_session,
                            replace(snapshot, filing_chunks=(FilingChunk(0, "Replacement"),)),
                        )
                finally:
                    release.set()
                    await indexing
                rows = (await session.execute(select(ChunkRow.content, ChunkRow.embedding))).all()
                assert rows == [("Replacement", None)]
                assert (await session.execute(select(Filing.indexed_at))).scalar_one() is None

    asyncio.run(run())


def test_backfill_recovers_a_stale_readiness_marker(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.embeddings.backfill.embed_texts", lambda texts: [[1.0] + [0.0] * 1023 for _ in texts]
    )

    async def run():
        async with _database() as factory:
            async with factory() as session:
                saved = await persist_normalized_filing(
                    session, _snapshot(include_eps=False, include_second_chunk=False)
                )
                await session.execute(
                    update(Filing).where(Filing.id == saved.filing_id)
                    .values(indexed_at=datetime.datetime.now(datetime.UTC))
                )
                await session.commit()
                assert await backfill_embeddings(session) == 1
                assert (await session.execute(select(ChunkRow.embedding))).scalar_one() is not None
                assert (await session.execute(select(Filing.indexed_at))).scalar_one() is not None

    asyncio.run(run())


@pytest.mark.parametrize("options", [{"batch_size": 0}, {"batch_size": -1}, {"limit": -1}])
def test_invalid_index_options_do_not_unpublish_a_ready_filing(monkeypatch, options) -> None:
    monkeypatch.setattr(
        "app.embeddings.backfill.embed_texts", lambda texts: [[1.0] + [0.0] * 1023 for _ in texts]
    )

    async def run():
        async with _database() as factory:
            async with factory() as session:
                saved = await persist_normalized_filing(
                    session, _snapshot(include_eps=False, include_second_chunk=False)
                )
                await index_filing_embeddings(session, saved.filing_id)
                with pytest.raises(ValueError):
                    await index_filing_embeddings(session, saved.filing_id, **options)
                assert (await session.execute(select(Filing.indexed_at))).scalar_one() is not None

    asyncio.run(run())


def test_readiness_counts_hold_the_filing_lock_against_replacement(monkeypatch) -> None:
    from sqlalchemy.exc import OperationalError

    monkeypatch.setattr(
        "app.embeddings.backfill.embed_texts", lambda texts: [[1.0] + [0.0] * 1023 for _ in texts]
    )

    async def run():
        async with _database() as factory:
            async with factory() as session:
                saved = await persist_normalized_filing(
                    session, _snapshot(include_eps=False, include_second_chunk=False)
                )
                execute = session.execute
                lock_checked = False

                async def checking_execute(statement, *args, **kwargs):
                    nonlocal lock_checked
                    result = await execute(statement, *args, **kwargs)
                    # Pause after reading readiness counts, before publication.
                    # A competing writer must be unable to replace the snapshot
                    # in this interval, even though it uses another connection.
                    if str(statement).startswith("SELECT count("):
                        async with factory() as competing:
                            await competing.execute(text("SET LOCAL lock_timeout = '100ms'"))
                            with pytest.raises(OperationalError) as error:
                                await competing.execute(
                                    update(Filing).where(Filing.id == saved.filing_id)
                                    .values(indexed_at=None)
                                )
                            assert error.value.orig.sqlstate == "55P03"
                            lock_checked = True
                    return result

                monkeypatch.setattr(session, "execute", checking_execute)
                assert await index_filing_embeddings(session, saved.filing_id) == 1
                assert lock_checked

    asyncio.run(run())
