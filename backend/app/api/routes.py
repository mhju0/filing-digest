"""API CONTRACT v0.3 endpoints, backed by the real database.

Principle: numbers come only from structured APIs (DART/SEC structured data);
the LLM narrates only; every claim carries a citation.
"""

import logging
import re
import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.db.models import Company as CompanyModel
from app.db.models import Filing as FilingModel
from app.db.session import get_db_session
from app.digest_narrative import DigestNarrativeError, build_company_summary
from app.evidence import (
    EvidenceIntegrityError,
    filing_source_from_filing,
    resolve_evidence,
)
from app.figures.service import build_figures, fetch_financials
from app.financials.calculations import compute_yoy_deltas
from app.financials.presentation import DIGEST_METRICS
from app.llm.base import LLMClient
from app.llm.citation_guard import CitationError
from app.llm.deps import get_llm_client
from app.llm.narrative import NarrativeError, generate_narrative
from app.llm.number_guard import NumberInNarrativeError
from app.llm.solar import SolarApiError, SolarClientError
from app.schemas import (
    AnswerRequest,
    AnswerResponse,
    Company,
    CompanyDigest,
    CompanySearchResponse,
    FilingSource,
    HealthResponse,
    Language,
    MetricCard,
    NarrativeBlockedReason,
    NarrativeStatus,
    SearchHit,
    SearchRequest,
    SearchResponse,
)
from app.search.constants import SIMILARITY_THRESHOLD
from app.search.service import search_chunks

logger = logging.getLogger(__name__)

router = APIRouter()


