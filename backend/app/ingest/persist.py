"""DART adapter for the source-independent Normalized Filing seam.

External fetches and DART parsing complete before this module builds a
:class:`app.filings.model.NormalizedFiling`. The shared persistence module then
replaces its Financial Facts and Filing Chunks atomically by Filing Identity.
Embedding remains a separate, filing-scoped indexing step after commit.

The adapter exposes source vocabulary helpers plus
:func:`build_dart_normalized_filing`; database row construction belongs solely
to :mod:`app.filings.persistence`.
"""

import logging
import uuid
from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.dart import (
    DartApiError,
    DartClient,
    FilingItem,
    FinancialItem,
    decode_dart_bytes,
    detect_document_format,
    extract_dsd_prose,
)
from app.filings.model import (
    CompanyIdentity,
    FilingIdentity,
    FinancialFact,
    NormalizedFiling,
    RegulatedCompany,
    RegulatorySource,
    ReportingPeriod,
)
from app.filings.model import (
    FilingChunk as NormalizedChunk,
)
from app.filings.persistence import persist_normalized_filing
from app.financials.vocabulary import PeriodKind, ReportedMetric
from app.ingest.chunking import Chunk, chunk_document

logger = logging.getLogger(__name__)

# -- standard vocabulary (idempotency depends on these being stable) ----------

# Written verbatim into companies/filings/financials.source. financials.source
# has NO CHECK constraint (unlike companies.source), so a typo would silently
# create a second, non-matching row -- hence a single constant.
SOURCE_DART = RegulatorySource.dart.value
SOURCE_SEC = RegulatorySource.sec.value

# Default currency for KRW-denominated DART numbers when a row omits `currency`
# (domestic filings report in won).
DEFAULT_CURRENCY = "KRW"

# financials.unit vocabulary. DART amounts are ABSOLUTE KRW (원) with no scaling
# (docs §3); EPS rows are per-share KRW (원/주). unit distinguishes the two so a
# reader never mistakes an EPS for an absolute amount.
UNIT_KRW = "KRW"  # absolute won (revenue, operating_income, net_income, ...)
UNIT_KRW_PER_SHARE = "KRW_PER_SHARE"  # won per share (eps, eps_diluted)

# Standard metric keys. These MIRROR app.clients.dart._ACCOUNT_ID_TO_METRIC (the
# account_id -> metric mapping); DART's structured API is the single source of
# numbers, and these are the keys it produces. Kept as named constants here so
# the unit split (below) and any downstream reader reference one spelling.
METRIC_REVENUE = ReportedMetric.revenue.value
METRIC_OPERATING_INCOME = ReportedMetric.operating_income.value
METRIC_NET_INCOME = ReportedMetric.net_income.value  # total 당기순이익
METRIC_NET_INCOME_ATTRIBUTABLE = ReportedMetric.net_income_attributable.value
METRIC_EPS = ReportedMetric.eps.value  # basic EPS, per-share KRW
METRIC_EPS_DILUTED = ReportedMetric.eps_diluted.value

# Per-share metrics -> UNIT_KRW_PER_SHARE; everything else -> UNIT_KRW.
_EPS_METRICS = frozenset({METRIC_EPS, METRIC_EPS_DILUTED})

# reprt_code (docs §3) -> (period suffix, fiscal_quarter, filing_type). The
# `period` string is the canonical, re-run-stable identity used by the financials
# UNIQUE constraint: `f"{fiscal_year}-{suffix}"` (e.g. "2023-annual", "2023-Q1").
# fiscal_quarter marks the as-of quarter (annual = None; 반기 = 2 i.e. end of H1).
_REPORT_CODES: dict[str, tuple[str, int | None, str]] = {
    "11011": ("annual", None, "business_report"),  # 사업보고서 (full year)
    "11012": ("H1", 2, "half_year_report"),  # 반기보고서 (H1, as-of Q2)
    "11013": ("Q1", 1, "quarterly_report"),  # 1분기보고서
    "11014": ("Q3", 3, "quarterly_report"),  # 3분기보고서
}

# corp_cls (법인구분, docs §2) -> human market label for companies.market.
_CORP_CLS_TO_MARKET: dict[str, str] = {
    "Y": "KOSPI",  # 유가증권시장
    "K": "KOSDAQ",
    "N": "KONEX",
    "E": "ETC",  # 기타법인
}


@dataclass(frozen=True)
class PeriodDescriptor:
    """The canonical (period, fiscal_year, fiscal_quarter) for one report.

    Derived purely from (``bsns_year``, ``reprt_code``); identical across re-runs,
    which is what makes the financials upsert idempotent.
    """

    period: str
    fiscal_year: int
    fiscal_quarter: int | None


