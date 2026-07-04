"""API CONTRACT v0.1 endpoints, served from in-memory stub data (Phase 1).

Principle: numbers come only from structured APIs (DART/SEC structured data);
the LLM narrates only; every claim carries a citation.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import stub_data
from app.db.session import get_db_session
from app.figures.service import build_figures, fetch_financials
from app.llm.answer import Answer
from app.llm.base import LLMClient
from app.llm.deps import get_llm_client
from app.llm.narrative import generate_narrative
from app.schemas import (
    AnswerRequest,
    AnswerResponse,
    ChatRequest,
    ChatResponse,
    CompanyDigest,
    CompanySearchResponse,
    HealthResponse,
    IngestRequest,
    IngestResponse,
    Language,
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
def search_companies(q: str = Query(default="")) -> CompanySearchResponse:
    items = stub_data.search_companies(q)
    return CompanySearchResponse(items=items, total=len(items))


@router.get("/companies/{company_id}/digest", response_model=CompanyDigest)
def get_company_digest(
    company_id: str, lang: Language = Query(default="ko")
) -> CompanyDigest:
    """Return the deterministic stub digest; 404 for unknown company ids.

    `lang` is accepted per the contract (default "ko"); the digest always
    contains both summary_ko and summary_en, so in Phase 1 it acts as a
    display hint only.
    """
    digest = stub_data.build_digest(company_id)
    if digest is None:
        logger.info("digest requested for unknown company_id=%s", company_id)
        raise HTTPException(status_code=404, detail="company not found")
    return digest


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
    citation" (CLAUDE.md) -- so we return an empty Answer rather than prompting.
    Figures are still returned in that case. Zero results is a valid 200.

    Guard violations (NarrativeError / CitationError / NumberInNarrativeError)
    propagate as a 500 -- not caught here (structured error bodies are a later
    step).
    """
    chunks = await search_chunks(
        session, query=request.query, company_id=request.company_id
    )
    rows = await fetch_financials(
        session, company_id=request.company_id, period=request.period
    )
    figures = build_figures(rows)

    if not chunks:
        answer = Answer(answer_segments=[])
    else:
        answer = await generate_narrative(
            client, question=request.query, chunks=chunks
        )

    return AnswerResponse(
        answer=answer, figures=figures, company_id=request.company_id
    )