def escape_ilike_literal(value: str) -> str:
    """Escape user text before placing it inside an ILIKE pattern."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)


@router.get("/companies", response_model=CompanySearchResponse)
async def search_companies(
    q: str = Query(default="", max_length=100),
    session: AsyncSession = Depends(get_db_session),
) -> CompanySearchResponse:
    """Case-insensitive substring search over companies (name/name_en/ticker).

    Mirrors app.search.service.search_chunks's session/query pattern
    (session-first, select().where(), await session.execute()). Empty ``q``
    matches every row. SQL wildcard characters in ``q`` are treated literally.
    """
    pattern = f"%{escape_ilike_literal(q)}%"
    stmt = select(CompanyModel).where(
        CompanyModel.name.ilike(pattern, escape="\\")
        | CompanyModel.name_en.ilike(pattern, escape="\\")
        | CompanyModel.ticker.ilike(pattern, escape="\\")
    )
    rows = (await session.execute(stmt)).scalars().all()
    items = [
        Company(
            id=str(row.id),
            name=row.name,
            name_en=row.name_en,
            ticker=row.ticker,
            market=row.market,
            source=row.source,
        )
        for row in rows
    ]
    return CompanySearchResponse(items=items, total=len(items))


def select_target_period(periods: Iterable[str]) -> str:
    """Deterministic "latest" period pick from a company's financials rows.

    ``financials.period`` strings share a ``"YYYY-<suffix>"`` shape (e.g.
    ``"2024-annual"`` from both ``persist.py`` and ``sec_ingest.py``), so the
    year prefix dominates lexicographic comparison -- ``max()`` reliably picks
    the highest fiscal year regardless of which source (dart/sec) produced
    each row. Empty input yields ``""`` (a company with no financials yet).
    Pure -- unit-tested offline.
    """
    return max(periods, default="")


def select_previous_period(periods: Iterable[str], target_period: str) -> str | None:
    """Pick the same reporting scope from exactly one fiscal year earlier.

    A two-year change is not YoY, and an annual fact is not the prior period for
    a quarterly fact. Canonical labels use ``YYYY-scope`` (with the legacy
    ``YYYYQn`` shape also accepted); unknown shapes fail closed to ``None``.
    """
    match = re.fullmatch(
        r"(?P<year>\d{4})(?P<separator>-?)(?P<scope>annual|Q[1-4]|H1)",
        target_period,
    )
    if match is None:
        return None
    previous_label = (
        f"{int(match.group('year')) - 1}"
        f"{match.group('separator')}{match.group('scope')}"
    )
    return previous_label if previous_label in set(periods) else None


def select_latest_filing_id(
    filing_ids: set[uuid.UUID], filings: Sequence[FilingModel]
) -> uuid.UUID:
    """Pick ONE filing id to anchor the target period's narrative/cache key.

    Normally exactly one filing backs a period's financials rows, returned
    directly. If more than one appears (unexpected: the target period's own
    rows disagree on their source filing), never silently guess -- pick
    deterministically by ``filed_at`` DESC (a filing with no parseable date
    sorts last, mirroring ``sec_ingest.select_target_filing``); the caller logs
    this branch. Pure given already-fetched ORM rows -- unit-tested offline.
    """
    if len(filing_ids) == 1:
        return next(iter(filing_ids))
    dated = [f for f in filings if f.filed_at is not None]
    return (max(dated, key=lambda f: f.filed_at) if dated else filings[0]).id


@router.get("/companies/{company_id}/digest", response_model=CompanyDigest)
async def get_company_digest(
    company_id: str,
    lang: Language = Query(default="ko"),
    session: AsyncSession = Depends(get_db_session),
    client: LLMClient = Depends(get_llm_client),
) -> CompanyDigest:
    """Real DB-backed digest for one company; 404 for unknown/malformed ids.

    Numbers come only from the structured ``financials`` table, never the LLM:
    each stored row selected by the digest presentation definitions becomes
    one MetricCard, linked to its Filing Source. summary_ko/summary_en are a
    prose-only, number-free business overview generated by
    :func:`app.digest_narrative.build_company_summary` (guarded on BOTH
    languages); both are returned regardless of ``lang`` (a display hint only),
    and both fall back to None when there is nothing to summarize or the number
    guard blocks the prose. A guard block, an unparseable body, or a Solar /
    network error degrades to null summaries rather than breaking the response
    (the figures track is always authoritative -- mirrors /answer's graceful
    ``blocked`` path); a retrieval/embedding failure still propagates, same as
    /search and /answer. ``yoy_delta_pct`` is computed only against the same
    reporting scope exactly one fiscal year earlier (see
    :func:`select_previous_period` / :func:`compute_yoy_deltas`) and is None when
    that period is absent, the prior value is missing/``<= 0``, or the two
    Financial Facts differ in reporting kind, duration, fiscal quarter,
    currency, unit, or scale.

    Multi-filing companies (e.g. 3 ingested fiscal years) can have several
    ``period`` values in ``financials``; the digest deterministically picks the
    lexicographically MAX period (``"YYYY-annual"`` / ``"YYYY-Q1"`` etc. sort
    correctly by year prefix) as "latest" and scopes metrics/period
    label/Filing Sources/narrative to that period only -- never an unordered DB
    scan.
    """
    try:
        company_uuid = uuid.UUID(company_id)
    except ValueError:
        logger.info("digest requested for malformed company_id=%s", company_id)
        raise HTTPException(status_code=404, detail="company not found") from None

    company = (
        await session.execute(
            select(CompanyModel).where(CompanyModel.id == company_uuid)
        )
    ).scalar_one_or_none()
    if company is None:
        logger.info("digest requested for unknown company_id=%s", company_id)
        raise HTTPException(status_code=404, detail="company not found")

    rows = await fetch_financials(session, company_id=company_uuid)
    target_period = select_target_period(row.period for row in rows)
    previous_period = select_previous_period(
        (row.period for row in rows), target_period
    )
    yoy_deltas = compute_yoy_deltas(rows, target_period, previous_period)
    period_rows = [row for row in rows if row.period == target_period]
    by_metric = {row.metric: row for row in period_rows}

    # Resolve every filing referenced by this period to its canonical, openable
    # Filing Source. Invalid metadata and its metric are omitted together: a
    # metric card without an openable source would violate the digest contract.
    filing_ids = {row.filing_id for row in period_rows if row.filing_id is not None}
    filings: Sequence[FilingModel] = []
    filing_sources_by_filing_id: dict[uuid.UUID, FilingSource] = {}
    latest_filing_id: uuid.UUID | None = None
    if filing_ids:
        filings = (
            await session.execute(
                select(FilingModel).where(FilingModel.id.in_(filing_ids))
            )
        ).scalars().all()
        for filing in filings:
            try:
                filing_sources_by_filing_id[filing.id] = filing_source_from_filing(
                    filing
                )
            except EvidenceIntegrityError as exc:
                logger.warning("digest omitted invalid Filing Source: %s", exc)
        resolved_filing_ids = {filing.id for filing in filings}
        if resolved_filing_ids:
            latest_filing_id = select_latest_filing_id(resolved_filing_ids, filings)
        if len(filing_ids) > 1:
            logger.warning(
                "digest: target_period=%s for company_id=%s spans multiple "
                "filings %s; picked filing_id=%s by filed_at desc",
                target_period,
                company_id,
                sorted(str(fid) for fid in filing_ids),
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

    # Prose-only KO/EN business overview (numbers forbidden + guarded). Any
    # summary-side failure (guard block, unparseable body, Solar/network error)
    # degrades to null summaries so the figures response always survives.
    summary_ko: str | None = None
    summary_en: str | None = None
    if latest_filing_id in filing_sources_by_filing_id:
        try:
            summary_ko, summary_en = await build_company_summary(
                session, client, company_uuid, latest_filing_id
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


@router.post("/search", response_model=SearchResponse)
async def search(
    request: SearchRequest,
    session: AsyncSession = Depends(get_db_session),
) -> SearchResponse:
    """Semantic search over filing_chunks (KO/EN cross-lingual via KURE-v1).

    Thin routing layer over app.search.service.search_chunks; every hit
    carries its citation anchor. Zero results is a valid 200, not an error.
    """
    results = await search_chunks(
        session,
        query=request.query,
        top_k=request.top_k,
        company_id=request.company_id,
    )
    items = [SearchHit.model_validate(r) for r in results]
    return SearchResponse(items=items, total=len(items))


@router.post("/answer", response_model=AnswerResponse)
async def answer(
    request: AnswerRequest,
    session: AsyncSession = Depends(get_db_session),
    client: LLMClient = Depends(get_llm_client),
) -> AnswerResponse:
    """Citation-grounded answer: prose narrative + authoritative figures.

    Combines the two tracks that keep the number policy intact -- semantic search
    feeds prose-only narrative (app.llm.narrative), while figures come straight
    from the structured filing API (app.figures.service), never through the LLM.

    Empty or too-weak retrieval SKIPS the LLM: with no grounding chunks, or a
    best-chunk similarity below SIMILARITY_THRESHOLD (query has no real match in
    the corpus), there is nothing to cite, and narrating over that would violate
    the "every claim carries a citation" contract -- so we return no Answer
    (narrative_status=no_results) rather than prompting. Figures are still
    returned. Zero/weak results is a valid 200.

    The number guard tripping (NumberInNarrativeError) or the external narrative
    service being unavailable is a graceful outcome: the figures track is still
    authoritative, so we suppress just the prose (answer=None,
    narrative_status=blocked) and return 200 with figures. Any CitationError is
    an Evidence Integrity Failure: fabricated or omitted evidence must never
    become a server error or reach the client as narrative, so it maps to
    ``blocked`` with figures preserved. Malformed narrative JSON and fabricated
    positional labels are handled the same way.
    """
    chunks = await search_chunks(
        session, query=request.query, company_id=request.company_id
    )
    rows = await fetch_financials(
        session, company_id=request.company_id, period=request.period
    )
    figures = build_figures(rows)

    if not chunks or chunks[0].score < SIMILARITY_THRESHOLD:
        return AnswerResponse(
            answer=None,
            figures=figures,
            citations=[],
            filing_sources=[],
            company_id=request.company_id,
            narrative_status=NarrativeStatus.no_results,
        )

    try:
        answer = await generate_narrative(
            client, question=request.query, chunks=chunks
        )
    except NumberInNarrativeError:
        logger.warning(
            "number guard blocked narrative for company_id=%s; returning figures only",
            request.company_id,
        )
        return AnswerResponse(
            answer=None,
            figures=figures,
            citations=[],
            filing_sources=[],
            company_id=request.company_id,
            narrative_status=NarrativeStatus.blocked,
            blocked_reason=NarrativeBlockedReason.number_guard,
        )
    except (SolarApiError, SolarClientError, httpx.HTTPError) as exc:
        logger.warning(
            "narrative service unavailable for company_id=%s (%s); "
            "returning figures only",
            request.company_id,
            type(exc).__name__,
        )
        return AnswerResponse(
            answer=None,
            figures=figures,
            citations=[],
            filing_sources=[],
            company_id=request.company_id,
            narrative_status=NarrativeStatus.blocked,
            blocked_reason=NarrativeBlockedReason.narrative_unavailable,
        )
    except (CitationError, NarrativeError) as exc:
        logger.warning(
            "citation guard rejected narrative for company_id=%s; "
            "returning evidence-integrity block (%s: %s)",
            request.company_id,
            type(exc).__name__,
            exc,
        )
        return AnswerResponse(
            answer=None,
            figures=figures,
            citations=[],
            filing_sources=[],
            company_id=request.company_id,
            narrative_status=NarrativeStatus.blocked,
            blocked_reason=NarrativeBlockedReason.evidence_integrity,
        )

    if not answer.answer_segments:
        logger.warning(
            "narrative generation produced no segments for company_id=%s; "
            "returning no_results",
            request.company_id,
        )
        return AnswerResponse(
            answer=None,
            figures=figures,
            citations=[],
            filing_sources=[],
            company_id=request.company_id,
            narrative_status=NarrativeStatus.no_results,
        )

    filing_ids = {chunk.filing_id for chunk in chunks}
    filings = (
        (
            await session.execute(
                select(FilingModel).where(FilingModel.id.in_(filing_ids))
            )
        )
        .scalars()
        .all()
    )
    try:
        evidence = resolve_evidence(answer, chunks, filings)
    except EvidenceIntegrityError as exc:
        logger.warning(
            "evidence integrity failure for company_id=%s: %s; "
            "returning figures only",
            request.company_id,
            exc,
        )
        return AnswerResponse(
            answer=None,
            figures=figures,
            citations=[],
            filing_sources=[],
            company_id=request.company_id,
            narrative_status=NarrativeStatus.blocked,
            blocked_reason=NarrativeBlockedReason.evidence_integrity,
        )

    return AnswerResponse(
        answer=answer,
        figures=figures,
        citations=evidence.citations,
        filing_sources=evidence.filing_sources,
        company_id=request.company_id,
        narrative_status=NarrativeStatus.ok,
    )