@dataclass(frozen=True)
class IngestResult:
    """Outcome of one :func:`ingest_filing` call (ids + written counts)."""

    company_id: uuid.UUID
    filing_id: uuid.UUID
    financials_written: int
    chunks_written: int
    document_format: str


# -- pure vocabulary helpers --------------------------------------------------


def period_descriptor(bsns_year: str, reprt_code: str) -> PeriodDescriptor:
    """Map (business year, report code) -> canonical :class:`PeriodDescriptor`.

    Raises ``ValueError`` for an unknown ``reprt_code`` or unparseable
    ``bsns_year`` -- we never guess a period, because a wrong/unstable period
    string would break the idempotent financials UNIQUE. Pure -> unit-tested.
    """
    entry = _REPORT_CODES.get((reprt_code or "").strip())
    if entry is None:
        raise ValueError(f"unknown DART reprt_code {reprt_code!r}; refusing to guess a period")
    suffix, quarter, _filing_type = entry
    try:
        year = int(str(bsns_year).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid bsns_year {bsns_year!r}") from exc
    return PeriodDescriptor(period=f"{year}-{suffix}", fiscal_year=year, fiscal_quarter=quarter)


def filing_type_for(reprt_code: str) -> str:
    """Map a report code to the stable ``filings.filing_type`` label (NOT NULL).

    Raises ``ValueError`` for an unknown code (same no-guessing rule as
    :func:`period_descriptor`). Pure -> unit-tested.
    """
    entry = _REPORT_CODES.get((reprt_code or "").strip())
    if entry is None:
        raise ValueError(f"unknown DART reprt_code {reprt_code!r}")
    return entry[2]


def market_for(corp_cls: str | None) -> str | None:
    """Map a DART ``corp_cls`` to a market label, or ``None`` if unknown/blank."""
    return _CORP_CLS_TO_MARKET.get((corp_cls or "").strip().upper())


def unit_for(metric: str) -> str:
    """Return the ``financials.unit`` for a metric (per-share vs absolute KRW)."""
    return UNIT_KRW_PER_SHARE if metric in _EPS_METRICS else UNIT_KRW


def _dart_financial_facts(
    items: list[FinancialItem], descriptor: PeriodDescriptor
) -> tuple[FinancialFact, ...]:
    """Translate DART taxonomy rows into canonical reported Financial Facts."""
    current_period = ReportingPeriod(descriptor.period, PeriodKind.duration)
    prior_period = ReportingPeriod(f"{descriptor.fiscal_year - 1}-annual", PeriodKind.duration)
    facts: list[FinancialFact] = []
    seen_current: set[ReportedMetric] = set()
    seen_prior: set[ReportedMetric] = set()
    for item in items:
        if item.metric is None or item.thstrm_amount is None:
            continue
        metric = ReportedMetric(item.metric)
        if metric not in seen_current:
            seen_current.add(metric)
            facts.append(
                FinancialFact(
                    metric=metric,
                    period=current_period,
                    value=item.thstrm_amount,
                    unit=unit_for(metric.value),
                    currency=item.currency or DEFAULT_CURRENCY,
                )
            )
        if (
            descriptor.fiscal_quarter is None
            and item.frmtrm_amount is not None
            and metric not in seen_prior
        ):
            seen_prior.add(metric)
            facts.append(
                FinancialFact(
                    metric=metric,
                    period=prior_period,
                    value=item.frmtrm_amount,
                    unit=unit_for(metric.value),
                    currency=item.currency or DEFAULT_CURRENCY,
                )
            )
    return tuple(facts)


def build_dart_normalized_filing(
    *,
    filing_item: FilingItem,
    corp_code: str,
    name_en: str | None,
    descriptor: PeriodDescriptor,
    filing_type: str,
    financial_items: list[FinancialItem],
    chunks: list[Chunk],
) -> NormalizedFiling:
    """Adapt one fetched DART filing to the public Normalized Filing seam."""
    return NormalizedFiling(
        company=RegulatedCompany(
            identity=CompanyIdentity(RegulatorySource.dart, corp_code),
            name=filing_item.corp_name,
            name_en=name_en,
            ticker=filing_item.stock_code or None,
            market=market_for(filing_item.corp_cls),
        ),
        identity=FilingIdentity(RegulatorySource.dart, filing_item.rcept_no),
        filing_type=filing_type,
        title=filing_item.report_nm,
        reporting_period=ReportingPeriod(descriptor.period, PeriodKind.duration),
        financial_facts=_dart_financial_facts(financial_items, descriptor),
        filing_chunks=tuple(
            NormalizedChunk(
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
        filed_at=filing_item.rcept_dt,
        url=filing_item.viewer_url,
    )


async def _fetch_filing_item(dart_client: DartClient, corp_code: str, rcept_no: str) -> FilingItem:
    """Resolve the :class:`FilingItem` for a single rcept_no via list.json.

    A ``rcept_no`` is ``YYYYMMDDNNNNNN``: its first 8 digits are the receipt date
    [Verified DART convention], so list.json for exactly that day contains this
    filing. Raises :class:`DartApiError` if it is not found -- we never fabricate
    the issuer/title from thin air.
    """
    day = rcept_no[:8]
    filings = await dart_client.list_filings(corp_code, bgn_de=day, end_de=day)
    for filing in filings:
        if filing.rcept_no == rcept_no:
            return filing
    raise DartApiError(
        f"list.json: rcept_no {rcept_no} not found for corp_code {corp_code} on {day}"
    )


async def _fetch_company_eng_name(dart_client: DartClient, corp_code: str) -> str | None:
    """Best-effort English-name enrichment; never fails the ingest.

    ``name_en`` is enrichment for bilingual search, not the critical path (numbers
    and prose are), so a company.json failure -- bad key, rate limit, network --
    logs and yields ``None`` rather than aborting the filing ingest.
    """
    try:
        return await dart_client.fetch_company_eng_name(corp_code)
    except (DartApiError, httpx.HTTPError) as exc:
        logger.warning(
            "company.json enrichment failed for corp_code=%s: %s; name_en stays NULL",
            corp_code,
            exc,
        )
        return None


async def ingest_filing(
    session: AsyncSession,
    dart_client: DartClient,
    corp_code: str,
    rcept_no: str,
    bsns_year: str,
    reprt_code: str,
    fs_div: str = "CFS",
) -> IngestResult:
    """Ingest ONE DART filing into all 4 tables as a single atomic transaction.

    Flow: list.json (filing metadata) -> fnlttSinglAcntAll.json (numbers) ->
    document.xml (prose -> chunks), then adaptation to a complete
    :class:`~app.filings.model.NormalizedFiling`. The shared persistence seam
    atomically upserts its company/filing and replaces the filing's facts and
    chunks. All network I/O happens *before* the transaction opens, so a fetch
    failure never leaves a partial write, and the transaction stays short.

    ``bsns_year``/``reprt_code`` are required because the financials API is keyed
    by them and they are NOT derivable from ``rcept_no`` (an annual report filed
    in 2024 covers business year 2023). ``fs_div`` defaults to consolidated (CFS).

    Returns an :class:`IngestResult` (ids + written counts). Re-running with the
    same arguments is idempotent: row counts and the company/filing ids are
    unchanged.
    """
    # -- 1. fetch everything first (network only; DB untouched) --------------
    filing_item = await _fetch_filing_item(dart_client, corp_code, rcept_no)
    financial_items = await dart_client.fetch_financials(corp_code, bsns_year, reprt_code, fs_div)
    name_en = await _fetch_company_eng_name(dart_client, corp_code)
    document = await dart_client.fetch_document(rcept_no)

    text = decode_dart_bytes(document.content)
    doc_format = detect_document_format(text)
    if doc_format == "dsd":
        sections = extract_dsd_prose(text)
        chunks = chunk_document(sections, rcept_no=rcept_no)
    else:
        # xforms/unknown: unsupported. Skip + log; never invent prose.
        logger.warning(
            "ingest_filing: rcept_no=%s document format=%s not parseable yet; writing 0 chunks",
            rcept_no,
            doc_format,
        )
        chunks = []

    # -- 2. derive canonical vocabulary (pure) ------------------------------
    descriptor = period_descriptor(bsns_year, reprt_code)
    ftype = filing_type_for(reprt_code)

    # -- 3. one atomic authoritative snapshot replacement -------------------
    normalized = build_dart_normalized_filing(
        filing_item=filing_item,
        corp_code=corp_code,
        name_en=name_en,
        descriptor=descriptor,
        filing_type=ftype,
        financial_items=financial_items,
        chunks=chunks,
    )
    persisted = await persist_normalized_filing(session, normalized)
    company_id = persisted.company_id
    filing_id = persisted.filing_id
    fin_rows = normalized.financial_facts
    c_rows = normalized.filing_chunks

    logger.info(
        "ingest_filing: rcept_no=%s -> company=%s filing=%s financials=%d chunks=%d (format=%s)",
        rcept_no,
        company_id,
        filing_id,
        len(fin_rows),
        len(c_rows),
        doc_format,
    )
    return IngestResult(
        company_id=company_id,
        filing_id=filing_id,
        financials_written=len(fin_rows),
        chunks_written=len(c_rows),
        document_format=doc_format,
    )
