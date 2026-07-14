"""SEC adapter for the source-independent Normalized Filing seam.

The SEC orchestration fetches and parses everything before building one complete
:class:`app.filings.model.NormalizedFiling`. The shared persistence module then
replaces its reported Financial Facts and Filing Chunks atomically by Filing
Identity, keyed by ``sec_cik`` / ``sec_accession_no``. Embeddings are indexed only
for that filing after the database snapshot commits.

Design (mirrors persist.py; the differences are all SEC-specific):

1. **Numbers come only from companyfacts.** ``fetch_company_facts`` is the single
   source of figures (never the LLM/document); the facts are filtered to the
   chosen accession's *own-period* rows before they become ``financials`` rows.

2. **USD units, not KRW.** SEC facts use their own unit vocabulary while the
   adapter translates ``SecFinancialItem.value`` directly into canonical
   :class:`~app.filings.model.FinancialFact` objects.

3. **Chunks carry no rcept_no.** SEC filings have no DART receipt number, so
   ``chunk_document`` is called with ``rcept_no=None``; the citation anchor's
   provenance rides on ``filing_id -> filings.sec_accession_no`` instead (the
   accession is never stuffed into the DART-specific ``meta.rcept_no`` field --
   see :mod:`app.ingest.chunking`).

4. **Filing-scoped indexing is part of the orchestration.** After persistence
   commits, :func:`~app.embeddings.backfill.index_filing_embeddings` fills only
   the target filing on a fresh session and publishes ``indexed_at`` only after
   all of its chunks have vectors.

The adapter exposes source vocabulary helpers plus
:func:`build_sec_normalized_filing`; database row construction belongs solely
to :mod:`app.filings.persistence`.
"""

import datetime
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.sec import (
    SecClient,
    SecCompanyMatch,
    SecFilingItem,
    SecFinancialItem,
    format_cik,
)
from app.clients.sec_document import extract_10k_prose
from app.embeddings.backfill import index_filing_embeddings
from app.filings.model import (
    CompanyIdentity,
    FilingChunk,
    FilingIdentity,
    FinancialFact,
    NormalizedFiling,
    RegulatedCompany,
    RegulatorySource,
    ReportingPeriod,
)
from app.filings.persistence import persist_normalized_filing
from app.financials.vocabulary import PeriodKind, ReportedMetric
from app.ingest.chunking import Chunk, chunk_document
from app.ingest.persist import (
    METRIC_EPS,
    METRIC_EPS_DILUTED,
)

logger = logging.getLogger(__name__)

# The one SEC form this step ingests (annual report). Mirrors persist.py's report
# codes: only 10-K is in scope; 10-Q and others are a future extension.
_FORM_10K = "10-K"

# SEC financials.unit / currency vocabulary. Parallels persist.py's UNIT_KRW /
# UNIT_KRW_PER_SHARE split (the `unit` column distinguishes absolute vs per-share
# so a reader never mistakes an EPS for an absolute amount); `currency` carries
# the ISO code.
UNIT_USD = "USD"  # absolute USD (revenue, operating_income, net_income)
UNIT_USD_PER_SHARE = "USD_PER_SHARE"  # USD per share (eps, eps_diluted)
CURRENCY_USD = "USD"

# Per-share metrics -> UNIT_USD_PER_SHARE; everything else -> UNIT_USD. Reuses the
# standard metric keys from persist.py (the single spelling shared with DART).
_EPS_METRICS = frozenset({METRIC_EPS, METRIC_EPS_DILUTED})


class SecIngestError(RuntimeError):
    """Raised when a SEC 10-K cannot be selected/resolved for ingest.

    Distinct from ``SecApiError`` (malformed API response) and
    ``SecDocumentParseError`` (prose extraction failed): this is an
    orchestration-level "no filing to ingest" / "cannot derive a required field"
    signal. Fail-loud -- we never silently ingest a different or partial filing.
    """


