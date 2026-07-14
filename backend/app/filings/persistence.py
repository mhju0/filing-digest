"""Atomic PostgreSQL persistence for complete Normalized Filing snapshots."""

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Company, Filing, Financial
from app.db.models import FilingChunk as FilingChunkRow
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


@dataclass(frozen=True)
class PersistedFiling:
    """Database references and snapshot sizes after one atomic replacement."""

    company_id: uuid.UUID
    filing_id: uuid.UUID
    financial_facts_written: int
    filing_chunks_written: int


def _source_columns(source: RegulatorySource) -> tuple[str, str]:
    if source is RegulatorySource.dart:
        return "dart_corp_code", "rcept_no"
    if source is RegulatorySource.sec:
        return "sec_cik", "sec_accession_no"
    raise ValueError(f"unsupported regulatory source {source!r}")


async def _upsert_company(session: AsyncSession, filing: NormalizedFiling) -> uuid.UUID:
    company_key, _ = _source_columns(filing.identity.source)
    values = {
        "name": filing.company.name,
        "name_en": filing.company.name_en,
        "ticker": filing.company.ticker,
        "market": filing.company.market,
        "source": filing.identity.source.value,
        "dart_corp_code": (
            filing.company.identity.source_company_id
            if filing.identity.source is RegulatorySource.dart
            else None
        ),
        "sec_cik": (
            filing.company.identity.source_company_id
            if filing.identity.source is RegulatorySource.sec
            else None
        ),
    }
    statement = pg_insert(Company).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=[company_key],
        set_={
            "name": statement.excluded.name,
            "name_en": func.coalesce(statement.excluded.name_en, Company.name_en),
            "ticker": statement.excluded.ticker,
            "market": statement.excluded.market,
            "source": statement.excluded.source,
        },
    ).returning(Company.id)
    return (await session.execute(statement)).scalar_one()


async def _upsert_filing(
    session: AsyncSession,
    filing: NormalizedFiling,
    company_id: uuid.UUID,
) -> uuid.UUID:
    _, filing_key = _source_columns(filing.identity.source)
    values = {
        "company_id": company_id,
        "source": filing.identity.source.value,
        "rcept_no": (
            filing.identity.source_filing_id
            if filing.identity.source is RegulatorySource.dart
            else None
        ),
        "sec_accession_no": (
            filing.identity.source_filing_id
            if filing.identity.source is RegulatorySource.sec
            else None
        ),
        "filing_type": filing.filing_type,
        "title": filing.title,
        "period": filing.reporting_period.label,
        "filed_at": filing.filed_at,
        "url": filing.url,
        "indexed_at": None,
    }
    statement = pg_insert(Filing).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=[filing_key],
        set_={
            "company_id": statement.excluded.company_id,
            "source": statement.excluded.source,
            "filing_type": statement.excluded.filing_type,
            "title": statement.excluded.title,
            "period": statement.excluded.period,
            "filed_at": statement.excluded.filed_at,
            "url": statement.excluded.url,
            "indexed_at": None,
        },
    ).returning(Filing.id)
    return (await session.execute(statement)).scalar_one()


def _legacy_fiscal_fields(period: ReportingPeriod) -> tuple[int, int | None]:
    """Derive presentation-only legacy columns from a source-provided label."""
    match = re.fullmatch(r"(\d{4})-(annual|Q1|H1|Q3)", period.label)
    if match is None:
        if period.end_date is None:
            raise ValueError(
                f"Reporting Period {period.label!r} has no legacy fiscal-year representation"
            )
        return period.end_date.year, None
    suffix = match.group(2)
    quarter = {"annual": None, "Q1": 1, "H1": 2, "Q3": 3}[suffix]
    return int(match.group(1)), quarter


def _financial_rows(
    filing: NormalizedFiling,
    company_id: uuid.UUID,
    filing_id: uuid.UUID,
) -> list[dict]:
    rows: list[dict] = []
    for fact in filing.financial_facts:
        fiscal_year, fiscal_quarter = _legacy_fiscal_fields(fact.period)
        rows.append(
            {
                "company_id": company_id,
                "filing_id": filing_id,
                "fiscal_year": fiscal_year,
                "fiscal_quarter": fiscal_quarter,
                "period": fact.period.label,
                "period_kind": fact.period.kind.value,
                "period_start": fact.period.start_date,
                "period_end": fact.period.end_date,
                "metric": fact.metric.value,
                "value": fact.value,
                "unit": fact.unit,
                "currency": fact.currency,
                "scale": fact.scale,
                "source": filing.identity.source.value,
            }
        )
    return rows


