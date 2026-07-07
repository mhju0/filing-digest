"""Persist ONE SEC 10-K filing into the 4 Postgres tables (SEC counterpart to
:func:`app.ingest.persist.ingest_filing`).

This mirrors ``ingest_filing`` exactly in shape -- fetch + parse everything first
(network only; DB untouched), then a single ``session.begin()`` block upserts
company -> filing -> financials and replaces the filing's chunks -- but keyed on
SEC natural keys (``sec_cik`` / ``sec_accession_no``), with ``source='sec'`` and
``currency='USD'``. The DART path in ``persist.py`` is deliberately untouched: the
impure writers (:func:`~app.ingest.persist._upsert_company` /
``_upsert_filing`` / ``_upsert_financials`` / ``_replace_chunks``) and
:func:`~app.ingest.persist.chunk_rows` are reused as-is, so the natural-key
``ValueError`` protection and the delete-then-insert chunk semantics are shared,
not re-implemented.

Design (mirrors persist.py; the differences are all SEC-specific):

1. **Numbers come only from companyfacts.** ``fetch_company_facts`` is the single
   source of figures (never the LLM/document); the facts are filtered to the
   chosen accession's *own-period* rows before they become ``financials`` rows.

2. **USD units, not KRW.** ``persist.financial_rows`` bakes in KRW units via
   ``unit_for`` and reads DART's ``FinancialItem.thstrm_amount``; a 10-K reports
   USD and carries ``SecFinancialItem.value``, so :func:`sec_financial_rows` is a
   separate builder (reusing it would mislabel USD figures as KRW).

3. **Chunks carry no rcept_no.** SEC filings have no DART receipt number, so
   ``chunk_document`` is called with ``rcept_no=None``; the citation anchor's
   provenance rides on ``filing_id -> filings.sec_accession_no`` instead (the
   accession is never stuffed into the DART-specific ``meta.rcept_no`` field --
   see :mod:`app.ingest.chunking`).

4. **Embeddings backfill is part of the orchestration.** After the write
   transaction commits, :func:`~app.embeddings.backfill.backfill_embeddings` is
   reused unchanged on a fresh session (it manages its own per-batch commits).
   The orchestrator therefore takes a ``session_factory`` (not a live session),
   since it opens two independent session scopes.

The "cleaned object -> row dict" mapping is kept as *pure* functions (no network,
no DB) so it is unit-tested offline; only :func:`ingest_sec_filing` touches the
database (via the reused persist writers).
"""