@dataclass(frozen=True)
class SecIngestResult:
    """Outcome of one :func:`ingest_sec_filing` call (ids + written counts)."""

    company_id: uuid.UUID
    filing_id: uuid.UUID
    accession_number: str
    financials_written: int
    chunks_written: int
    embeddings_backfilled: int


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

    Mirrors ``persist.unit_for`` but in USD vocabulary: EPS metrics ->
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


# -- impure: fetch + atomic snapshot replacement + scoped indexing -----------


async def ingest_sec_filing(
    sec_client: SecClient,
    session_factory: Callable[[], AsyncSession],
    cik: str | int,
    accession_number: str | None = None,
) -> SecIngestResult:
    """Ingest ONE SEC 10-K into all 4 tables, then backfill its embeddings.

    Flow (mirrors :func:`app.ingest.persist.ingest_filing`): resolve the filer's
    display name (company_tickers) -> list 10-K filings and pick the latest (or
    the given ``accession_number``) -> fetch the primary document -> extract Item
    1/7 prose -> chunk it -> fetch companyfacts and filter to the chosen
    accession's own-period figures. ALL network I/O + parsing happens *before* the
    transaction opens, so a fetch/parse failure (e.g. ``SecDocumentParseError``)
    raises before any write -- nothing is persisted. Then one ``session.begin()``
    snapshot is adapted and atomically persisted through the shared Normalized
    Filing seam. Finally, only that filing's embeddings are indexed on a fresh
    session; it becomes searchable after all target chunks are ready.

    ``session_factory`` is a zero-arg callable returning an ``AsyncSession`` usable
    as an async context manager (e.g. ``get_async_session``); two scopes are
    opened -- one for the atomic write, one for self-committing scoped indexing.

    Returns a :class:`SecIngestResult` (ids + written counts). Re-running with the
    same arguments is idempotent: the company/filing ids and row counts are
    unchanged (natural-key upserts + delete-then-insert chunks).
    """
    cik10 = format_cik(cik)

    # -- 1. fetch + parse everything first (network only; DB untouched) ------
    company_match = await sec_client.resolve_company_by_cik(cik)
    filings = await sec_client.list_filings(cik, filing_types=[_FORM_10K])
    target = select_target_filing(filings, accession_number)

    document = await sec_client.fetch_document(
        cik, target.accession_number, target.primary_document
    )
    # Fail-loud: SecDocumentParseError raises here, before any session opens.
    sections = extract_10k_prose(document.raw_bytes)
    chunks = chunk_document(sections, rcept_no=None)  # SEC has no rcept_no

    facts = await sec_client.fetch_company_facts(cik)
    own_facts = [f for f in facts if f.accession_number == target.accession_number]
    fiscal_year = _filing_fiscal_year(target, own_facts)

    # -- 2. one atomic authoritative snapshot replacement -------------------
    normalized = build_sec_normalized_filing(
        company_match=company_match,
        cik10=cik10,
        filing=target,
        fiscal_year=fiscal_year,
        document_url=document.url,
        financial_items=own_facts,
        chunks=chunks,
    )
    async with session_factory() as session:
        persisted = await persist_normalized_filing(session, normalized)
    company_id = persisted.company_id
    filing_id = persisted.filing_id
    fin_rows = normalized.financial_facts
    c_rows = normalized.filing_chunks

    # -- 3. index this filing on a fresh session (self-committing per batch) --
    async with session_factory() as session:
        embeddings_backfilled = await index_filing_embeddings(session, filing_id)

    logger.info(
        "ingest_sec_filing: cik=%s accession=%s -> company=%s filing=%s "
        "financials=%d chunks=%d embeddings=%d (fiscal_year=%d)",
        cik10,
        target.accession_number,
        company_id,
        filing_id,
        len(fin_rows),
        len(c_rows),
        embeddings_backfilled,
        fiscal_year,
    )
    return SecIngestResult(
        company_id=company_id,
        filing_id=filing_id,
        accession_number=target.accession_number,
        financials_written=len(fin_rows),
        chunks_written=len(c_rows),
        embeddings_backfilled=embeddings_backfilled,
    )
