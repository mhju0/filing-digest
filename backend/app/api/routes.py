"""API CONTRACT v0.3 endpoints, backed by the real database.

Principle: numbers come only from structured APIs (DART/SEC structured data);
the LLM narrates only; every claim carries a citation.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.answers import build_answer_response
from app.db.models import Company as CompanyModel
from app.db.session import get_db_session
from app.digests import CompanyNotFoundError, build_company_digest
from app.llm.base import LLMClient
from app.llm.deps import get_llm_client
from app.schemas import (
    AnswerRequest,
    AnswerResponse,
    Company,
    CompanyDigest,
    CompanySearchResponse,
    HealthResponse,
    Language,
    SearchHit,
    SearchRequest,
    SearchResponse,
)
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


@router.get("/companies/{company_id}/digest", response_model=CompanyDigest)
async def get_company_digest(
    company_id: str,
    lang: Language = Query(default="ko"),
    session: AsyncSession = Depends(get_db_session),
    client: LLMClient = Depends(get_llm_client),
) -> CompanyDigest:
    """Translate the digest HTTP request into the company digest module."""
    del lang  # Both localized summaries are returned; this remains a display hint.
    try:
        company_uuid = uuid.UUID(company_id)
    except ValueError:
        logger.info("digest requested for malformed company_id=%s", company_id)
        raise HTTPException(status_code=404, detail="company not found") from None
    try:
        return await build_company_digest(session, client, company_uuid)
    except CompanyNotFoundError:
        logger.info("digest requested for unknown company_id=%s", company_id)
        raise HTTPException(status_code=404, detail="company not found") from None


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
    """Translate the answer HTTP request into the answer module."""
    return await build_answer_response(session, client, request)
