"""API CONTRACT v0.1 endpoints, served from in-memory stub data (Phase 1).

Principle: numbers come only from structured APIs (DART/SEC structured data);
the LLM narrates only; every claim carries a citation.
"""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import stub_data
from app.db.models import Company as CompanyModel
from app.db.models import Filing as FilingModel
from app.db.session import get_db_session
from app.figures.service import build_figures, fetch_financials
from app.llm.base import LLMClient
from app.llm.deps import get_llm_client
from app.llm.narrative import generate_narrative
from app.llm.number_guard import NumberInNarrativeError
from app.schemas import (
    AnswerRequest,
    AnswerResponse,
    ChatRequest,
    ChatResponse,
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


@router.get("/companies/{company_id}/digest", response_model=CompanyDigest)
async def get_company_digest(
    company_id: str,
    lang: Language = Query(default="ko"),
    session: AsyncSession = Depends(get_db_session),
) -> CompanyDigest:
    """Real DB-backed digest for one company; 404 for unknown/malformed ids.

    Numbers come only from the structured ``financials`` table, never the LLM
    (CLAUDE.md): each stored row whose metric is a contract ``MetricKey`` becomes
    one MetricCard, linked to its filing's Citation. summary_ko/summary_en are
    None in this MVP -- the narrative pipeline lives on /answer, not here.
    ``yoy_delta_pct`` is None because only a single period is stored, so there is
    nothing to compare against. ``lang`` is accepted per the contract but the
    payload carries both label languages, so it stays a display hint only.
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
    by_metric = {row.metric: row for row in rows}

    metrics = [
        MetricCard(
            key=key,
            label_ko=labels["ko"],
            label_en=labels["en"],
            value=float(row.value),
            unit=row.unit,
            yoy_delta_pct=None,
            source=row.source,
            citation_id=str(row.filing_id) if row.filing_id else None,
        )
        for key, labels in _DIGEST_METRIC_LABELS.items()
        if (row := by_metric.get(key)) is not None
    ]

    # One Citation per filing referenced by the stored financials.
    filing_ids = {row.filing_id for row in rows if row.filing_id is not None}
    citations: list[Citation] = []
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

    return CompanyDigest(
        company_id=str(company.id),
        company_name=company.name,
        period=next((row.period for row in rows), ""),
        metrics=metrics,
        summary_ko=None,
        summary_en=None,
        citations=citations,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    return stub_data.build_chat_response(
        company_id=request.company_id,
        question=request.question,
        language=request.language,
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

    Empty retrieval SKIPS the LLM: with no grounding chunks there is nothing to
    cite, and narrating over zero sources would violate "every claim carries a
    citation" (CLAUDE.md) -- so we return no Answer (narrative_status=no_results)
    rather than prompting. Figures are still returned. Zero results is a valid 200.

    The number guard tripping (NumberInNarrativeError) is a graceful outcome, not
    a bug: the figures track is always authoritative, so we suppress just the
    prose (answer=None, narrative_status=blocked) and still return 200 with
    figures. The other guards (NarrativeError / CitationError) and FigureError
    signal a broken contract, so they are NOT caught here and propagate as 500.
    """
    chunks = await search_chunks(
        session, query=request.query, company_id=request.company_id
    )
    rows = await fetch_financials(
        session, company_id=request.company_id, period=request.period
    )
    figures = build_figures(rows)

    if not chunks:
        return AnswerResponse(
            answer=None,
            figures=figures,
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
            company_id=request.company_id,
            narrative_status=NarrativeStatus.blocked,
        )

    return AnswerResponse(
        answer=answer,
        figures=figures,
        company_id=request.company_id,
        narrative_status=NarrativeStatus.ok,
    )
