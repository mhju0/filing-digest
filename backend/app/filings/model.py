"""Source-independent value objects for a complete Normalized Filing."""

import datetime
from dataclasses import dataclass
from enum import StrEnum

from app.financials.model import FinancialFact, ReportingPeriod


class RegulatorySource(StrEnum):
    """Regulatory system that owns a Company or Filing Identity."""

    dart = "dart"
    sec = "sec"


@dataclass(frozen=True)
class CompanyIdentity:
    """A regulator-scoped immutable company identifier."""

    source: RegulatorySource
    source_company_id: str

    def __post_init__(self) -> None:
        if not self.source_company_id.strip():
            raise ValueError("source_company_id must not be blank")


@dataclass(frozen=True)
class FilingIdentity:
    """A regulator-scoped immutable Corporate Filing identifier."""

    source: RegulatorySource
    source_filing_id: str

    def __post_init__(self) -> None:
        if not self.source_filing_id.strip():
            raise ValueError("source_filing_id must not be blank")

    @property
    def stable_id(self) -> str:
        """Stable transport-safe identity independent of a database UUID."""
        return f"{self.source.value}:{self.source_filing_id}"


@dataclass(frozen=True)
class RegulatedCompany:
    """Source-owned company metadata accompanying a Normalized Filing."""

    identity: CompanyIdentity
    name: str
    name_en: str | None = None
    ticker: str | None = None
    market: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Regulated Company name must not be blank")


@dataclass(frozen=True)
class FilingChunkLocation:
    """Available section coordinates for one Filing Chunk."""

    section_title: str | None = None
    section_order: int | None = None
    part_index: int | None = None

    def __post_init__(self) -> None:
        if self.section_order is not None and self.section_order < 0:
            raise ValueError("Filing Chunk section order must not be negative")
        if self.part_index is not None and self.part_index < 0:
            raise ValueError("Filing Chunk part index must not be negative")


@dataclass(frozen=True)
class FilingChunk:
    """A bounded prose excerpt and its available location."""

    chunk_index: int
    content: str
    location: FilingChunkLocation = FilingChunkLocation()

    def __post_init__(self) -> None:
        if self.chunk_index < 0:
            raise ValueError("Filing Chunk index must not be negative")
        if not self.content.strip():
            raise ValueError("Filing Chunk content must not be blank")
        if not isinstance(self.location, FilingChunkLocation):
            raise TypeError("Filing Chunk location must be a FilingChunkLocation")


@dataclass(frozen=True)
class NormalizedFiling:
    """A complete source-independent snapshot ready for atomic persistence."""

    company: RegulatedCompany
    identity: FilingIdentity
    filing_type: str
    title: str
    reporting_period: ReportingPeriod
    financial_facts: tuple[FinancialFact, ...] = ()
    filing_chunks: tuple[FilingChunk, ...] = ()
    filed_at: datetime.date | None = None
    url: str | None = None

    def __post_init__(self) -> None:
        if self.company.identity.source is not self.identity.source:
            raise ValueError(
                "Normalized Filing and Regulated Company must use the same regulatory source"
            )
        if not self.filing_type.strip():
            raise ValueError("filing_type must not be blank")
        if not self.title.strip():
            raise ValueError("Corporate Filing title must not be blank")
        fact_keys = [(fact.period.label, fact.metric) for fact in self.financial_facts]
        if len(fact_keys) != len(set(fact_keys)):
            raise ValueError("Normalized Filing contains a duplicate Financial Fact")
        chunk_indexes = [chunk.chunk_index for chunk in self.filing_chunks]
        if len(chunk_indexes) != len(set(chunk_indexes)):
            raise ValueError("Normalized Filing contains a duplicate Filing Chunk index")
