"""Source-independent value objects for a complete Normalized Filing."""

import datetime
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.financials.vocabulary import PeriodKind, ReportedMetric


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
class ReportingPeriod:
    """The temporal scope of a Financial Fact plus its presentation label.

    Exact dates remain absent when the regulatory source does not disclose them;
    they are never inferred from a label. A duration range is useful only when
    both endpoints are known, so a half-known range is rejected.
    """

    label: str
    kind: PeriodKind
    start_date: datetime.date | None = None
    end_date: datetime.date | None = None

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("Reporting Period label must not be blank")
        if self.kind is PeriodKind.instant and self.start_date is not None:
            raise ValueError("an instant Reporting Period cannot have a start date")
        if self.kind is PeriodKind.duration and (
            (self.start_date is None) != (self.end_date is None)
        ):
            raise ValueError("duration start_date and end_date must both be provided")
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.start_date > self.end_date
        ):
            raise ValueError("Reporting Period start_date must not follow end_date")


@dataclass(frozen=True)
class FinancialFact:
    """One directly reported value in a Normalized Filing snapshot."""

    metric: ReportedMetric
    period: ReportingPeriod
    value: Decimal
    unit: str
    currency: str | None
    scale: int = 1

    def __post_init__(self) -> None:
        if isinstance(self.value, float):
            raise TypeError("Financial Fact value must not be a float")
        if not isinstance(self.value, Decimal):
            object.__setattr__(self, "value", Decimal(self.value))
        if not self.unit.strip():
            raise ValueError("Financial Fact unit must not be blank")
        if self.scale < 1:
            raise ValueError("Financial Fact scale must be positive")


@dataclass(frozen=True)
class FilingChunk:
    """A bounded prose excerpt that may support a Citation."""

    chunk_index: int
    content: str
    metadata: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.chunk_index < 0:
            raise ValueError("Filing Chunk index must not be negative")
        if not self.content.strip():
            raise ValueError("Filing Chunk content must not be blank")


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
