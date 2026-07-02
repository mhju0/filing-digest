"""DART (OpenDART) client -- corpCode resolution + filing list (Phase 2).

Implements:
- ``corpCode.xml`` flow: fetch the ZIP, unzip in memory, parse ``CORPCODE.xml``
  with defusedxml, cache on disk, resolve ticker (stock_code) -> ``corp_code``.
- ``list.json`` flow: query the filing (공시) list for a corp_code + date range
  and return cleaned ``FilingItem`` records (see docs/dart-api-notes.md §2).
- ``fnlttSinglAcntAll.json`` flow: fetch a single company's full financial
  statements and return cleaned ``FinancialItem`` records (docs §3). This is the
  *single source of numbers* in the system: financial values come only from this
  structured API, never from LLM/document text.

``search_company`` remains a stub. ``document.xml`` ingest (chunking / DB writes)
is the next step and is intentionally not touched here.

SECURITY:
- The API key lives in ``settings.dart_api_key`` as a SecretStr. Only call
  ``.get_secret_value()`` when building outgoing request params -- never log it,
  never put it in exceptions, and mask it as ``***`` in any logged URL/params.

Design source of truth: docs/dart-api-notes.md, §1 (corpCode.xml) and §2
(list.json). Status codes follow §5.
"""

import datetime
import io
import json
import logging
import zipfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx
from defusedxml.ElementTree import fromstring as _defused_fromstring

from app.config import Settings

logger = logging.getLogger(__name__)

# backend/app/clients/dart.py -> parents[2] == backend/
_BACKEND_DIR = Path(__file__).resolve().parents[2]
# 3.5MB ZIP / ~118k records: cache the parsed listed-company subset locally so
# we do not re-download on every call. Regenerable -> gitignored (not committed).
DEFAULT_CACHE_PATH = _BACKEND_DIR / "data" / "corpcode_snapshot.json"

# corpCode.xml is served as a ZIP whose magic bytes are "PK". An error response
# is instead a plain ``<result><status>...</status></result>`` XML (see notes §5).
_ZIP_MAGIC = b"PK"

# Generous timeouts: the corpCode ZIP is ~3.5MB (see docs/dart-api-notes.md §1).
_TIMEOUT = httpx.Timeout(60.0, connect=10.0)

# JSON status codes (docs/dart-api-notes.md §5). Only these two are non-fatal;
# any other code is surfaced as a DartApiError.
_STATUS_OK = "000"  # 정상 [Verified]
_STATUS_NO_DATA = "013"  # 조회된 데이터 없음(무자료) -> empty result, not an error

# DART filing viewer link. rcept_no is the natural join key to document.xml and
# financials; filings.url is built from it (docs/dart-api-notes.md §2, §6).
_VIEWER_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"


class DartClientError(RuntimeError):
    """Raised for client-side misconfiguration (e.g. missing API key)."""


class DartApiError(RuntimeError):
    """Raised when the DART API returns a non-OK ``status`` code.

    The DART ``status`` code and ``message`` are included, but the API key is
    never referenced -- keys live only in request params (masked in logs).
    """


@dataclass(frozen=True)
class FilingItem:
    """One cleaned filing from ``list.json`` (docs/dart-api-notes.md §2).

    This is the raw-ish source object the later ingest step maps onto the
    ``filings`` table. Mapping (see docs/dart-api-notes.md §6):
        filings.title   <- report_nm  (already right-trimmed here)
        filings.filed_at<- rcept_dt   (parsed YYYYMMDD -> date)
        filings.url     <- viewer_url (derived from rcept_no)
    ``rcept_no`` / ``corp_code`` are the join keys the next steps (financials,
    document.xml) consume.
    """

    rcept_no: str
    corp_code: str
    corp_name: str
    report_nm: str  # right-trimmed: report_nm has trailing space padding [Verified]
    flr_nm: str
    rcept_dt: datetime.date | None  # parsed from YYYYMMDD; None if unparseable
    rm: str
    stock_code: str
    corp_cls: str

    @property
    def viewer_url(self) -> str:
        """DART original-document viewer link for this filing (filings.url)."""
        return _VIEWER_URL.format(rcept_no=self.rcept_no)