def _chunk_rows(filing: NormalizedFiling, filing_id: uuid.UUID) -> list[dict]:
    return [
        {
            "filing_id": filing_id,
            "chunk_index": chunk.chunk_index,
            "content": chunk.content,
            "embedding": None,
            "meta": dict(chunk.metadata),
        }
        for chunk in filing.filing_chunks
    ]


async def persist_normalized_filing(
    session: AsyncSession,
    filing: NormalizedFiling,
) -> PersistedFiling:
    """Replace one filing's facts and chunks atomically by Filing Identity."""
    async with session.begin():
        company_id = await _upsert_company(session, filing)
        filing_id = await _upsert_filing(session, filing, company_id)

        await session.execute(delete(Financial).where(Financial.filing_id == filing_id))
        financial_rows = _financial_rows(filing, company_id, filing_id)
        if financial_rows:
            await session.execute(insert(Financial).values(financial_rows))

        await session.execute(delete(FilingChunkRow).where(FilingChunkRow.filing_id == filing_id))
        chunk_rows = _chunk_rows(filing, filing_id)
        if chunk_rows:
            await session.execute(insert(FilingChunkRow).values(chunk_rows))

    return PersistedFiling(
        company_id=company_id,
        filing_id=filing_id,
        financial_facts_written=len(financial_rows),
        filing_chunks_written=len(chunk_rows),
    )


async def load_normalized_filing(
    session: AsyncSession,
    identity: FilingIdentity,
) -> NormalizedFiling | None:
    """Load the current authoritative snapshot through the domain interface."""
    company_key, filing_key = _source_columns(identity.source)
    statement = (
        select(Filing, Company)
        .join(Company, Company.id == Filing.company_id)
        .where(getattr(Filing, filing_key) == identity.source_filing_id)
    )
    result = (await session.execute(statement)).one_or_none()
    if result is None:
        return None
    filing_row, company_row = result

    financial_rows = (
        (
            await session.execute(
                select(Financial)
                .where(Financial.filing_id == filing_row.id)
                .order_by(Financial.period, Financial.metric)
            )
        )
        .scalars()
        .all()
    )
    facts = tuple(
        FinancialFact(
            metric=ReportedMetric(row.metric),
            period=ReportingPeriod(
                label=row.period,
                kind=PeriodKind(row.period_kind),
                start_date=row.period_start,
                end_date=row.period_end,
            ),
            value=row.value,
            unit=row.unit,
            currency=row.currency,
            scale=row.scale,
        )
        for row in financial_rows
    )
    chunks = tuple(
        FilingChunk(
            chunk_index=row.chunk_index,
            content=row.content,
            metadata=row.meta,
        )
        for row in (
            (
                await session.execute(
                    select(FilingChunkRow)
                    .where(FilingChunkRow.filing_id == filing_row.id)
                    .order_by(FilingChunkRow.chunk_index)
                )
            )
            .scalars()
            .all()
        )
    )
    current_period = next(
        (fact.period for fact in facts if fact.period.label == filing_row.period),
        ReportingPeriod(label=filing_row.period or "unknown", kind=PeriodKind.duration),
    )
    source_company_id = getattr(company_row, company_key)
    if source_company_id is None:
        raise ValueError(f"stored company has no {company_key}")

    return NormalizedFiling(
        company=RegulatedCompany(
            identity=CompanyIdentity(identity.source, source_company_id),
            name=company_row.name,
            name_en=company_row.name_en,
            ticker=company_row.ticker,
            market=company_row.market,
        ),
        identity=identity,
        filing_type=filing_row.filing_type,
        title=filing_row.title,
        reporting_period=current_period,
        financial_facts=facts,
        filing_chunks=chunks,
        filed_at=filing_row.filed_at,
        url=filing_row.url,
    )
