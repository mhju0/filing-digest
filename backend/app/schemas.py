"""Pydantic v2 models for API CONTRACT v0.1.

All JSON fields are snake_case. Principle: numbers come only from structured
APIs (DART/SEC structured data); the LLM narrates only; every claim carries a
citation.
"""

import logging
import uuid
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.llm.answer import Answer
from app.search.constants import DEFAULT_TOP_K, MAX_TOP_K

logger = logging.getLogger(__name__)

Source = Literal["dart", "sec"]
Market = Literal["KOSPI", "KOSDAQ", "NYSE", "NASDAQ"]
Language = Literal["ko", "en"]
MetricKey = Literal[
    "revenue",
    "operating_income",
    "net_income",
    "eps",
    "operating_margin",
]


class HealthResponse(BaseModel):
    """GET /health response."""

    status: Literal["ok"] = "ok"
    version: str = "0.1.0"


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


class Citation(BaseModel):
    id: str
    source: Source
    title: str
    url: str
    excerpt: str | None = None
    filed_at: str | None = None  # ISO date, e.g. "2026-04-30"


class MetricCard(BaseModel):
    key: MetricKey
    label_ko: str
    label_en: str
    value: float | None = None
    unit: str
    yoy_delta_pct: float | None = None
    source: Source
    citation_id: str | None = None


class CompanyDigest(BaseModel):
    """GET /companies/{company_id}/digest response."""

    company_id: str
    company_name: str
    period: str  # e.g. "2026Q1"
    metrics: list[MetricCard]
    # None in the MVP DB-backed digest: the narrative pipeline lives on /answer,
    # not here. Kept nullable so /digest can return figures without prose.
    summary_ko: str | None = None
    summary_en: str | None = None
    citations: list[Citation]
    generated_at: str  # ISO 8601


class ChatRequest(BaseModel):
    """POST /chat request body."""

    company_id: str | None = None
    question: str = Field(min_length=1)
    language: Language = "ko"


class ChatResponse(BaseModel):
    """POST /chat response."""

    answer: str
    language: Language
    citations: list[Citation]


class IngestRequest(BaseModel):
    """POST /ingest request body."""

    company_id: str
    source: Source
    filing_types: list[str] | None = None


class IngestResponse(BaseModel):
    """POST /ingest response (202 Accepted)."""

    job_id: str
    status: Literal["queued"] = "queued"


class SearchRequest(BaseModel):
    """POST /search request body."""

    query: str = Field(min_length=1)
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
    own ``filing_id`` so each value is a self-contained citation anchor
    (CLAUDE.md: "숫자는 구조화 filing API에서만 온다").

    ``value`` is a :class:`~decimal.Decimal`, never ``float`` -- ``financials.value``
    is ``numeric(24,4)`` and its full precision (e.g. EPS ``2131.0000``, KRW
    revenue in the hundreds of trillions) must survive intact.
    """

    model_config = ConfigDict(from_attributes=True)

    metric: str
    value: Decimal
    unit: str
    currency: str | None = None
    period: str
    fiscal_year: int
    fiscal_quarter: int | None = None
    filing_id: uuid.UUID


class AnswerRequest(BaseModel):
    """POST /answer request body.

    Numbers are anchored per company, so ``company_id`` is required (unlike
    ``ChatRequest``'s optional one). ``period`` narrows the figures scope; None
    returns the whole company scope.
    """

    query: str = Field(min_length=1)
    company_id: uuid.UUID
    period: str | None = None


class NarrativeStatus(str, Enum):
    """Disposition of the ``answer`` track in an :class:`AnswerResponse`.

    ``figures`` is always authoritative and always returned; only the prose
    narrative can be withheld. ``ok`` -- narrative generated. ``no_results`` --
    empty retrieval, nothing to cite over, so no narrative was attempted.
    ``blocked`` -- the number guard tripped on the generated prose, so the
    narrative is suppressed while figures survive (graceful, not a 500).
    """

    ok = "ok"
    blocked = "blocked"
    no_results = "no_results"


class AnswerResponse(BaseModel):
    """POST /answer response: citation-bearing prose + authoritative figures.

    The two tracks stay separate by design: ``answer`` carries LLM narrative
    (prose only, every segment cited, no numbers), while ``figures`` carries the
    numbers pulled deterministically from the structured filing API -- never
    through the LLM (CLAUDE.md: "숫자는 구조화 filing API에서만 온다").

    ``answer`` is nullable: when ``narrative_status`` is ``no_results`` or
    ``blocked`` there is no prose to return, but ``figures`` still is.
    """

    answer: Answer | None
    figures: list[Figure]
    company_id: uuid.UUID
    narrative_status: NarrativeStatus
