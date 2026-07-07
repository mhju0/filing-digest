"""SEC EDGAR client -- submissions / company facts / document fetch / ticker search.

Implements:
- ``submissions/CIK##########.json``: :func:`SecClient.list_filings` -- per-CIK
  filing history (column-oriented arrays in the response; zipped into rows here),
  optionally filtered to a set of form types (e.g. ``["10-K"]``).
- ``api/xbrl/companyfacts/CIK##########.json``: :func:`SecClient.fetch_company_facts`
  -- structured XBRL facts. This is the *single source of numbers* for SEC filings:
  financial values come only from this API, never from LLM/document text. Only
  annual (``form == "10-K"``) facts for the five us-gaap tags in
  :data:`_US_GAAP_TO_METRIC` are extracted and mapped to standard metric keys.
- Document fetch (EDGAR archives, not a JSON API): :func:`SecClient.fetch_document`
  downloads the primary document's raw bytes. Fetch only -- HTML parsing is a
  later step (mirrors DART's document.xml -> prose split).
- ``files/company_tickers.json``: :func:`SecClient.search_company` -- SEC has no
  fuzzy search endpoint, so this mapping file is fetched once, cached in-process,
  and matched by exact ticker or case-insensitive name substring.

SEC compliance: every request carries a ``User-Agent`` header built from
``settings.sec_user_agent`` (contact info per
https://www.sec.gov/os/accessing-edgar-data). No secret is involved (unlike
DART's ``crtfc_key``), so no log-masking filter is needed here. Politeness: no
method fans requests out in parallel -- each call issues exactly one request.

Design source mirrors ``app.clients.dart``: pure parse functions (network-free,
unit-tested directly) are split from the thin async I/O methods.
"""

import datetime
import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

# Generous but bounded: submissions/companyfacts bodies can run to several MB
# for large filers (many years of quarterly facts).
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

_SUBMISSIONS_PATH = "/submissions/CIK{cik10}.json"
_COMPANYFACTS_PATH = "/api/xbrl/companyfacts/CIK{cik10}.json"
# Served from www.sec.gov, not data.sec.gov -- a different host than
# settings.sec_base_url, so this is a fixed constant rather than built from it.
_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
# EDGAR archive root for primary documents (not under data.sec.gov either).
_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"


class SecClientError(RuntimeError):
    """Raised for client-side misconfiguration (e.g. a malformed CIK)."""


class SecApiError(RuntimeError):
    """Raised when the SEC API returns an unexpected/malformed response shape."""


def format_cik(cik: str | int) -> str:
    """Zero-pad a CIK to SEC's fixed 10-digit form (e.g. ``320193`` -> ``"0000320193"``).

    Raises :class:`SecClientError` for a non-numeric or over-length CIK -- we
    never guess/truncate an identifier that feeds directly into a URL path.
    """
    text = str(cik).strip()
    if not text.isdigit():
        raise SecClientError(f"CIK must be numeric, got {cik!r}")
    if len(text) > 10:
        raise SecClientError(f"CIK too long (max 10 digits), got {cik!r}")
    return text.zfill(10)


# -- submissions/CIK##########.json -------------------------------------------


@dataclass(frozen=True)
class SecFilingItem:
    """One cleaned filing from the ``submissions`` ``filings.recent`` block.

    The response is column-oriented (parallel same-length arrays keyed by field
    name, not a list of row objects like DART's ``list.json``); zipped into rows
    by :func:`parse_submissions_payload`. ``accession_number`` is the natural key
    that joins to :class:`SecFinancialItem` (via ``accn``) and to
    :func:`SecClient.fetch_document`.
    """

    accession_number: str
    form: str
    filing_date: datetime.date | None
    report_date: datetime.date | None
    primary_document: str