def _parse_rcept_dt(raw: str) -> datetime.date | None:
    """Parse a DART ``rcept_dt`` (``YYYYMMDD`` string) into a ``date``.

    Returns ``None`` (and logs) on empty/malformed input rather than raising --
    a single odd date must not abort a whole list fetch.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.datetime.strptime(raw, "%Y%m%d").date()
    except ValueError:
        logger.warning("list.json: unparseable rcept_dt %r; storing None", raw)
        return None


def _filing_item_from_row(row: dict[str, Any]) -> FilingItem:
    """Build a :class:`FilingItem` from one raw ``list[]`` element.

    Defensive against missing keys / non-string values (JSON is trusted less
    than the notes imply): every field is coerced to ``str`` before cleaning.
    """

    def _s(key: str) -> str:
        val = row.get(key)
        return val.strip() if isinstance(val, str) else ""

    return FilingItem(
        rcept_no=_s("rcept_no"),
        corp_code=_s("corp_code"),
        corp_name=_s("corp_name"),
        # report_nm carries trailing space padding in DART responses [Verified].
        report_nm=_s("report_nm"),
        flr_nm=_s("flr_nm"),
        rcept_dt=_parse_rcept_dt(_s("rcept_dt")),
        rm=_s("rm"),
        stock_code=_s("stock_code"),
        corp_cls=_s("corp_cls"),
    )


def parse_corpcode_xml(xml_bytes: bytes) -> list[dict[str, str]]:
    """Parse ``CORPCODE.xml`` bytes into listed-company records.

    Only listed companies (``stock_code`` not blank -- DART pads non-listed
    companies with a single space) are returned, since that is all we resolve.
    Each record has: ``corp_code``, ``corp_name``, ``stock_code``, ``modify_date``.

    Uses defusedxml (XXE / billion-laughs safe). Bytes are passed through so the
    parser honours the XML declaration's encoding rather than a guessed one.
    """
    root = _defused_fromstring(xml_bytes)
    records: list[dict[str, str]] = []
    for el in root.iter("list"):
        stock_code = (el.findtext("stock_code") or "").strip()
        if not stock_code:  # non-listed: blank/space -> not our target
            continue
        records.append(
            {
                "corp_code": (el.findtext("corp_code") or "").strip(),
                "corp_name": (el.findtext("corp_name") or "").strip(),
                "stock_code": stock_code,
                "modify_date": (el.findtext("modify_date") or "").strip(),
            }
        )
    return records


def _extract_corpcode_member(zip_bytes: bytes) -> bytes:
    """Return the ``CORPCODE.xml`` member bytes from the corpCode ZIP."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        member = next(
            (n for n in names if n.upper().endswith("CORPCODE.XML")),
            names[0] if names else None,
        )
        if member is None:
            raise DartApiError("corpCode ZIP contained no members")
        return zf.read(member)


def _error_status_from_xml(xml_bytes: bytes) -> str:
    """Best-effort extraction of ``<status>`` from an error XML response."""
    try:
        root = _defused_fromstring(xml_bytes)
    except Exception:  # noqa: BLE001 -- malformed body; report as unknown
        return "unknown"
    return (root.findtext("status") or "unknown").strip() or "unknown"


# -- fnlttSinglAcntAll.json (financials) -------------------------------------
#
# ★ Single source of truth for numbers. Every financial value in the system
# comes from this structured API (docs/dart-api-notes.md §3); we never read an
# amount from LLM output or document prose. Parsing is deliberately over-
# defensive: when a value is ambiguous we return None / skip the row and log --
# we never fabricate a number.

# account_id (IFRS/DART taxonomy) -> standard metric key (MetricCard.key).
# account_nm differs per company (삼성 labels revenue as "영업수익"), so we key
# off the stable account_id and keep account_nm only as a display label
# (docs/dart-api-notes.md §3 [Verified]).
_ACCOUNT_ID_TO_METRIC: dict[str, str] = {
    "ifrs-full_Revenue": "revenue",
    "dart_OperatingIncomeLoss": "operating_income",  # DART extension taxonomy
    # net income comes in two flavours we keep separately (docs §3 [Verified]):
    #   total 당기순이익 and the owners-of-parent (지배주주귀속) subset. For 삼성
    #   2023 CFS: total 15.49조 > attributable 14.47조 (gap = 비지배지분).
    "ifrs-full_ProfitLoss": "net_income",
    "ifrs-full_ProfitLossAttributableToOwnersOfParent": "net_income_attributable",
    # per-share KRW, not an absolute amount; parsed as Decimal (may be non-integer
    # for other companies, docs §3 [Verified]). We keep basic + diluted EPS.
    "ifrs-full_BasicEarningsLossPerShare": "eps",
    "ifrs-full_DilutedEarningsLossPerShare": "eps_diluted",
}

