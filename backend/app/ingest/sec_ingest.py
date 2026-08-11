"""SEC adapter for the source-independent Normalized Filing seam.

The SEC adapter fetches and parses everything before building one complete
:class:`app.filings.model.NormalizedFiling`. The shared ingestion module owns
persistence and filing-scoped indexing after this adapter returns.

Design (mirrors the DART adapter; the differences are all SEC-specific):

1. **Numbers come only from companyfacts.** ``fetch_company_facts`` is the single
   source of figures (never the LLM/document); the facts are filtered to the
   chosen accession's *own-period* rows before they become ``financials`` rows.

2. **USD units, not KRW.** SEC facts use their own unit vocabulary while the
   adapter translates ``SecFinancialItem.value`` directly into canonical
   :class:`~app.financials.model.FinancialFact` objects.

3. **Chunks carry no rcept_no.** SEC filings have no DART receipt number, so
   ``chunk_document`` is called with ``rcept_no=None``; the citation anchor's
   provenance rides on ``filing_id -> filings.sec_accession_no`` instead (the
   accession is never stuffed into the DART-specific ``meta.rcept_no`` field --
   see :mod:`app.ingest.chunking`).

The :class:`SecFilingAdapter` interface returns a complete snapshot; database
row construction belongs solely to :mod:`app.filings.persistence`.
"""

import datetime
import logging
from dataclasses import dataclass

from app.clients.sec import (
    SecClient,
    SecCompanyMatch,
    SecFilingItem,
    SecFinancialItem,
    format_cik,
)
from app.clients.sec_document import extract_10k_prose
from app.filings.model import (
    CompanyIdentity,
    FilingChunk,
    FilingIdentity,
    NormalizedFiling,
    RegulatedCompany,
    RegulatorySource,
)
from app.financials.model import FinancialFact, ReportingPeriod
from app.financials.vocabulary import PeriodKind, ReportedMetric
from app.ingest.chunking import Chunk, chunk_document

logger = logging.getLogger(__name__)

# The one SEC form this step ingests (annual report). Mirrors the DART report
# codes: only 10-K is in scope; 10-Q and others are a future extension.
_FORM_10K = "10-K"

# SEC financials.unit / currency vocabulary. Parallels the DART UNIT_KRW /
# UNIT_KRW_PER_SHARE split (the `unit` column distinguishes absolute vs per-share
# so a reader never mistakes an EPS for an absolute amount); `currency` carries
# the ISO code.
UNIT_USD = "USD"  # absolute USD (revenue, operating_income, net_income)
UNIT_USD_PER_SHARE = "USD_PER_SHARE"  # USD per share (eps, eps_diluted)
CURRENCY_USD = "USD"

# Per-share metrics -> UNIT_USD_PER_SHARE; everything else -> UNIT_USD.
_EPS_METRICS = frozenset(
    {ReportedMetric.eps.value, ReportedMetric.eps_diluted.value}
)


class SecIngestError(RuntimeError):
    """Raised when a SEC 10-K cannot be selected/resolved for ingest.

    Distinct from ``SecApiError`` (malformed API response) and
    ``SecDocumentParseError`` (prose extraction failed): this is an
    orchestration-level "no filing to ingest" / "cannot derive a required field"
    signal. Fail-loud -- we never silently ingest a different or partial filing.
    """


# -- pure vocabulary helpers --------------------------------------------------


def sec_period(fiscal_year: int) -> str:
    """Canonical annual ``period`` string for a 10-K (mirrors DART's "<year>-annual").

    A 10-K is always a full-year report, so the period is ``f"{fiscal_year}-annual"``
    -- byte-identical across re-runs, which is what keeps the financials upsert
    idempotent (its UNIQUE includes ``period``). Pure.
    """
    return f"{fiscal_year}-annual"


def sec_unit_for(metric: str) -> str:
    """Return the ``financials.unit`` for a SEC metric (per-share vs absolute USD).

    EPS metrics ->
    ``UNIT_USD_PER_SHARE``, everything else -> ``UNIT_USD``. Pure.
    """
    return UNIT_USD_PER_SHARE if metric in _EPS_METRICS else UNIT_USD


