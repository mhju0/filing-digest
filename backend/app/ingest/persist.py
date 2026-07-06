"""Persist ONE DART filing into the 4 Postgres tables (the first DB write step).

Every step before this one stopped at "fetch -> cleaned object"; this module is
where those objects finally become rows. It wires the cleaned DART objects
(:class:`~app.clients.dart.FilingItem`, :class:`~app.clients.dart.FinancialItem`,
:class:`~app.ingest.chunking.Chunk`) onto ``companies`` / ``filings`` /
``financials`` / ``filing_chunks`` (docs/dart-api-notes.md §6), as one atomic
async transaction per filing.

Design (fixed -- do not relitigate):

1. **One transaction per filing.** company upsert -> filing upsert -> financials
   upsert -> chunks replace, all inside a single ``async with session.begin()``.
   A failure anywhere rolls the whole filing back (no half-written filing).

2. **companies is idempotent** on ``ON CONFLICT (dart_corp_code) DO UPDATE
   ... RETURNING id`` for DART rows, or ``ON CONFLICT (sec_cik)`` for SEC rows
   (the conflict column is picked from ``row["source"]``, not corp_code).
   ``source='dart'`` satisfies the ``companies_source_check`` CHECK.

3. **filings is idempotent** on ``ON CONFLICT (rcept_no) DO UPDATE ... RETURNING
   id`` for DART rows, or ``ON CONFLICT (sec_accession_no)`` for SEC rows (same
   source-based branching as companies). This is the *only* place a natural key
   becomes a ``filing_id`` (uuid); everything below uses the uuid.

4. **financials is idempotent, company-scoped** on the existing
   ``ON CONFLICT (company_id, period, metric, source)``. The table is EAV: each
   metric is one ``(metric text, value numeric(24,4))`` row, not a dedicated
   column. Per the citation rule EVERY financials row carries ``filing_id`` (the
   column is nullable, but we always populate it).

5. **filing_chunks is delete-then-insert** (``DELETE ... WHERE filing_id`` then
   bulk INSERT). A re-ingest with fewer chunks must not leave stale tail chunks;
   embeddings are still NULL so nothing is lost by rewriting.

6. **Embeddings are NOT written here.** ``filing_chunks.embedding`` stays NULL;
   a later step backfills the KURE-v1 vectors.

The "cleaned object -> row dict" mapping is kept as *pure* functions (no network,
no DB) so it is unit-tested offline; only :func:`ingest_filing` (and its private
``_upsert_*`` helpers) touch the database.

Standard vocabulary (below) is pinned as module constants because the idempotent
UNIQUE constraints depend on ``period`` / ``metric`` / ``source`` strings being
byte-for-byte identical across re-runs -- a drifting string would create a
duplicate row instead of updating the existing one.
"""

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
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
from app.db.models import Company, Filing, FilingChunk, Financial
from app.ingest.chunking import Chunk, chunk_document

logger = logging.getLogger(__name__)

# -- standard vocabulary (idempotency depends on these being stable) ----------

# Written verbatim into companies/filings/financials.source. financials.source
# has NO CHECK constraint (unlike companies.source), so a typo would silently
# create a second, non-matching row -- hence a single constant.
SOURCE_DART = "dart"
SOURCE_SEC = "sec"