def _parse_sec_date(raw: str) -> datetime.date | None:
    """Parse a SEC ISO date string (``YYYY-MM-DD``) -> ``date``.

    Returns ``None`` (and logs) on empty/malformed input rather than raising --
    a single odd date must not abort a whole filings/facts fetch.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        logger.warning("SEC: unparseable date %r; storing None", raw)
        return None


def parse_submissions_payload(
    payload: Any, filing_types: list[str] | None = None
) -> list[SecFilingItem]:
    """Turn a ``submissions/CIK##########.json`` body into cleaned SecFilingItems.

    ``filing_types`` (e.g. ``["10-K"]``) filters by ``form``, case-insensitively;
    ``None`` returns every filing. Split from network I/O so offline fixtures
    exercise column-zipping and form filtering without a live call.

    [Inferred] response shape (``filings.recent`` parallel arrays: ``form``,
    ``accessionNumber``, ``filingDate``, ``reportDate``, ``primaryDocument``) --
    not verified against a live call in this offline step.
    """
    if not isinstance(payload, dict):
        raise SecApiError("submissions: unexpected response (not a JSON object)")

    filings = payload.get("filings")
    recent = filings.get("recent") if isinstance(filings, dict) else None
    if not isinstance(recent, dict):
        raise SecApiError("submissions: missing 'filings.recent' object")

    forms = recent.get("form")
    accession_numbers = recent.get("accessionNumber")
    if not isinstance(forms, list) or not isinstance(accession_numbers, list):
        # Defensive: status-000-equivalent "nothing here" -- not an error.
        logger.warning("submissions: 'form'/'accessionNumber' missing/not arrays")
        return []
    filing_dates = recent.get("filingDate") if isinstance(recent.get("filingDate"), list) else []
    report_dates = recent.get("reportDate") if isinstance(recent.get("reportDate"), list) else []
    primary_documents = (
        recent.get("primaryDocument") if isinstance(recent.get("primaryDocument"), list) else []
    )

    wanted = {t.strip().upper() for t in filing_types} if filing_types else None

    def _at(arr: list, i: int) -> str:
        val = arr[i] if i < len(arr) else None
        return val.strip() if isinstance(val, str) else ""

    items: list[SecFilingItem] = []
    for i in range(len(accession_numbers)):
        form = _at(forms, i)
        if wanted is not None and form.upper() not in wanted:
            continue
        items.append(
            SecFilingItem(
                accession_number=_at(accession_numbers, i),
                form=form,
                filing_date=_parse_sec_date(_at(filing_dates, i)),
                report_date=_parse_sec_date(_at(report_dates, i)),
                primary_document=_at(primary_documents, i),
            )
        )

    logger.info(
        "submissions: %d filing(s) total, %d after form filter %s",
        len(accession_numbers),
        len(items),
        sorted(wanted) if wanted else "-",
    )
    return items


# -- api/xbrl/companyfacts/CIK##########.json (financials) --------------------
#
# ★ Single source of truth for SEC numbers, mirroring dart.py's fnlttSinglAcntAll
# handling: every financial value comes from this structured API, never from
# document/LLM text. Only annual (form == "10-K") facts for the mapped tags
# below are extracted; parsing is deliberately over-defensive -- an ambiguous
# value is skipped (None), never fabricated.

# us-gaap tag -> standard metric key (mirrors dart.py's _ACCOUNT_ID_TO_METRIC).
# Two tags map to "revenue": companies report one or the other depending on
# whether they adopted ASC 606 (RevenueFromContractWithCustomerExcludingAssessedTax)
# or still use the legacy "Revenues" tag. Dict order matters: "Revenues" is
# preferred when a filer's payload somehow carries both for the same filing (see
# _dedup_by_accession_and_metric).
_US_GAAP_TO_METRIC: dict[str, str] = {
    "Revenues": "revenue",
    "RevenueFromContractWithCustomerExcludingAssessedTax": "revenue",
    "OperatingIncomeLoss": "operating_income",
    "NetIncomeLoss": "net_income",
    "EarningsPerShareBasic": "eps",
    "EarningsPerShareDiluted": "eps_diluted",
}

# EPS tags carry a per-share USD value (fractional) -- parsed as Decimal, never
# int, mirroring dart.py's parse_dart_decimal / _EPS_ACCOUNT_IDS split.
_EPS_TAGS = frozenset({"EarningsPerShareBasic", "EarningsPerShareDiluted"})

# Only annual facts are extracted in this step (docs task scope: "annual (10-K,
# form='10-K') USD facts"). Quarterly (10-Q) facts are a future extension.
_ANNUAL_FORM = "10-K"


def us_gaap_tag_to_metric(tag: str | None) -> str | None:
    """Map a us-gaap tag to our standard metric key, or ``None`` if unmapped."""
    return _US_GAAP_TO_METRIC.get((tag or "").strip())


def parse_sec_amount(raw: Any) -> int | None:
    """Parse a companyfacts amount (JSON number, unlike DART's string) -> int.

    SEC's JSON already types ``val`` as a number (Python's ``json`` module gives
    arbitrary-precision ``int`` for integer literals, so a multi-trillion-dollar
    figure round-trips exactly). A ``str`` is accepted defensively (some SEC
    frame endpoints have been observed to stringify values) and comma-stripped
    before parsing. Non-integer floats and unparseable strings return ``None`` +
    a warning -- per the project's core rule we never invent a number.

    Kept pure (no network) so its edge cases are unit-tested directly.
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        if raw.is_integer():
            return int(raw)
        logger.warning("companyfacts: non-integer float amount %r; storing None", raw)
        return None
    if isinstance(raw, str):
        text = raw.strip().replace(",", "")
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            logger.warning("companyfacts: unparseable amount %r; storing None", raw)
            return None
    return None


