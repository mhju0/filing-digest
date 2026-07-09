"""API CONTRACT v0.1 endpoints, served from in-memory stub data (Phase 1).

Principle: numbers come only from structured APIs (DART/SEC structured data);
the LLM narrates only; every claim carries a citation.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Iterable, Sequence

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Company as CompanyModel
from app.db.models import Filing as FilingModel
from app.db.models import Financial
from app.db.session import get_db_session
from app.digest_narrative import DigestNarrativeError, build_company_summary
from app.figures.service import build_figures, fetch_financials
from app.llm.base import LLMClient
from app.llm.citation_guard import CitationError
from app.llm.deps import get_llm_client
from app.llm.narrative import generate_narrative
from app.llm.number_guard import NumberInNarrativeError
from app.llm.solar import SolarApiError, SolarClientError
from app.schemas import (
    AnswerRequest,
    AnswerResponse,
    Citation,
    Company,
    CompanyDigest,
    CompanySearchResponse,
    HealthResponse,
    IngestRequest,
    IngestResponse,
    Language,
    MetricCard,
    NarrativeStatus,
    SearchHit,
    SearchRequest,
    SearchResponse,
)
from app.search.constants import SIMILARITY_THRESHOLD
from app.search.service import search_chunks

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", version="0.1.0")


@router.get("/companies", response_model=CompanySearchResponse)
async def search_companies(
    q: str = Query(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> CompanySearchResponse:
    """Case-insensitive substring search over companies (name/name_en/ticker).

    Mirrors app.search.service.search_chunks's session/query pattern
    (session-first, select().where(), await session.execute()). Empty ``q``
    matches every row (``ilike("%%")``), same as the stub it replaces.
    """
    pattern = f"%{q}%"
    stmt = select(CompanyModel).where(
        CompanyModel.name.ilike(pattern)
        | CompanyModel.name_en.ilike(pattern)
        | CompanyModel.ticker.ilike(pattern)
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


# Static KO/EN labels for the four contract MetricKey rows a digest surfaces.
# DB financials also carry eps_diluted / net_income_attributable, which are NOT
# MetricKey values (schemas.py) -- they are intentionally omitted here, not
# mapped. Iteration order fixes the card order (revenue -> operating_income ->
# net_income -> eps). operating_margin is a MetricKey but is not stored, so it
# simply never appears.
_DIGEST_METRIC_LABELS: dict[str, dict[str, str]] = {
    "revenue": {"ko": "매출액", "en": "Revenue"},
    "operating_income": {"ko": "영업이익", "en": "Operating Income"},
    "net_income": {"ko": "당기순이익", "en": "Net Income"},
    "eps": {"ko": "주당순이익", "en": "EPS"},
}


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
    """Deterministic "previous year" period pick for YoY comparison.

    Same lexicographic-max logic as :func:`select_target_period`, applied to
    every period except ``target_period`` itself. ``None`` when no other
    period exists (e.g. Samsung's single ingested fiscal year) -- callers
    treat that as "nothing to compare against" rather than raising. Pure --
    unit-tested offline.
    """
    others = [p for p in periods if p != target_period]
    return max(others, default=None)


def compute_yoy_deltas(
    rows: Sequence[Financial], target_period: str, previous_period: str | None
) -> dict[str, float | None]:
    """YoY %% change per metric, ``target_period`` vs ``previous_period``.

    One entry per metric present in ``target_period``. A metric is ``None``
    rather than raising when: there is no ``previous_period``, the previous
    period has no row for that metric, or the previous value is ``<= 0``
    (division-by-zero / sign-flip guard -- a negative-to-positive swing has no
    meaningful percentage). Pure given already-fetched ORM rows -- unit-tested
    offline.
    """
    target_metrics = {row.metric for row in rows if row.period == target_period}
    if previous_period is None:
        return dict.fromkeys(target_metrics)

    current_by_metric = {
        row.metric: row.value for row in rows if row.period == target_period
    }
    previous_by_metric = {
        row.metric: row.value for row in rows if row.period == previous_period
    }

    deltas: dict[str, float | None] = {}
    for metric in target_metrics:
        previous_value = previous_by_metric.get(metric)
        if previous_value is None or previous_value <= 0:
            deltas[metric] = None
            continue
        current_value = current_by_metric[metric]
        deltas[metric] = float((current_value - previous_value) / previous_value * 100)
    return deltas


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

    Numbers come only from the structured ``financials`` table, never the LLM
    (CLAUDE.md): each stored row whose metric is a contract ``MetricKey`` becomes
    one MetricCard, linked to its filing's Citation. summary_ko/summary_en are a
    prose-only, number-free business overview generated by
    :func:`app.digest_narrative.build_company_summary` (guarded on BOTH
    languages); both are returned regardless of ``lang`` (a display hint only),
    and both fall back to None when there is nothing to summarize or the number
    guard blocks the prose. A guard block, an unparseable body, or a Solar /
    network error degrades to null summaries rather than breaking the response
    (the figures track is always authoritative -- mirrors /answer's graceful
    ``blocked`` path); a retrieval/embedding failure still propagates, same as
    /search and /answer. ``yoy_delta_pct`` is computed against the
    lexicographically next-highest period (see :func:`select_previous_period`
    / :func:`compute_yoy_deltas`) and is None when no other period is stored
    (single-filing companies) or the prior value is missing/``<= 0``.

    Multi-filing companies (e.g. 3 ingested fiscal years) can have several
    ``period`` values in ``financials``; the digest deterministically picks the
    lexicographically MAX period (``"YYYY-annual"`` / ``"YYYY-Q1"`` etc. sort
    correctly by year prefix) as "latest" and scopes metrics/period
    label/citations/narrative to that period only -- never an unordered DB scan.
    """
    try:
        company_uuid = uuid.UUID(company_id)
    except ValueError:
        logger.info("digest requested for malformed company_id=%s", company_id)
        raise HTTPException(status_code=404, detail="company not found")

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

    metrics = [
        MetricCard(
            key=key,
            label_ko=labels["ko"],
            label_en=labels["en"],
            value=float(row.value),
            unit=row.unit,
            yoy_delta_pct=yoy_deltas.get(key),
            source=row.source,
            citation_id=str(row.filing_id) if row.filing_id else None,
        )
        for key, labels in _DIGEST_METRIC_LABELS.items()
        if (row := by_metric.get(key)) is not None
    ]

    # One Citation per filing referenced by the target period's financials.
    filing_ids = {row.filing_id for row in period_rows if row.filing_id is not None}
    citations: list[Citation] = []
    latest_filing_id: uuid.UUID | None = None
    if filing_ids:
        filings = (
            await session.execute(
                select(FilingModel).where(FilingModel.id.in_(filing_ids))
            )
        ).scalars().all()
        citations = [
            Citation(
                id=str(f.id),
                source=f.source,
                title=f.title,
                url=f.url or "",
                excerpt=None,
                filed_at=f.filed_at.isoformat() if f.filed_at else None,
            )
            for f in filings
        ]
        latest_filing_id = select_latest_filing_id(filing_ids, filings)
        if len(filing_ids) > 1:
            logger.warning(
                "digest: target_period=%s for company_id=%s spans multiple "
                "filings %s; picked filing_id=%s by filed_at desc",
                target_period,
                company_id,
                sorted(str(fid) for fid in filing_ids),
                latest_filing_id,
            )

    # Prose-only KO/EN business overview (numbers forbidden + guarded). Any
    # summary-side failure (guard block, unparseable body, Solar/network error)
    # degrades to null summaries so the figures response always survives.
    summary_ko: str | None = None
    summary_en: str | None = None
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
        citations=citations,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


@router.post(
    "/ingest",
    response_model=IngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def ingest(request: IngestRequest) -> IngestResponse:
    """Accept an ingest job (stub: no worker yet, TODO(Phase 2))."""
    job_id = str(uuid.uuid4())
    logger.info(
        "ingest queued (stub): job_id=%s company_id=%s source=%s",
        job_id,
        request.company_id,
        request.source,
    )
    return IngestResponse(job_id=job_id, status="queued")


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
    "every claim carries a citation" (CLAUDE.md) -- so we return no Answer
    (narrative_status=no_results) rather than prompting. Figures are still
    returned. Zero/weak results is a valid 200.

    The number guard tripping (NumberInNarrativeError) is a graceful outcome, not
    a bug: the figures track is always authoritative, so we suppress just the
    prose (answer=None, narrative_status=blocked) and still return 200 with
    figures. A CitationError where every violation is kind="empty" means the LLM
    had nothing groundable to say (e.g. the corpus lacks the asked-for period) --
    that is the same "nothing to cite" situation as the retrieval-threshold gate
    above, so it also maps to narrative_status=no_results. A CitationError with
    ANY "unknown" (fabricated) citation id is a real hallucination signal and is
    re-raised as-is. NarrativeError and FigureError signal a broken contract, so
    they are NOT caught here and propagate as 500.
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
            company_id=request.company_id,
            narrative_status=NarrativeStatus.blocked,
        )
    except CitationError as exc:
        if any(v.kind != "empty" for v in exc.violations):
            raise
        logger.warning(
            "citation guard found no groundable segments for company_id=%s; "
            "returning no_results",
            request.company_id,
        )
        return AnswerResponse(
            answer=None,
            figures=figures,
            citations=[],
            company_id=request.company_id,
            narrative_status=NarrativeStatus.no_results,
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
            company_id=request.company_id,
            narrative_status=NarrativeStatus.no_results,
        )

    # Resolve each cited chunk id (segment anchor, kept as-is) to its source
    # filing's metadata, batched (filing_id IN (...)) same as /digest above.
    cited_chunk_ids = {
        chunk_id for segment in answer.answer_segments for chunk_id in segment.citations
    }
    chunks_by_id = {str(chunk.chunk_id): chunk for chunk in chunks}
    filing_ids = {chunks_by_id[cid].filing_id for cid in cited_chunk_ids}
    citations: list[Citation] = []
    if filing_ids:
        filings = (
            await session.execute(
                select(FilingModel).where(FilingModel.id.in_(filing_ids))
            )
        ).scalars().all()
        filings_by_id = {f.id: f for f in filings}
        citations = [
            Citation(
                id=cid,
                source=(filing := filings_by_id[chunks_by_id[cid].filing_id]).source,
                title=filing.title,
                url=filing.url or "",
                excerpt=None,
                filed_at=filing.filed_at.isoformat() if filing.filed_at else None,
            )
            for cid in cited_chunk_ids
        ]

    return AnswerResponse(
        answer=answer,
        figures=figures,
        citations=citations,
        company_id=request.company_id,
        narrative_status=NarrativeStatus.ok,
    )
