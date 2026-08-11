"""Build guarded narrative answers with authoritative structured figures."""

import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Filing as FilingModel
from app.evidence import EvidenceIntegrityError, resolve_evidence
from app.figures.service import build_figures, fetch_financials
from app.llm.base import LLMClient
from app.llm.citation_guard import CitationError
from app.llm.narrative import NarrativeError, generate_narrative
from app.llm.number_guard import NumberInNarrativeError
from app.llm.solar import SolarApiError, SolarClientError
from app.schemas import (
    AnswerRequest,
    AnswerResponse,
    Figure,
    NarrativeBlockedReason,
    NarrativeStatus,
)
from app.search.constants import SIMILARITY_THRESHOLD
from app.search.service import search_chunks

logger = logging.getLogger(__name__)


def _answer_without_narrative(
    request: AnswerRequest,
    figures: list[Figure],
    status: NarrativeStatus,
    blocked_reason: NarrativeBlockedReason | None = None,
) -> AnswerResponse:
    """Build the shared figures-only response for unavailable narratives."""
    return AnswerResponse(
        answer=None,
        figures=figures,
        citations=[],
        filing_sources=[],
        company_id=request.company_id,
        narrative_status=status,
        blocked_reason=blocked_reason,
    )


async def build_answer_response(
    session: AsyncSession,
    client: LLMClient,
    request: AnswerRequest,
) -> AnswerResponse:
    """Answer one question without allowing generated prose to own figures."""
    chunks = await search_chunks(
        session, query=request.query, company_id=request.company_id
    )
    rows = await fetch_financials(
        session, company_id=request.company_id, period=request.period
    )
    figures = build_figures(rows)

    if not chunks or chunks[0].score < SIMILARITY_THRESHOLD:
        return _answer_without_narrative(
            request,
            figures,
            NarrativeStatus.no_results,
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
        return _answer_without_narrative(
            request,
            figures,
            NarrativeStatus.blocked,
            NarrativeBlockedReason.number_guard,
        )
    except (SolarApiError, SolarClientError, httpx.HTTPError) as exc:
        logger.warning(
            "narrative service unavailable for company_id=%s (%s); "
            "returning figures only",
            request.company_id,
            type(exc).__name__,
        )
        return _answer_without_narrative(
            request,
            figures,
            NarrativeStatus.blocked,
            NarrativeBlockedReason.narrative_unavailable,
        )
    except (CitationError, NarrativeError) as exc:
        logger.warning(
            "citation guard rejected narrative for company_id=%s; "
            "returning evidence-integrity block (%s: %s)",
            request.company_id,
            type(exc).__name__,
            exc,
        )
        return _answer_without_narrative(
            request,
            figures,
            NarrativeStatus.blocked,
            NarrativeBlockedReason.evidence_integrity,
        )

    if not answer.answer_segments:
        logger.warning(
            "narrative generation produced no segments for company_id=%s; "
            "returning no_results",
            request.company_id,
        )
        return _answer_without_narrative(
            request,
            figures,
            NarrativeStatus.no_results,
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
        return _answer_without_narrative(
            request,
            figures,
            NarrativeStatus.blocked,
            NarrativeBlockedReason.evidence_integrity,
        )

    return AnswerResponse(
        answer=answer,
        figures=figures,
        citations=evidence.citations,
        filing_sources=evidence.filing_sources,
        company_id=request.company_id,
        narrative_status=NarrativeStatus.ok,
    )