# Profit-loss family accounts are emitted redundantly under multiple statements
# (ifrs-full_ProfitLoss appears once each under IS/CIS/CF; the owners-of-parent
# subset appears under IS/CIS) with identical values (docs/dart-api-notes.md §3
# [Verified]). Keep only the IS row *per account_id* so the financials
# UNIQUE(company_id, period, metric, source) constraint is not violated by the
# duplicates -- two distinct account_ids each keep their own IS row.
_PROFIT_LOSS_ACCOUNT_IDS = frozenset(
    {
        "ifrs-full_ProfitLoss",
        "ifrs-full_ProfitLossAttributableToOwnersOfParent",
    }
)
_PROFIT_LOSS_KEEP_SJ_DIV = "IS"

# EPS-family accounts carry a per-share value that can be fractional; they are
# parsed with parse_dart_decimal (exact Decimal) rather than parse_dart_amount
# (integer KRW). Everything else is an absolute KRW integer.
_EPS_ACCOUNT_IDS = frozenset(
    {
        "ifrs-full_BasicEarningsLossPerShare",
        "ifrs-full_DilutedEarningsLossPerShare",
    }
)


def parse_dart_amount(raw: str | None) -> int | None:
    """Parse a DART amount string (thstrm/frmtrm/bfefrmtrm_amount) -> int.

    Format rules measured against the live response (docs/dart-api-notes.md §3):
    - amounts are *strings* with no thousand separators; values are absolute KRW
      (원) integers with no unit scaling (e.g. ``"455905980000000"``).
    - negatives carry a leading ``-`` (e.g. ``"-4480835000000"``).
    - an absent value is the empty string ``""`` (not ``"-"``).

    Returns ``None`` for empty/whitespace input (a valid "no value" -> the caller
    skips the row) and for anything non-numeric -- logging a warning only in the
    latter case. Per the project's core rule we never invent a number: when the
    text is not an unambiguous integer we return ``None`` rather than guess.

    Kept pure (no network) so its edge cases are unit-tested directly.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        # "" / whitespace -> genuine empty cell, not a parse error (no warning).
        return None
    # §3 [Verified]: no thousand separators appear, but strip commas defensively
    # so a future comma-formatted response cannot silently corrupt a value.
    cleaned = text.replace(",", "")
    try:
        return int(cleaned)
    except ValueError:
        # Non-integer (letters, stray decimal, ...) -> do not fabricate a number.
        logger.warning(
            "fnlttSinglAcntAll.json: unparseable amount %r; storing None", raw
        )
        return None


def parse_dart_decimal(raw: str | None) -> Decimal | None:
    """Parse a DART per-share amount (EPS) string -> :class:`Decimal`.

    Same defensive rules as :func:`parse_dart_amount` (empty/whitespace/None ->
    ``None``; commas stripped; leading ``-`` = negative), but a decimal point is
    *allowed* -- EPS can be fractional for other companies even though 삼성 2023
    happens to be the integer ``"2131"`` (docs/dart-api-notes.md §3 [Verified]).

    Uses ``Decimal`` (never ``float``) so a value like ``"123.45"`` round-trips
    exactly with no binary-float rounding error. Per the project's core rule we
    never invent a number: an unparseable value returns ``None`` + a warning.

    Kept pure (no network) so its edge cases are unit-tested directly.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        # "" / whitespace -> genuine empty cell, not a parse error (no warning).
        return None
    # Strip commas defensively (0 seen in §3) so a future comma-formatted
    # per-share value cannot silently corrupt the parse.
    cleaned = text.replace(",", "")
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        # Non-numeric (letters, doubled sign, ...) -> do not fabricate a number.
        logger.warning(
            "fnlttSinglAcntAll.json: unparseable decimal amount %r; storing None",
            raw,
        )
        return None