def parse_sec_decimal(raw: Any) -> Decimal | None:
    """Parse a companyfacts EPS value (JSON number, possibly a float) -> Decimal.

    SEC encodes EPS as a JSON float (e.g. ``6.13``), unlike DART's string
    amounts. ``Decimal(6.13)`` directly would capture the binary-float artifact
    (``6.12999999999999989...``), so the value always goes through ``str()``
    first -- Python's float ``repr``/``str`` already round-trips to the shortest
    exact decimal text, so ``Decimal(str(6.13)) == Decimal("6.13")``. Mirrors
    dart.py's ``parse_dart_decimal`` (exact Decimal, never float; never invent a
    number on a bad parse).

    Kept pure (no network) so its edge cases are unit-tested directly.
    """
    if raw is None or isinstance(raw, bool):
        return None
    if not isinstance(raw, (int, float, str)):
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        logger.warning("companyfacts: unparseable decimal value %r; storing None", raw)
        return None


@dataclass(frozen=True)
class SecFinancialItem:
    """One annual (10-K) us-gaap fact from companyfacts, mapped to a standard metric.

    Only facts whose tag is in :data:`_US_GAAP_TO_METRIC` are extracted -- unlike
    DART's ``fnlttSinglAcntAll.json`` (which returns a curated, already-small
    per-filing statement), companyfacts holds thousands of us-gaap tags per
    company, most irrelevant to our metric set.

    ``accession_number`` (``accn``) is the join key back to the
    :class:`SecFilingItem` that reported this fact -- a 10-K's companyfacts
    entries for a given tag include both the filing's own-period figure and
    prior-year comparative figures repeated for context, all tagged with the
    *same* ``accn``. ``fiscal_year`` is therefore derived from ``period_end``
    (the fact's own period), NOT from SEC's ``fy`` field -- ``fy`` describes the
    *filing's* fiscal period, so a comparative fact carries the filing's fy
    (e.g. 2025) even though its value belongs to an earlier year (e.g. 2023).
    The raw ``fy`` is preserved separately as ``filed_fiscal_year`` for
    provenance. ``value`` is ``Decimal`` for the two EPS tags, ``int`` for
    everything else (mirrors dart.py's ``FinancialItem.thstrm_amount`` union).
    """

    tag: str  # us-gaap element name, e.g. "Revenues"
    metric: str  # mapped standard key (unmapped tags are never emitted)
    accession_number: str  # accn -- joins to SecFilingItem.accession_number
    fiscal_year: int  # derived from period_end.year -- the FACT's own period
    filed_fiscal_year: int  # raw SEC fy -- the FILING's fiscal period (provenance only)
    fiscal_period: str  # fp, e.g. "FY"
    period_start: datetime.date | None  # start (duration facts only)
    period_end: datetime.date | None  # end
    value: int | Decimal  # Decimal for EPS tags, int otherwise
    unit: str  # "USD" or "USD/shares"
    form: str  # always "10-K" in this step (annual-only filter)
    filed: datetime.date | None  # filed