def build_sec_normalized_filing(
    *,
    company_match: SecCompanyMatch | None,
    cik10: str,
    filing: SecFilingItem,
    fiscal_year: int,
    document_url: str,
    financial_items: list[SecFinancialItem],
    chunks: list[Chunk],
) -> NormalizedFiling:
    """Adapt one fetched SEC filing to the public Normalized Filing seam."""
    facts: list[FinancialFact] = []
    seen: set[ReportedMetric] = set()
    for item in financial_items:
        metric = ReportedMetric(item.metric)
        if metric in seen:
            continue
        seen.add(metric)
        has_complete_range = item.period_start is not None and item.period_end is not None
        period = ReportingPeriod(
            label=sec_period(item.fiscal_year),
            kind=PeriodKind.duration,
            start_date=item.period_start if has_complete_range else None,
            end_date=item.period_end if has_complete_range else None,
        )
        facts.append(
            FinancialFact(
                metric=metric,
                period=period,
                value=item.value,
                unit=sec_unit_for(metric.value),
                currency=CURRENCY_USD,
            )
        )

    name = company_match.title if company_match and company_match.title else f"CIK {cik10}"
    return NormalizedFiling(
        company=RegulatedCompany(
            identity=CompanyIdentity(RegulatorySource.sec, cik10),
            name=name,
            name_en=name,
            ticker=(company_match.ticker or None) if company_match else None,
        ),
        identity=FilingIdentity(RegulatorySource.sec, filing.accession_number),
        filing_type=filing.form or _FORM_10K,
        title=f"Form {filing.form or _FORM_10K} (FY{fiscal_year})",
        reporting_period=ReportingPeriod(sec_period(fiscal_year), PeriodKind.duration),
        financial_facts=tuple(facts),
        filing_chunks=tuple(
            FilingChunk(
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                metadata={
                    "rcept_no": chunk.rcept_no,
                    "section_title": chunk.section_title,
                    "section_order": chunk.section_order,
                    "part_index": chunk.part_index,
                },
            )
            for chunk in chunks
        ),
        filed_at=filing.filing_date,
        url=document_url,
    )


# -- pure selection helpers ---------------------------------------------------


def select_target_filing(
    filings: list[SecFilingItem], accession_number: str | None
) -> SecFilingItem:
    """Pick the 10-K to ingest: the requested accession, or the latest filed.

    With ``accession_number`` given, the matching filing is returned, or
    :class:`SecIngestError` is raised if it is absent -- we never silently ingest
    a different filing. With ``None``, the most recently FILED 10-K wins (max
    ``filing_date``; a filing with no parseable date sorts last so a dated filing
    is always preferred). Raises :class:`SecIngestError` on an empty list. Pure.
    """
    if not filings:
        raise SecIngestError("no 10-K filings to select from")
    if accession_number is not None:
        for filing in filings:
            if filing.accession_number == accession_number:
                return filing
        raise SecIngestError(
            f"requested accession {accession_number!r} not found among "
            f"{len(filings)} 10-K filing(s)"
        )
    return max(
        filings,
        key=lambda f: (f.filing_date is not None, f.filing_date or datetime.date.min),
    )


def _filing_fiscal_year(filing: SecFilingItem, own_facts: list[SecFinancialItem]) -> int:
    """Derive the filing's fiscal year: the facts' own period, else report_date.year.

    Prefer the ``fiscal_year`` already derived from companyfacts' own-period
    figures (every own-accession annual fact shares the filing's fiscal year);
    fall back to the submissions ``report_date`` year when no mapped facts exist
    (a 10-K with prose but no mapped numbers is still a valid ingest). Raises
    :class:`SecIngestError` if neither is available -- ``financials.period`` and
    the filing title need a year and we never guess one. Pure.
    """
    for fact in own_facts:
        return fact.fiscal_year
    if filing.report_date is not None:
        return filing.report_date.year
    raise SecIngestError(
        f"cannot derive fiscal year for accession {filing.accession_number!r} "
        "(no own-period facts and no report_date)"
    )


# -- impure adapter fetch -----------------------------------------------------


@dataclass(frozen=True)
class SecFilingAdapter:
    """Fetch and normalize one selected or latest SEC 10-K."""

    client: SecClient
    cik: str | int
    accession_number: str | None = None

    async def fetch(self) -> NormalizedFiling:
        """Return a complete snapshot without opening a database session."""
        cik10 = format_cik(self.cik)
        company_match = await self.client.resolve_company_by_cik(self.cik)
        filings = await self.client.list_filings(
            self.cik, filing_types=[_FORM_10K]
        )
        target = select_target_filing(filings, self.accession_number)

        document = await self.client.fetch_document(
            self.cik, target.accession_number, target.primary_document
        )
        sections = extract_10k_prose(document.raw_bytes)
        chunks = chunk_document(sections, rcept_no=None)

        facts = await self.client.fetch_company_facts(self.cik)
        own_facts = [
            fact
            for fact in facts
            if fact.accession_number == target.accession_number
        ]
        fiscal_year = _filing_fiscal_year(target, own_facts)
        normalized = build_sec_normalized_filing(
            company_match=company_match,
            cik10=cik10,
            filing=target,
            fiscal_year=fiscal_year,
            document_url=document.url,
            financial_items=own_facts,
            chunks=chunks,
        )
        logger.info(
            "normalized SEC filing=%s financials=%d chunks=%d fiscal_year=%d",
            target.accession_number,
            len(normalized.financial_facts),
            len(normalized.filing_chunks),
            fiscal_year,
        )
        return normalized