def account_id_to_metric(account_id: str | None) -> str | None:
    """Map a DART ``account_id`` to our standard metric key, or ``None``.

    Unmapped accounts are intentionally *not* dropped by callers -- they keep
    their raw ``account_nm`` with ``metric=None`` so nothing is silently lost.
    """
    return _ACCOUNT_ID_TO_METRIC.get((account_id or "").strip())


@dataclass(frozen=True)
class FinancialItem:
    """One cleaned row from ``fnlttSinglAcntAll.json`` (docs/dart-api-notes.md §3).

    The raw-ish source object the later ingest step maps onto the ``financials``
    table (docs/dart-api-notes.md §6):
        financials.value        <- thstrm_amount (current-period amount)
        financials.metric       <- account_id -> standard key (``metric`` here)
        financials.fiscal_year  <- bsns_year
        financials.currency/unit<- currency (KRW, no scaling)
        financials.filing_id    <- rcept_no -> filings FK
    ``frmtrm_amount`` is the prior-period value used for YoY deltas. Amounts are
    already parsed (None == empty/unparseable -> skip on load); ``metric`` is
    ``None`` for accounts outside our mapping.

    Amount type [Inferred] -- single field, union type: absolute-KRW rows are
    parsed to ``int`` (integer 원, no fractional part) while EPS-family rows
    (``eps``/``eps_diluted``) are parsed to ``Decimal`` to preserve fractional
    per-share values exactly (docs §3). One field (rather than a split
    ``amount``/``amount_decimal`` pair) is the simplest shape that still maps
    cleanly onto ``financials.value numeric(24,4)``, which holds both -- and
    ``Decimal`` is not an ``int`` subclass, so callers can branch on type when a
    row's metric matters. The row's own ``metric`` disambiguates which it is.
    """

    rcept_no: str
    reprt_code: str
    bsns_year: str
    sj_div: str  # BS/IS/CIS/CF/SCE (재무제표 구분)
    sj_nm: str
    account_id: str
    account_nm: str  # company-specific label; display only
    # current period (financials.value source); Decimal for EPS, int otherwise.
    thstrm_amount: int | Decimal | None
    frmtrm_amount: int | Decimal | None  # prior period (YoY comparison)
    ord: int | None  # DART sort order
    currency: str
    metric: str | None  # standard MetricCard.key, or None if unmapped