import datetime
import logging
import uuid
from dataclasses import dataclass
from typing import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.sec import (
    SecClient,
    SecCompanyMatch,
    SecFilingItem,
    SecFinancialItem,
    format_cik,
)
from app.clients.sec_document import extract_10k_prose
from app.embeddings.backfill import backfill_embeddings
from app.ingest.chunking import chunk_document
from app.ingest.persist import (
    METRIC_EPS,
    METRIC_EPS_DILUTED,
    SOURCE_SEC,
    _replace_chunks,
    _upsert_company,
    _upsert_filing,
    _upsert_financials,
    chunk_rows,
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


# -- pure row builders (cleaned object -> row dict) ---------------------------


def sec_company_row(match: SecCompanyMatch | None, cik10: str) -> dict:
    """Build the ``companies`` row for a SEC filer (``source='sec'``).

    ``sec_cik`` is the idempotent conflict key (``dart_corp_code`` stays None).
    ``name``/``ticker`` come from company_tickers when the CIK resolved; a filer
    absent from that index falls back to a deterministic ``f"CIK {cik10}"`` name
    (the CIK IS the identity -- never fabricated data) with a NULL ticker.
    ``market`` is left None (company_tickers carries no exchange). ``name_en`` is
    None (the primary ``name`` is already English). Pure.
    """
    return {
        "name": match.title if (match and match.title) else f"CIK {cik10}",
        "name_en": None,
        "ticker": (match.ticker or None) if match else None,
        "market": None,
        "source": SOURCE_SEC,
        "dart_corp_code": None,
        "sec_cik": cik10,
    }


def sec_filing_row(
    filing: SecFilingItem,
    company_id: uuid.UUID,
    fiscal_year: int,
    url: str,
) -> dict:
    """Build the ``filings`` row for a 10-K (``source='sec'``).

    ``sec_accession_no`` is the idempotent conflict key (``rcept_no`` stays None).
    ``company_id`` is injected from the company upsert's RETURNING id.
    ``filing_type`` is the literal SEC form ("10-K" -- SEC's own human-facing
    designation, the honest analog of DART's semantic label), ``title`` a
    synthesized human label, ``period`` the canonical annual string, ``filed_at``
    <- filing_date, ``url`` <- the primary document's EDGAR archive URL (the
    citation link). Pure.
    """
    form = filing.form or _FORM_10K
    return {
        "company_id": company_id,
        "source": SOURCE_SEC,
        "rcept_no": None,
        "sec_accession_no": filing.accession_number,
        "filing_type": form,
        "title": f"Form {form} (FY{fiscal_year})",
        "period": sec_period(fiscal_year),
        "filed_at": filing.filing_date,
        "url": url,
    }


def sec_financial_rows(
    items: list[SecFinancialItem],
    company_id: uuid.UUID,
    filing_id: uuid.UUID,
) -> list[dict]:
    """Build the EAV ``financials`` rows from a 10-K's own-accession facts (source='sec').

    Mirrors ``persist.financial_rows``' rules -- one row per metric, **dedup by
    metric** (first wins, to avoid an in-batch ON CONFLICT clash), ``filing_id``
    ALWAYS populated (citation rule) -- but reads :class:`SecFinancialItem`
    (``.value`` / ``.metric`` / ``.fiscal_year``) and emits USD units + currency.
    The None guards mirror the DART builder's NOT-NULL contract even though the
    companyfacts parser already drops unmapped/empty facts (defensive, so a future
    caller passing raw-ish items still cannot write a NULL metric/value).

    ``items`` must already be filtered to ONE accession by the caller
    (:func:`ingest_sec_filing`); each row's ``period`` is derived from that fact's
    own ``fiscal_year`` (all own-accession annual facts share the filing's year).
    ``company_id``/``filing_id`` are injected from the upserts' RETURNING ids.
    Pure -> unit-tested.
    """
    rows: list[dict] = []
    seen: set[str] = set()
    for item in items:
        metric = item.metric
        if metric is None:
            continue  # unmapped: financials.metric is NOT NULL
        if item.value is None:
            continue  # empty/unparseable: financials.value is NOT NULL, never fabricate
        if metric in seen:
            continue  # duplicate metric -> avoid in-batch ON CONFLICT clash
        seen.add(metric)
        rows.append(
            {
                "company_id": company_id,
                "filing_id": filing_id,  # citation: always populated
                "fiscal_year": item.fiscal_year,
                "fiscal_quarter": None,  # 10-K is annual
                "period": sec_period(item.fiscal_year),
                "metric": metric,
                "value": item.value,  # int (USD) | Decimal (EPS)
                "unit": sec_unit_for(metric),
                "currency": CURRENCY_USD,
                "source": SOURCE_SEC,
            }
        )
    return rows


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


def _filing_fiscal_year(
    filing: SecFilingItem, own_facts: list[SecFinancialItem]
) -> int:
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


# -- impure: fetch + the atomic write + backfill ------------------------------


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
    block upserts company -> filing -> financials and replaces the filing's
    chunks. Finally, embeddings are backfilled on a fresh session.

    ``session_factory`` is a zero-arg callable returning an ``AsyncSession`` usable
    as an async context manager (e.g. ``get_async_session``); two scopes are
    opened -- one for the atomic write, one for the (self-committing) backfill.

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

    # -- 2. one atomic transaction (all-or-nothing for this filing) ----------
    async with session_factory() as session:
        async with session.begin():
            company_id = await _upsert_company(
                session, sec_company_row(company_match, cik10)
            )
            filing_id = await _upsert_filing(
                session, sec_filing_row(target, company_id, fiscal_year, document.url)
            )
            fin_rows = sec_financial_rows(own_facts, company_id, filing_id)
            await _upsert_financials(session, fin_rows)
            c_rows = chunk_rows(chunks, filing_id)
            await _replace_chunks(session, filing_id, c_rows)

    # -- 3. backfill embeddings on a fresh session (self-committing per batch)-
    async with session_factory() as session:
        embeddings_backfilled = await backfill_embeddings(session)

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
