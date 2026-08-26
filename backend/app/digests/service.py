"""Build a company digest from authoritative facts and guarded prose."""

import logging
import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Company as CompanyModel
from app.db.models import Filing as FilingModel
from app.digest_narrative import DigestNarrativeError, build_company_summary
from app.evidence import EvidenceIntegrityError, filing_source_from_filing
from app.figures.service import fetch_financials
from app.financials.calculations import compute_yoy_deltas, select_reporting_periods
from app.financials.presentation import DIGEST_METRICS
from app.llm.base import LLMClient
from app.llm.solar import SolarApiError, SolarClientError
from app.schemas import CompanyDigest, FilingSource, MetricCard

logger = logging.getLogger(__name__)


class CompanyNotFoundError(LookupError):
    """The requested Regulated Company is not present in the corpus."""


async def build_company_digest(
    session: AsyncSession,
    client: LLMClient,
    company_id: uuid.UUID,
) -> CompanyDigest:
    """Return one company's latest figures, Filing Sources, and guarded summary."""
    company = (
        await session.execute(
            select(CompanyModel).where(CompanyModel.id == company_id)
        )
    ).scalar_one_or_none()
    if company is None:
        raise CompanyNotFoundError(str(company_id))

    rows = await fetch_financials(session, company_id=company_id)
    target_period, previous_period = select_reporting_periods(rows)
    yoy_deltas = compute_yoy_deltas(rows, target_period, previous_period)
    period_rows = [row for row in rows if row.period == target_period]
    by_metric = {row.metric: row for row in period_rows}

    filing_ids = {row.filing_id for row in period_rows if row.filing_id is not None}
    filings: list[FilingModel] = []
    filing_sources_by_filing_id: dict[uuid.UUID, FilingSource] = {}
    latest_filing_id: uuid.UUID | None = None
    if filing_ids:
        filings = (
            await session.execute(
                select(FilingModel).where(FilingModel.id.in_(filing_ids))
                .order_by(
                    FilingModel.filed_at.desc().nullslast(),
                    FilingModel.created_at.desc(),
                    FilingModel.id.desc(),
                )
            )
        ).scalars().all()
        for filing in filings:
            try:
                filing_sources_by_filing_id[filing.id] = filing_source_from_filing(
                    filing
                )
            except EvidenceIntegrityError as exc:
                logger.warning("digest omitted invalid Filing Source: %s", exc)
        if filings:
            latest_filing_id = filings[0].id
        if len(filing_ids) > 1:
            logger.warning(
                "digest: target_period=%s for company_id=%s spans multiple "
                "filings %s; picked filing_id=%s by filed_at desc",
                target_period,
                company_id,
                sorted(str(filing_id) for filing_id in filing_ids),
                latest_filing_id,
            )

    metrics: list[MetricCard] = []
    filing_sources: list[FilingSource] = []
    seen_source_ids: set[str] = set()
    for presentation in DIGEST_METRICS:
        key = presentation.metric
        row = by_metric.get(key.value)
        if row is None:
            continue
        filing_source = filing_sources_by_filing_id.get(row.filing_id)
        if filing_source is None:
            logger.warning(
                "digest omitted metric=%s period=%s without an openable Filing Source",
                row.metric,
                row.period,
            )
            continue
        metrics.append(
            MetricCard(
                key=key,
                label_ko=presentation.label_ko,
                label_en=presentation.label_en,
                value=float(row.value),
                unit=row.unit,
                yoy_delta_pct=yoy_deltas.get(key.value),
                source=row.source,
                filing_source_id=filing_source.id,
            )
        )
        if filing_source.id not in seen_source_ids:
            seen_source_ids.add(filing_source.id)
            filing_sources.append(filing_source)

    summary_ko: str | None = None
    summary_en: str | None = None
    if latest_filing_id in filing_sources_by_filing_id:
        try:
            summary_ko, summary_en = await build_company_summary(
                session, client, company_id, latest_filing_id
            )
        except (
            DigestNarrativeError,
            SolarApiError,
            SolarClientError,
            httpx.HTTPError,
        ) as exc:
            logger.warning(
                "digest summary generation failed for company_id=%s: %s",
                company_id,
                type(exc).__name__,
            )

    return CompanyDigest(
        company_id=str(company.id),
        company_name=company.name,
        period=target_period,
        metrics=metrics,
        summary_ko=summary_ko,
        summary_en=summary_en,
        filing_sources=filing_sources,
        generated_at=datetime.now(UTC).isoformat(),
    )
