"""Pydantic v2 models for API CONTRACT v0.3.

All JSON fields are snake_case. Principle: numbers come only from structured
APIs (DART/SEC structured data); the LLM narrates only; every claim carries a
citation.
"""

import logging
import uuid
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app import __version__
from app.financials import DerivedMetric, PeriodKind, ReportedMetric
from app.llm.answer import Answer
from app.search.constants import DEFAULT_TOP_K, MAX_TOP_K

logger = logging.getLogger(__name__)

Source = Literal["dart", "sec"]
Market = Literal["KOSPI", "KOSDAQ", "NYSE", "NASDAQ"]
Language = Literal["ko", "en"]
MetricKey = ReportedMetric | DerivedMetric
MAX_CITATION_EXCERPT_CHARS = 1_200


class HealthResponse(BaseModel):
    """GET /health response."""

    status: Literal["ok"] = "ok"
    version: str = __version__


class Company(BaseModel):
    id: str
    name: str
    name_en: str | None = None
    ticker: str | None = None
    market: Market | None = None
    source: Source


class CompanySearchResponse(BaseModel):
    """GET /companies response."""

    items: list[Company]
    total: int


class CitationAnchor(BaseModel):
    """Location of one Filing Chunk within its Corporate Filing."""

    section_title: str | None = None
    section_order: int | None = Field(default=None, ge=0)
    part_index: int | None = Field(default=None, ge=0)
    chunk_index: int = Field(ge=0)


class Citation(BaseModel):
    """Claim-level evidence pointing to one exact Filing Chunk."""

    id: str = Field(min_length=1)
    filing_source_id: str = Field(min_length=1)
    excerpt: str = Field(min_length=1, max_length=MAX_CITATION_EXCERPT_CHARS)
    anchor: CitationAnchor


class FilingSource(BaseModel):
    """Deduplicated, openable representation of one Corporate Filing."""

    id: str = Field(min_length=1)
    source: Source
    source_filing_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    filed_at: str | None = None  # ISO date, e.g. "2026-04-30"


class MetricCard(BaseModel):
    key: MetricKey
    label_ko: str
    label_en: str
    value: float | None = None
    unit: str
    yoy_delta_pct: float | None = None
    source: Source
    filing_source_id: str = Field(min_length=1)


class CompanyDigest(BaseModel):
    """GET /companies/{company_id}/digest response."""

    company_id: str
    company_name: str
    period: str  # e.g. "2026Q1"
    metrics: list[MetricCard]
    # A prose-only, number-free KO/EN business overview (app.digest_narrative),
    # guarded on both languages. Nullable: falls back to None when there is
    # nothing to summarize or the number guard blocks the prose, so /digest can
    # still return its authoritative figures without prose.
    summary_ko: str | None = None
    summary_en: str | None = None
    filing_sources: list[FilingSource]
    generated_at: str  # ISO 8601


class SearchRequest(BaseModel):
    """POST /search request body."""

    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=DEFAULT_TOP_K, ge=1, le=MAX_TOP_K)
    company_id: uuid.UUID | None = None


class SearchHit(BaseModel):
    """One semantic search hit -- mirrors app.search.service.SearchResult 1:1
    so every field needed to trace a claim back to its source filing/section/
    paragraph is exposed (citation anchor: rcept_no, section_title,
    section_order, part_index, chunk_index, filing_id).
    """

    model_config = ConfigDict(from_attributes=True)

    chunk_id: uuid.UUID
    filing_id: uuid.UUID
    text: str
    score: float
    rcept_no: str | None = None
    section_title: str | None = None
    section_order: int | None = None
    part_index: int | None = None
    chunk_index: int


class SearchResponse(BaseModel):
    """POST /search response."""

    items: list[SearchHit]
    total: int


class Figure(BaseModel):
    """One authoritative financial figure pulled from the structured filing API.

    Mirror of :class:`SearchHit`'s tone for the figures track: where a search hit
    anchors a NARRATED claim to its source, a Figure IS the number -- pulled
    deterministically from ``financials`` (never through the LLM), carrying its
    own ``filing_id`` so each value is a self-contained citation anchor.

    ``value`` is a :class:`~decimal.Decimal`, never ``float`` -- ``financials.value``
    is ``numeric(24,4)`` and its full precision (e.g. EPS ``2131.0000``, KRW
    revenue in the hundreds of trillions) must survive intact.
    """

    model_config = ConfigDict(from_attributes=True)

    metric: ReportedMetric
    value: Decimal
    unit: str
    currency: str | None = None
    period: str
    period_kind: PeriodKind
    fiscal_year: int
    fiscal_quarter: int | None = None
    filing_id: uuid.UUID


class AnswerRequest(BaseModel):
    """POST /answer request body.

    Numbers are anchored per company, so ``company_id`` is required.
    ``period`` narrows the figures scope; None returns the whole company scope.
    """

    query: str = Field(min_length=1, max_length=1_000)
    company_id: uuid.UUID
    period: str | None = Field(default=None, max_length=32)


class NarrativeStatus(StrEnum):
    """Disposition of the ``answer`` track in an :class:`AnswerResponse`.

    ``figures`` is always authoritative and always returned; only the prose
    narrative can be withheld. ``ok`` -- narrative generated. ``no_results`` --
    empty retrieval, nothing to cite over, so no narrative was attempted.
    ``blocked`` -- a guard, evidence-integrity check, or narrative dependency
    withheld the prose, while figures survive (graceful, not a 500).
    """

    ok = "ok"
    blocked = "blocked"
    no_results = "no_results"


class NarrativeBlockedReason(StrEnum):
    """Why an otherwise useful answer narrative was withheld."""

    number_guard = "number_guard"
    narrative_unavailable = "narrative_unavailable"
    evidence_integrity = "evidence_integrity"


class AnswerResponse(BaseModel):
    """POST /answer response: citation-bearing prose + authoritative figures.

    The two tracks stay separate by design: ``answer`` carries LLM narrative
    (prose only, every segment cited, no numbers), while ``figures`` carries the
    numbers pulled deterministically from the structured filing API -- never
    through the LLM.

    ``answer`` is nullable: when ``narrative_status`` is ``no_results`` or
    ``blocked`` there is no prose to return, but ``figures`` still is.

    ``citations`` resolves every cited chunk id to a bounded evidence excerpt
    and location anchor. ``filing_sources`` separately exposes each canonical,
    openable Corporate Filing in first-citation order. Segment citation ids stay
    chunk-level and unchanged.
    """

    answer: Answer | None
    figures: list[Figure]
    citations: list[Citation]
    filing_sources: list[FilingSource]
    company_id: uuid.UUID
    narrative_status: NarrativeStatus
    blocked_reason: NarrativeBlockedReason | None = None
