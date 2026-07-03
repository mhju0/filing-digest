"""Pydantic v2 models for API CONTRACT v0.1.

All JSON fields are snake_case. Principle: numbers come only from structured
APIs (DART/SEC structured data); the LLM narrates only; every claim carries a
citation.
"""

import logging
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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
    summary_ko: str
    summary_en: str
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
