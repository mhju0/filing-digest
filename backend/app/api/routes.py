"""API CONTRACT v0.1 endpoints, served from in-memory stub data (Phase 1).

Principle: numbers come only from structured APIs (DART/SEC structured data);
the LLM narrates only; every claim carries a citation.
"""

import logging
import uuid

from fastapi import APIRouter, HTTPException, Query, status

from app import stub_data
from app.schemas import (
    ChatRequest,
    ChatResponse,
    CompanyDigest,
    CompanySearchResponse,
    HealthResponse,
    IngestRequest,
    IngestResponse,
    Language,
)

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