def _dedup_keep_latest_period(items: list[SecFinancialItem]) -> list[SecFinancialItem]:
    """Per (accession_number, metric), keep only the fact with the latest period_end.

    A 10-K's companyfacts entries repeat prior-year comparative figures under
    the same ``accn`` (see :class:`SecFinancialItem`); only the latest
    ``period_end`` is the filing's own-period figure, so every earlier
    comparative duplicate is dropped here. When two distinct tags map to the
    same metric (the two "revenue" tags) and tie on ``period_end``, the
    first-seen tag wins -- iteration order follows :data:`_US_GAAP_TO_METRIC`,
    so "Revenues" wins over "RevenueFromContractWithCustomerExcludingAssessedTax"
    (see that dict's ordering comment). Pure -> unit-tested.
    """
    best: dict[tuple[str, str], SecFinancialItem] = {}
    dropped = 0
    for it in items:
        key = (it.accession_number, it.metric)
        current = best.get(key)
        if current is None:
            best[key] = it
            continue
        if it.period_end is not None and (
            current.period_end is None or it.period_end > current.period_end
        ):
            best[key] = it
        dropped += 1
    if dropped:
        logger.debug(
            "companyfacts: dropped %d comparative/duplicate-metric row(s) "
            "(same accession+metric, earlier period_end)",
            dropped,
        )
    return list(best.values())


def parse_companyfacts_payload(payload: Any) -> list[SecFinancialItem]:
    """Turn a ``companyfacts/CIK##########.json`` body into SecFinancialItems.

    Only annual (``form == "10-K"``) facts for the mapped us-gaap tags are kept;
    amounts use :func:`parse_sec_amount` (int), EPS tags use
    :func:`parse_sec_decimal` (Decimal). One fact survives per
    (accession_number, metric) -- see :func:`_dedup_keep_latest_period` --
    since a 10-K repeats prior-year comparative figures under its own ``accn``.
    ``fiscal_year`` is derived from ``period_end.year``, not SEC's ``fy`` (see
    :class:`SecFinancialItem`); an entry whose ``end`` is missing/unparseable
    is skipped since fiscal_year cannot be derived. Split from network I/O so
    offline fixtures exercise tag selection, unit selection, annual filtering
    and dedup without a live call.

    [Inferred] response shape (``facts.us-gaap.<tag>.units.<unit>[]`` entries
    with ``val``/``fy``/``fp``/``start``/``end``/``form``/``accn``/``filed``) --
    not verified against a live call in this offline step.
    """
    if not isinstance(payload, dict):
        raise SecApiError("companyfacts: unexpected response (not a JSON object)")

    facts = payload.get("facts")
    us_gaap = facts.get("us-gaap") if isinstance(facts, dict) else None
    if not isinstance(us_gaap, dict):
        logger.warning("companyfacts: no 'facts.us-gaap' object present")
        return []

    items: list[SecFinancialItem] = []
    for tag, metric in _US_GAAP_TO_METRIC.items():
        fact = us_gaap.get(tag)
        if not isinstance(fact, dict):
            continue
        units = fact.get("units")
        if not isinstance(units, dict):
            continue

        is_eps = tag in _EPS_TAGS
        unit_key = "USD/shares" if is_eps else "USD"
        entries = units.get(unit_key)
        if not isinstance(entries, list):
            continue

        for entry in entries:
            if not isinstance(entry, dict) or entry.get("form") != _ANNUAL_FORM:
                continue
            value = (
                parse_sec_decimal(entry.get("val"))
                if is_eps
                else parse_sec_amount(entry.get("val"))
            )
            if value is None:
                continue
            filed_fy = entry.get("fy")
            if not isinstance(filed_fy, int):
                logger.warning("companyfacts: %s entry missing integer fy; skipping", tag)
                continue
            period_end = _parse_sec_date(str(entry.get("end") or ""))
            if period_end is None:
                logger.warning(
                    "companyfacts: %s entry missing/unparseable end date "
                    "(cannot derive fiscal_year); skipping",
                    tag,
                )
                continue
            items.append(
                SecFinancialItem(
                    tag=tag,
                    metric=metric,
                    accession_number=str(entry.get("accn") or "").strip(),
                    fiscal_year=period_end.year,
                    filed_fiscal_year=filed_fy,
                    fiscal_period=str(entry.get("fp") or "").strip(),
                    period_start=_parse_sec_date(str(entry.get("start") or "")),
                    period_end=period_end,
                    value=value,
                    unit=unit_key,
                    form=str(entry.get("form") or "").strip(),
                    filed=_parse_sec_date(str(entry.get("filed") or "")),
                )
            )

    deduped = _dedup_keep_latest_period(items)
    logger.info(
        "companyfacts: %d annual fact(s) -> %d after dedup",
        len(items),
        len(deduped),
    )
    return deduped


# -- EDGAR archive document fetch ---------------------------------------------


