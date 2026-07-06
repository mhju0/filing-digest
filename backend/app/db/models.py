"""SQLAlchemy 2.0 ORM models (Mapped[] style).

Must stay in exact sync with backend/db/init.sql (DB SCHEMA v0.1).
Schema is applied via init.sql (docker-entrypoint-initdb.d) -- no Alembic.

Notes:
- filing_chunks column is named "meta" because 'metadata' is a reserved
  attribute name on SQLAlchemy Declarative models.
- embedding vector(1024) is [Verified]: KURE-v1 (nlpai-lab/KURE-v1) dense
  dimension (HuggingFace config.json hidden_size=1024).
- TODO(Phase 2): vector index (hnsw or ivfflat) on filing_chunks.embedding --
  do not create it now; it cannot be tuned without real data.
"""

import datetime
import decimal
import logging
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

logger = logging.getLogger(__name__)

# Matches vector(1024) in init.sql. [Verified] -- see module docstring.
EMBEDDING_DIM = 1024


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (
        CheckConstraint("source IN ('dart', 'sec')", name="companies_source_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    name_en: Mapped[str | None] = mapped_column(Text)
    ticker: Mapped[str | None] = mapped_column(Text)
    market: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    dart_corp_code: Mapped[str | None] = mapped_column(Text, unique=True)
    sec_cik: Mapped[str | None] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class Filing(Base):
    __tablename__ = "filings"
    __table_args__ = (Index("idx_filings_company", "company_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    # DART 접수번호: DART filing의 자연키 / financials/document 조인키.
    # SEC filing은 없으므로 nullable. UNIQUE는 ON CONFLICT (rcept_no) 멱등 upsert의
    # inference target; NULL은 서로 distinct라 SEC row끼리는 충돌하지 않는다.
    rcept_no: Mapped[str | None] = mapped_column(Text, unique=True)
    # SEC accession number: SEC filing의 자연키. DART filing은 없으므로 nullable.
    # UNIQUE는 ON CONFLICT (sec_accession_no) 멱등 upsert의 inference target;
    # NULL은 서로 distinct라 DART row끼리는 충돌하지 않는다.
    sec_accession_no: Mapped[str | None] = mapped_column(Text, unique=True)
    filing_type: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    period: Mapped[str | None] = mapped_column(Text)
    filed_at: Mapped[datetime.date | None] = mapped_column(Date)
    url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class FilingChunk(Base):
    __tablename__ = "filing_chunks"
    __table_args__ = (
        UniqueConstraint(
            "filing_id", "chunk_index", name="filing_chunks_filing_id_chunk_index_key"
        ),
        # TODO(Phase 2): vector index (hnsw/ivfflat) on embedding -- needs data.
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    filing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("filings.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # [Verified] 1024 dims -- KURE-v1 dense dimension (see module docstring).
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    # Named "meta": 'metadata' is reserved on Declarative models.
    meta: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class Financial(Base):
    __tablename__ = "financials"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "period",
            "metric",
            "source",
            name="financials_company_id_period_metric_source_key",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    filing_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("filings.id", ondelete="SET NULL"),
    )
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    fiscal_quarter: Mapped[int | None] = mapped_column(Integer)
    period: Mapped[str] = mapped_column(Text, nullable=False)
    metric: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[decimal.Decimal] = mapped_column(Numeric(24, 4), nullable=False)
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