# Default currency for KRW-denominated DART numbers when a row omits `currency`
# (domestic filings report in won). See docs/dart-api-notes.md §3.
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
METRIC_REVENUE = "revenue"
METRIC_OPERATING_INCOME = "operating_income"
METRIC_NET_INCOME = "net_income"  # total 당기순이익 (ifrs-full_ProfitLoss)
METRIC_NET_INCOME_ATTRIBUTABLE = "net_income_attributable"  # 지배주주귀속 subset
METRIC_EPS = "eps"  # basic EPS, per-share KRW (Decimal preserved)
METRIC_EPS_DILUTED = "eps_diluted"  # diluted EPS, per-share KRW (Decimal)

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
        raise ValueError(
            f"unknown DART reprt_code {reprt_code!r}; refusing to guess a period"
        )
    suffix, quarter, _filing_type = entry
    try:
        year = int(str(bsns_year).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid bsns_year {bsns_year!r}") from exc
    return PeriodDescriptor(
        period=f"{year}-{suffix}", fiscal_year=year, fiscal_quarter=quarter
    )


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


# -- pure row builders (cleaned object -> row dict) ---------------------------


def company_row(filing_item: FilingItem, corp_code: str, source: str = SOURCE_DART) -> dict:
    """Build the ``companies`` row for this filing's issuer (docs §6).

    ``name``/``source`` are the NOT NULL columns; ``dart_corp_code`` is the
    idempotent conflict key for the default ``source='dart'``. ``name_en`` is
    left NULL (list.json carries no English name); ``ticker`` <- stock_code,
    ``market`` <- corp_cls. Pure.
    """
    return {
        "name": filing_item.corp_name,
        "name_en": None,
        "ticker": filing_item.stock_code or None,
        "market": market_for(filing_item.corp_cls),
        "source": source,
        "dart_corp_code": corp_code,
        "sec_cik": None,
    }


def filing_row(
    filing_item: FilingItem,
    company_id: uuid.UUID,
    filing_type: str,
    period: str,
    source: str = SOURCE_DART,
) -> dict:
    """Build the ``filings`` row (docs §6).

    ``company_id`` is injected from the company upsert's RETURNING id.
    ``rcept_no`` is the idempotent conflict key for the default
    ``source='dart'``; ``title`` <- report_nm (already right-trimmed
    upstream), ``filed_at`` <- rcept_dt, ``url`` <- viewer_url. ``period`` is
    the same canonical string used for financials. Pure.
    """
    return {
        "company_id": company_id,
        "source": source,
        "rcept_no": filing_item.rcept_no,
        "filing_type": filing_type,
        "title": filing_item.report_nm,
        "period": period,
        "filed_at": filing_item.rcept_dt,
        "url": filing_item.viewer_url,
    }


def financial_rows(
    items: list[FinancialItem],
    company_id: uuid.UUID,
    filing_id: uuid.UUID,
    descriptor: PeriodDescriptor,
    source: str = SOURCE_DART,
    default_currency: str = DEFAULT_CURRENCY,
) -> list[dict]:
    """Build the EAV ``financials`` rows for one filing (docs §3, §6).

    One row per mapped metric. Rules:
    - drop unmapped rows (``metric is None``): ``financials.metric`` is NOT NULL
      and we have no standard key for them.
    - drop rows whose current-period value is ``None`` (empty/unparseable):
      ``financials.value`` is NOT NULL and we never fabricate a number (§3).
    - **dedup by metric**, keeping the first occurrence. A metric can appear under
      more than one statement (sj_div) with an identical value; two rows with the
      same metric in one INSERT would make ``ON CONFLICT`` "affect a row twice".
      Values are identical, so first-wins is lossless.
    - ``filing_id`` is ALWAYS populated (citation rule), even though the column is
      nullable.
    - ``value`` keeps its type: ``int`` (absolute KRW) or ``Decimal`` (EPS, exact,
      never float) -- ``numeric(24,4)`` holds both.

    ``company_id``/``filing_id`` are injected from the upserts' RETURNING ids.
    Pure -> unit-tested.
    """
    rows: list[dict] = []
    seen: set[str] = set()
    for item in items:
        metric = item.metric
        if metric is None:
            continue  # unmapped account: no standard key for a NOT NULL column
        if item.thstrm_amount is None:
            continue  # empty/unparseable current value: never fabricate (§3)
        if metric in seen:
            continue  # duplicate statement copy -> avoid in-batch ON CONFLICT clash
        seen.add(metric)
        rows.append(
            {
                "company_id": company_id,
                "filing_id": filing_id,  # citation: always populated
                "fiscal_year": descriptor.fiscal_year,
                "fiscal_quarter": descriptor.fiscal_quarter,
                "period": descriptor.period,
                "metric": metric,
                "value": item.thstrm_amount,  # int (KRW) | Decimal (EPS)
                "unit": unit_for(metric),
                "currency": item.currency or default_currency,
                "source": source,
            }
        )
    return rows


def chunk_rows(chunks: list[Chunk], filing_id: uuid.UUID) -> list[dict]:
    """Build the ``filing_chunks`` rows (docs §4, §6).

    ``embedding`` is explicitly NULL (backfilled in the next step). ``meta`` holds
    the citation anchor (``rcept_no``, ``section_title``, ``section_order``,
    ``part_index``) so any embedded text stays traceable to its source. Pure.
    """
    return [
        {
            "filing_id": filing_id,
            "chunk_index": chunk.chunk_index,
            "content": chunk.content,
            "embedding": None,  # NULL now; KURE-v1 vectors are the next step
            "meta": {
                "rcept_no": chunk.rcept_no,
                "section_title": chunk.section_title,
                "section_order": chunk.section_order,
                "part_index": chunk.part_index,
            },
        }
        for chunk in chunks
    ]


# -- impure: the atomic write ------------------------------------------------

# source -> idempotent conflict column. No default: an unknown source must
# fail loudly rather than silently reuse DART's column and corrupt idempotency.
_COMPANY_CONFLICT_COLUMNS: dict[str, str] = {
    SOURCE_DART: "dart_corp_code",
    SOURCE_SEC: "sec_cik",
}
_FILING_CONFLICT_COLUMNS: dict[str, str] = {
    SOURCE_DART: "rcept_no",
    SOURCE_SEC: "sec_accession_no",
}


def _natural_key_column(columns: dict[str, str], label: str, source: str) -> str:
    """Look up the idempotent conflict column for ``source``, or raise."""
    try:
        return columns[source]
    except KeyError as exc:
        raise ValueError(f"unknown {label} source {source!r}") from exc


def _require_natural_key(row: dict, column: str) -> None:
    """Raise if ``row[column]`` (the row's natural key for its source) is NULL.

    A NULL natural key can never conflict with itself on re-run (Postgres
    treats NULLs as distinct under UNIQUE), so it would silently break
    idempotency by inserting a fresh duplicate row every time.
    """
    if row.get(column) is None:
        raise ValueError(
            f"row for source {row.get('source')!r} has no {column}; "
            "refusing to write a non-idempotent row"
        )


async def _upsert_company(session: AsyncSession, row: dict) -> uuid.UUID:
    """Upsert one ``companies`` row; return its id (invariant).

    Conflict column is picked from ``row["source"]``: ``dart_corp_code`` for
    'dart', ``sec_cik`` for 'sec'.
    """
    conflict_column = _natural_key_column(_COMPANY_CONFLICT_COLUMNS, "company", row["source"])
    _require_natural_key(row, conflict_column)
    ins = pg_insert(Company).values(**row)
    stmt = ins.on_conflict_do_update(
        index_elements=[conflict_column],
        set_={
            "name": ins.excluded.name,
            "name_en": ins.excluded.name_en,
            "ticker": ins.excluded.ticker,
            "market": ins.excluded.market,
            "source": ins.excluded.source,
        },
    ).returning(Company.id)
    return (await session.execute(stmt)).scalar_one()


async def _upsert_filing(session: AsyncSession, row: dict) -> uuid.UUID:
    """Upsert one ``filings`` row; return its id (invariant).

    Conflict column is picked from ``row["source"]``: ``rcept_no`` for 'dart',
    ``sec_accession_no`` for 'sec'. This is the sole natural-key ->
    filing_id(uuid) mapping point in the system.
    """
    conflict_column = _natural_key_column(_FILING_CONFLICT_COLUMNS, "filing", row["source"])
    _require_natural_key(row, conflict_column)
    ins = pg_insert(Filing).values(**row)
    stmt = ins.on_conflict_do_update(
        index_elements=[conflict_column],
        set_={
            "company_id": ins.excluded.company_id,
            "source": ins.excluded.source,
            "filing_type": ins.excluded.filing_type,
            "title": ins.excluded.title,
            "period": ins.excluded.period,
            "filed_at": ins.excluded.filed_at,
            "url": ins.excluded.url,
        },
    ).returning(Filing.id)
    return (await session.execute(stmt)).scalar_one()


async def _upsert_financials(session: AsyncSession, rows: list[dict]) -> None:
    """Upsert the ``financials`` rows on (company_id, period, metric, source)."""
    if not rows:
        return
    ins = pg_insert(Financial).values(rows)
    stmt = ins.on_conflict_do_update(
        index_elements=["company_id", "period", "metric", "source"],
        set_={
            "filing_id": ins.excluded.filing_id,
            "fiscal_year": ins.excluded.fiscal_year,
            "fiscal_quarter": ins.excluded.fiscal_quarter,
            "value": ins.excluded.value,
            "unit": ins.excluded.unit,
            "currency": ins.excluded.currency,
        },
    )
    await session.execute(stmt)


async def _replace_chunks(
    session: AsyncSession, filing_id: uuid.UUID, rows: list[dict]
) -> None:
    """Delete this filing's chunks, then bulk-insert the fresh set (no stale tail)."""
    await session.execute(delete(FilingChunk).where(FilingChunk.filing_id == filing_id))
    if rows:
        await session.execute(insert(FilingChunk).values(rows))


async def _fetch_filing_item(
    dart_client: DartClient, corp_code: str, rcept_no: str
) -> FilingItem:
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
    document.xml (prose -> chunks), then a single ``session.begin()`` block that
    upserts company -> filing -> financials and replaces the filing's chunks. All
    network I/O happens *before* the transaction opens, so a fetch failure never
    leaves a partial write, and the transaction stays short.

    ``bsns_year``/``reprt_code`` are required because the financials API is keyed
    by them and they are NOT derivable from ``rcept_no`` (an annual report filed
    in 2024 covers business year 2023). ``fs_div`` defaults to consolidated (CFS).

    Returns an :class:`IngestResult` (ids + written counts). Re-running with the
    same arguments is idempotent: row counts and the company/filing ids are
    unchanged.
    """
    # -- 1. fetch everything first (network only; DB untouched) --------------
    filing_item = await _fetch_filing_item(dart_client, corp_code, rcept_no)
    financial_items = await dart_client.fetch_financials(
        corp_code, bsns_year, reprt_code, fs_div
    )
    document = await dart_client.fetch_document(rcept_no)

    text = decode_dart_bytes(document.content)
    doc_format = detect_document_format(text)
    if doc_format == "dsd":
        sections = extract_dsd_prose(text)
        chunks = chunk_document(sections, rcept_no=rcept_no)
    else:
        # xforms/unknown: no parser yet (Phase 2). Skip + log; never invent prose.
        logger.warning(
            "ingest_filing: rcept_no=%s document format=%s not parseable yet; "
            "writing 0 chunks",
            rcept_no,
            doc_format,
        )
        chunks = []

    # -- 2. derive canonical vocabulary (pure) ------------------------------
    descriptor = period_descriptor(bsns_year, reprt_code)
    ftype = filing_type_for(reprt_code)

    # -- 3. one atomic transaction (all-or-nothing for this filing) ---------
    async with session.begin():
        company_id = await _upsert_company(session, company_row(filing_item, corp_code))
        filing_id = await _upsert_filing(
            session, filing_row(filing_item, company_id, ftype, descriptor.period)
        )
        fin_rows = financial_rows(financial_items, company_id, filing_id, descriptor)
        await _upsert_financials(session, fin_rows)
        c_rows = chunk_rows(chunks, filing_id)
        await _replace_chunks(session, filing_id, c_rows)

    logger.info(
        "ingest_filing: rcept_no=%s -> company=%s filing=%s financials=%d chunks=%d "
        "(format=%s)",
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