@dataclass(frozen=True)
class SecDocumentPayload:
    """A fetched primary document's raw bytes (fetch only; HTML parsing is next).

    Mirrors dart.py's ``DocumentPayload`` role: the caller normalizes/parses this
    in a later step. ``url`` is included so citations can link back to the
    original filing.
    """

    cik: str  # zero-padded 10-digit CIK
    accession_number: str
    primary_document: str
    url: str
    raw_bytes: bytes  # raw response bytes (HTML/XBRL-viewer doc; undecoded)


def _archive_url(cik10: str, accession_number: str, primary_document: str) -> str:
    """Build the EDGAR archive URL for a filing's primary document.

    [Inferred] URL shape (``/Archives/edgar/data/{cik-no-leading-zeros}/
    {accession-no-dashes}/{primary_document}``) -- widely documented but not
    verified against a live fetch in this offline step. The CIK segment drops
    leading zeros (``format_cik`` output is re-parsed to ``int``); the accession
    number's dashes are stripped for the path segment.
    """
    cik_int = str(int(cik10))
    accn_nodash = accession_number.replace("-", "")
    return f"{_ARCHIVES_BASE}/{cik_int}/{accn_nodash}/{primary_document}"


# -- files/company_tickers.json (search_company) ------------------------------


@dataclass(frozen=True)
class SecCompanyMatch:
    """One company_tickers.json record matched by :func:`search_company_matches`."""

    cik: str  # zero-padded 10-digit CIK
    ticker: str
    title: str


def parse_company_tickers_payload(payload: Any) -> list[SecCompanyMatch]:
    """Turn ``company_tickers.json`` (a dict of index -> record) into a flat list.

    [Inferred] response shape (``{"0": {"cik_str": int, "ticker": str,
    "title": str}, "1": {...}, ...}``) -- not verified against a live fetch in
    this offline step. A record with a non-numeric/missing ``cik_str`` is
    dropped defensively rather than raising, since one odd record must not
    abort the whole mapping load.
    """
    if not isinstance(payload, dict):
        raise SecApiError("company_tickers.json: unexpected response (not a JSON object)")

    records: list[SecCompanyMatch] = []
    for entry in payload.values():
        if not isinstance(entry, dict):
            continue
        cik_raw = entry.get("cik_str")
        if cik_raw is None:
            continue
        try:
            cik = format_cik(cik_raw)
        except SecClientError:
            logger.warning("company_tickers.json: skipping malformed cik_str %r", cik_raw)
            continue
        records.append(
            SecCompanyMatch(
                cik=cik,
                ticker=str(entry.get("ticker") or "").strip(),
                title=str(entry.get("title") or "").strip(),
            )
        )
    return records


def search_company_matches(
    records: list[SecCompanyMatch], query: str
) -> list[SecCompanyMatch]:
    """Match ``records`` by exact ticker first, else case-insensitive name substring.

    An exact (case-insensitive) ticker match is unambiguous and preferred; only
    when no ticker matches do we fall back to substring-matching the company
    title (which can return multiple hits). Pure -> unit-tested.
    """
    query = query.strip()
    if not query:
        return []
    query_upper = query.upper()
    ticker_matches = [r for r in records if r.ticker.upper() == query_upper]
    if ticker_matches:
        return ticker_matches
    query_lower = query.lower()
    return [r for r in records if query_lower in r.title.lower()]


def find_company_by_cik(
    records: list[SecCompanyMatch], cik: str | int
) -> SecCompanyMatch | None:
    """Return the company_tickers record whose CIK matches ``cik``, or ``None``.

    Unlike :func:`search_company_matches` (ticker/name substring), this is an
    exact identity lookup: the SEC ingest path already knows the CIK and only
    needs the display name/ticker. ``cik`` is normalized to the zero-padded
    10-digit form before comparison, so a caller may pass ``320193`` or
    ``"0000320193"``. ``None`` (rather than a raise) when the CIK is absent -- a
    filer without a ticker is not in company_tickers.json, and the caller falls
    back to a deterministic name rather than aborting. Pure -> unit-tested.
    """
    cik10 = format_cik(cik)
    return next((r for r in records if r.cik == cik10), None)