def _parse_ord(raw: str) -> int | None:
    """Parse the ``ord`` sort-order field (string int) -> int; ``None`` if odd."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("fnlttSinglAcntAll.json: unparseable ord %r; storing None", raw)
        return None


def _financial_item_from_row(row: dict[str, Any]) -> FinancialItem:
    """Build a :class:`FinancialItem` from one raw ``list[]`` element.

    Defensive against missing keys / non-string values: amounts are coerced to
    text before parsing (EPS-family via :func:`parse_dart_decimal`, everything
    else via :func:`parse_dart_amount`), other fields to stripped ``str``.
    """

    def _s(key: str) -> str:
        val = row.get(key)
        return val.strip() if isinstance(val, str) else ""

    account_id = _s("account_id")
    # EPS-family rows carry a fractional per-share value -> parse as Decimal;
    # every other account is an integer KRW amount (docs §3).
    parse_amount = (
        parse_dart_decimal if account_id in _EPS_ACCOUNT_IDS else parse_dart_amount
    )

    def _amount(key: str) -> int | Decimal | None:
        val = row.get(key)
        if val is None:
            return None
        # Amounts are strings in §3; coerce a stray JSON number to text so it
        # still parses rather than silently becoming None.
        return parse_amount(val if isinstance(val, str) else str(val))

    return FinancialItem(
        rcept_no=_s("rcept_no"),
        reprt_code=_s("reprt_code"),
        bsns_year=_s("bsns_year"),
        sj_div=_s("sj_div"),
        sj_nm=_s("sj_nm"),
        account_id=account_id,
        account_nm=_s("account_nm"),
        thstrm_amount=_amount("thstrm_amount"),
        frmtrm_amount=_amount("frmtrm_amount"),
        ord=_parse_ord(_s("ord")),
        currency=_s("currency"),
        metric=account_id_to_metric(account_id),
    )


def _dedup_profit_loss(items: list[FinancialItem]) -> list[FinancialItem]:
    """Drop duplicate profit-loss-family rows, keeping only the IS one per account.

    Each profit-loss account is emitted identically under multiple statements
    (docs §3 [Verified]): total ``ifrs-full_ProfitLoss`` under IS/CIS/CF, and the
    owners-of-parent subset under IS/CIS. Dedup is grouped *by account_id*: for
    every account in :data:`_PROFIT_LOSS_ACCOUNT_IDS` keep sj_div=IS and discard
    its other-statement copies, so the later load does not violate the financials
    UNIQUE(company_id, period, metric, source) constraint. Two distinct
    profit-loss account_ids therefore each survive as their own single IS row;
    every non-profit-loss account passes through untouched. Pure -> unit-tested.
    """
    result: list[FinancialItem] = []
    dropped = 0
    for it in items:
        is_duplicate_pl = (
            it.account_id in _PROFIT_LOSS_ACCOUNT_IDS
            and it.sj_div != _PROFIT_LOSS_KEEP_SJ_DIV
        )
        if is_duplicate_pl:
            dropped += 1
            continue
        result.append(it)
    if dropped:
        logger.debug(
            "fnlttSinglAcntAll.json: dropped %d duplicate ProfitLoss row(s) "
            "(kept sj_div=%s)",
            dropped,
            _PROFIT_LOSS_KEEP_SJ_DIV,
        )
    return result


class DartClient:
    """Client for the OpenDART API (https://opendart.fss.or.kr).

    Settings are injected; an ``httpx.AsyncClient`` may be injected for testing,
    and a ``cache_path`` override lets tests point at a temp snapshot file.
    """

    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        cache_path: Path | None = None,
    ) -> None:
        self._settings = settings
        self._base_url = settings.dart_base_url.rstrip("/")
        self._client = client
        self._owns_client = client is None
        self._cache_path = cache_path or DEFAULT_CACHE_PATH
        # In-process memo of parsed listed-company records (list of dicts).
        self._records: list[dict[str, str]] | None = None

    # -- corpCode.xml -------------------------------------------------------

    async def resolve_corp_code(self, ticker: str) -> str | None:
        """Resolve a listed company's ``stock_code`` (ticker) -> ``corp_code``.

        Loads from the local snapshot cache if present, otherwise fetches the
        corpCode ZIP once, parses it, and writes the cache. Returns ``None`` if
        the ticker is not found among listed companies.
        """
        records = await self._load_corp_codes()
        key = ticker.strip()
        for rec in records:
            if rec.get("stock_code") == key:
                return rec.get("corp_code")
        return None

    async def refresh_corp_codes(self) -> list[dict[str, str]]:
        """Fetch corpCode.xml from the live API, parse, and rewrite the cache."""
        content = await self._fetch_corpcode_zip()
        if content[:2] != _ZIP_MAGIC:
            status = _error_status_from_xml(content)
            raise DartApiError(f"corpCode.xml returned status {status}")
        xml_bytes = _extract_corpcode_member(content)
        records = parse_corpcode_xml(xml_bytes)
        self._write_cache(records)
        self._records = records
        logger.info("corpCode snapshot refreshed: %d listed companies", len(records))
        return records

    async def _load_corp_codes(self) -> list[dict[str, str]]:
        if self._records is not None:
            return self._records
        cached = self._read_cache()
        if cached is not None:
            self._records = cached
            return cached
        return await self.refresh_corp_codes()

    async def _fetch_corpcode_zip(self) -> bytes:
        client = self._get_client()
        # crtfc_key is masked in the log; never emit its value.
        logger.info("fetching %s/corpCode.xml (crtfc_key=***)", self._base_url)
        resp = await client.get(
            f"{self._base_url}/corpCode.xml",
            params={"crtfc_key": self._api_key()},
        )
        resp.raise_for_status()
        return resp.content

    # -- cache --------------------------------------------------------------

    def _read_cache(self) -> list[dict[str, str]] | None:
        path = self._cache_path
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("corpCode cache unreadable at %s: %s; refetching", path, exc)
            return None
        records = payload.get("records") if isinstance(payload, dict) else None
        if not isinstance(records, list):
            logger.warning("corpCode cache malformed at %s; refetching", path)
            return None
        return records

    def _write_cache(self, records: list[dict[str, str]]) -> None:
        path = self._cache_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"records": records}, ensure_ascii=False),
            encoding="utf-8",
        )

    # -- helpers ------------------------------------------------------------

    def _api_key(self) -> str:
        secret = self._settings.dart_api_key
        value = secret.get_secret_value() if secret is not None else ""
        if not value:
            raise DartClientError(
                "DART_API_KEY is not configured (set it in the environment/.env)"
            )
        return value

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True)
        return self._client

    # -- API endpoints (search_company still a stub) ------------------------

    async def search_company(self, name: str) -> Any:
        """Search DART corp codes by company name.

        TODO(next step): the corpCode snapshot can back a name search, but the
        current step only implements ticker -> corp_code resolution.
        """
        raise NotImplementedError("DartClient.search_company: TODO(next step)")

    async def list_filings(
        self,
        corp_code: str,
        bgn_de: str,
        end_de: str,
        pblntf_ty: str | None = None,
        page_no: int = 1,
        page_count: int = 100,
    ) -> list[FilingItem]:
        """List filings (공시) for ``corp_code`` via ``list.json`` (§2).

        ``bgn_de`` / ``end_de`` are ``YYYYMMDD`` receipt-date bounds. ``pblntf_ty``
        is the coarse disclosure type (e.g. ``"A"`` = 정기공시). ``page_count`` is
        capped at 100 by DART.

        Returns cleaned :class:`FilingItem` records for a single page. No DB
        write happens here -- persistence is handled by the later ingest step.

        Status handling (docs/dart-api-notes.md §5):
        - ``000`` -> parse ``list`` into FilingItems.
        - ``013`` (무자료/no data) -> return ``[]`` (not an error).
        - anything else (``010`` bad key, ``020`` rate limit, ...) -> DartApiError.

        TODO(next step): iterate all pages using ``total_page`` when the caller
        needs the full history; this step intentionally fetches one page only.
        """
        params: dict[str, str] = {
            "crtfc_key": self._api_key(),
            "corp_code": corp_code,
            "bgn_de": bgn_de,
            "end_de": end_de,
            "page_no": str(page_no),
            "page_count": str(page_count),
        }
        if pblntf_ty:
            params["pblntf_ty"] = pblntf_ty

        client = self._get_client()
        # crtfc_key is masked; log only the non-secret query shape.
        logger.info(
            "fetching %s/list.json (crtfc_key=***, corp_code=%s, bgn_de=%s, "
            "end_de=%s, pblntf_ty=%s, page_no=%d)",
            self._base_url,
            corp_code,
            bgn_de,
            end_de,
            pblntf_ty or "-",
            page_no,
        )
        resp = await client.get(f"{self._base_url}/list.json", params=params)
        resp.raise_for_status()
        payload = resp.json()
        return self._parse_list_payload(payload)

    @staticmethod
    def _parse_list_payload(payload: Any) -> list[FilingItem]:
        """Turn a ``list.json`` response body into cleaned FilingItems.

        Split out from network I/O so offline fixtures exercise status branching
        and field cleaning without a live call.
        """
        if not isinstance(payload, dict):
            raise DartApiError("list.json: unexpected response (not a JSON object)")

        status = str(payload.get("status", "")).strip()
        message = str(payload.get("message", "")).strip()

        if status == _STATUS_NO_DATA:
            # 무자료: a valid "nothing in range" answer, not a failure.
            logger.info("list.json: no data (status 013); returning empty list")
            return []
        if status != _STATUS_OK:
            # Includes unmapped codes; the docs are the SSOT for meaning, and
            # anything absent there is treated as [Inferred]/fatal here.
            raise DartApiError(f"list.json returned status {status}: {message}")

        # Paging fields are JSON ints [Verified]. We only fetch page_no; surface
        # the totals so callers/logs can tell whether more pages exist.
        total_page = payload.get("total_page")
        total_count = payload.get("total_count")
        logger.info(
            "list.json: status 000, %s items on page %s/%s (total_count=%s)",
            payload.get("page_count"),
            payload.get("page_no"),
            total_page,
            total_count,
        )

        rows = payload.get("list")
        if not isinstance(rows, list):
            # status 000 with no/blank list -> treat as empty (defensive).
            logger.warning("list.json: status 000 but 'list' missing/not an array")
            return []
        return [_filing_item_from_row(r) for r in rows if isinstance(r, dict)]

    async def fetch_financials(
        self,
        corp_code: str,
        bsns_year: str,
        reprt_code: str,
        fs_div: str = "CFS",
    ) -> list[FinancialItem]:
        """Fetch full financial statements via ``fnlttSinglAcntAll.json`` (§3).

        ★ This is the single source of numbers in the system: financial values
        come only from this structured API, never from document/LLM text.

        Args:
            corp_code: DART 8-digit 고유번호.
            bsns_year: business year, ``YYYY`` (e.g. ``"2023"``).
            reprt_code: report code -- ``11011`` 사업보고서(annual) / ``11012``
                반기 / ``11013`` 1분기 / ``11014`` 3분기 (docs §3).
            fs_div: ``"CFS"`` 연결(consolidated) / ``"OFS"`` 별도(separate).

        Returns cleaned :class:`FinancialItem` records (profit-loss family
        de-duplicated to one IS row each, amounts parsed to ``int``/``Decimal``
        (EPS) ``| None``, ``account_id`` mapped to ``metric``). No DB write
        happens here; persistence is the later ingest step.

        Financials table mapping (docs §6): value<-thstrm_amount,
        metric<-account_id mapping, unit/currency<-KRW (no scaling),
        YoY<-frmtrm_amount, filing_id<-rcept_no.

        Status handling matches list.json (§5): ``000`` -> parse; ``013`` (무자료)
        -> ``[]``; anything else -> :class:`DartApiError`.
        """
        params: dict[str, str] = {
            "crtfc_key": self._api_key(),
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
            "fs_div": fs_div,
        }
        client = self._get_client()
        # crtfc_key is masked; log only the non-secret query shape.
        logger.info(
            "fetching %s/fnlttSinglAcntAll.json (crtfc_key=***, corp_code=%s, "
            "bsns_year=%s, reprt_code=%s, fs_div=%s)",
            self._base_url,
            corp_code,
            bsns_year,
            reprt_code,
            fs_div,
        )
        resp = await client.get(
            f"{self._base_url}/fnlttSinglAcntAll.json", params=params
        )
        resp.raise_for_status()
        payload = resp.json()
        return self._parse_financials_payload(payload)

    @staticmethod
    def _parse_financials_payload(payload: Any) -> list[FinancialItem]:
        """Turn a ``fnlttSinglAcntAll.json`` body into cleaned FinancialItems.

        Split from network I/O so offline fixtures exercise status branching,
        amount parsing, account mapping and ProfitLoss dedup without a live call.
        """
        if not isinstance(payload, dict):
            raise DartApiError(
                "fnlttSinglAcntAll.json: unexpected response (not a JSON object)"
            )

        status = str(payload.get("status", "")).strip()
        message = str(payload.get("message", "")).strip()

        if status == _STATUS_NO_DATA:
            logger.info(
                "fnlttSinglAcntAll.json: no data (status 013); returning empty list"
            )
            return []
        if status != _STATUS_OK:
            raise DartApiError(
                f"fnlttSinglAcntAll.json returned status {status}: {message}"
            )

        rows = payload.get("list")
        if not isinstance(rows, list):
            # status 000 with no/blank list -> treat as empty (defensive).
            logger.warning(
                "fnlttSinglAcntAll.json: status 000 but 'list' missing/not an array"
            )
            return []

        items = [_financial_item_from_row(r) for r in rows if isinstance(r, dict)]
        deduped = _dedup_profit_loss(items)
        logger.info(
            "fnlttSinglAcntAll.json: status 000, %d row(s) -> %d item(s) after "
            "ProfitLoss dedup",
            len(items),
            len(deduped),
        )
        return deduped

    async def aclose(self) -> None:
        """Close the underlying httpx client if this instance created it."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None