class SecClient:
    """Client for SEC EDGAR (https://data.sec.gov + https://www.sec.gov).

    Settings are injected; an ``httpx.AsyncClient`` may be injected for testing.
    Every request carries the required ``User-Agent`` header
    (``settings.sec_user_agent``). No API key/secret is involved.
    """

    def __init__(
        self, settings: Settings, client: httpx.AsyncClient | None = None
    ) -> None:
        self._settings = settings
        self._base_url = settings.sec_base_url.rstrip("/")
        self._user_agent = settings.sec_user_agent
        self._client = client
        self._owns_client = client is None
        # In-process memo of the parsed company_tickers.json mapping.
        self._ticker_cache: list[SecCompanyMatch] | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True)
        return self._client

    def _headers(self) -> dict[str, str]:
        # Required by SEC on every request (docs task scope item 3); no secret
        # travels here, so no masking filter is needed (unlike DART's crtfc_key).
        return {"User-Agent": self._user_agent}

    async def search_company(self, query: str) -> list[SecCompanyMatch]:
        """Search companies by ticker or name substring via company_tickers.json.

        SEC has no fuzzy search API; the mapping file is fetched once and cached
        in-process (:attr:`_ticker_cache`) for the lifetime of this client.
        """
        records = await self._load_company_tickers()
        return search_company_matches(records, query)

    async def resolve_company_by_cik(self, cik: str | int) -> SecCompanyMatch | None:
        """Resolve a CIK to its company_tickers.json display name/ticker.

        Reuses the same in-process cache as :meth:`search_company` (the mapping
        is fetched at most once per client). Returns ``None`` when the CIK is not
        in company_tickers (e.g. a filer with no ticker); the ingest caller then
        supplies a deterministic fallback name rather than failing, because a
        company's identity is its ``sec_cik`` natural key, not its display name.
        """
        records = await self._load_company_tickers()
        return find_company_by_cik(records, cik)

    async def _load_company_tickers(self) -> list[SecCompanyMatch]:
        if self._ticker_cache is not None:
            return self._ticker_cache
        client = self._get_client()
        logger.info("fetching %s", _COMPANY_TICKERS_URL)
        resp = await client.get(_COMPANY_TICKERS_URL, headers=self._headers())
        resp.raise_for_status()
        records = parse_company_tickers_payload(resp.json())
        self._ticker_cache = records
        logger.info("company_tickers.json: cached %d record(s)", len(records))
        return records

    async def list_filings(
        self, cik: str | int, filing_types: list[str] | None = None
    ) -> list[SecFilingItem]:
        """List filings for a CIK via the submissions API, optionally form-filtered.

        ``filing_types`` (e.g. ``["10-K"]``) filters by form, case-insensitively;
        ``None`` returns the full recent-filings history. No DB write happens
        here -- persistence is a later ingest step.
        """
        cik10 = format_cik(cik)
        client = self._get_client()
        url = f"{self._base_url}{_SUBMISSIONS_PATH.format(cik10=cik10)}"
        logger.info("fetching %s (filing_types=%s)", url, filing_types or "-")
        resp = await client.get(url, headers=self._headers())
        resp.raise_for_status()
        return parse_submissions_payload(resp.json(), filing_types)

    async def fetch_company_facts(self, cik: str | int) -> list[SecFinancialItem]:
        """Fetch structured XBRL company facts, mapped to standard metric keys.

        ★ Numbers must come only from this structured API, never from LLM text.
        Only annual (form == "10-K") facts for the mapped us-gaap tags are
        returned (see :data:`_US_GAAP_TO_METRIC`). No DB write happens here.
        """
        cik10 = format_cik(cik)
        client = self._get_client()
        url = f"{self._base_url}{_COMPANYFACTS_PATH.format(cik10=cik10)}"
        logger.info("fetching %s", url)
        resp = await client.get(url, headers=self._headers())
        resp.raise_for_status()
        return parse_companyfacts_payload(resp.json())

    async def fetch_document(
        self, cik: str | int, accession_number: str, primary_document: str
    ) -> SecDocumentPayload:
        """Fetch a filing's primary document's raw bytes from EDGAR archives.

        Fetch only -- no HTML parsing here (that is the next step, mirroring
        DART's document.xml -> prose split). No DB write happens here.
        """
        cik10 = format_cik(cik)
        url = _archive_url(cik10, accession_number, primary_document)
        client = self._get_client()
        logger.info("fetching %s", url)
        resp = await client.get(url, headers=self._headers())
        resp.raise_for_status()
        return SecDocumentPayload(
            cik=cik10,
            accession_number=accession_number,
            primary_document=primary_document,
            url=url,
            raw_bytes=resp.content,
        )

    async def aclose(self) -> None:
        """Close the underlying httpx client if this instance created it."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None
