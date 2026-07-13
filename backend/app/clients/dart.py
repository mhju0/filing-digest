"""DART (OpenDART) client -- corpCode / filing list / financials / document.xml.

Implements:
- ``corpCode.xml`` flow: fetch the ZIP, unzip in memory, parse ``CORPCODE.xml``
  with defusedxml, cache on disk, resolve ticker (stock_code) -> ``corp_code``.
- ``list.json`` flow: query the filing (공시) list for a corp_code + date range
  and return cleaned ``FilingItem`` records.
- ``fnlttSinglAcntAll.json`` flow: fetch a single company's full financial
  statements and return cleaned ``FinancialItem`` records (docs §3). This is the
  *single source of numbers* in the system: financial values come only from this
  structured API, never from LLM/document text.
- ``document.xml`` flow: fetch a filing's original document ZIP, decode its bytes
  (encoding trap -- see :func:`decode_dart_bytes`), detect its format
  (:func:`detect_document_format`), and extract *prose only* from the DSD format
  (:func:`extract_dsd_prose`). Numeric tables are excluded -- numbers come solely
  from the financials API (§3). This step stops at "raw bytes -> clean prose
  sections"; the downstream ingest pipeline handles chunking, embedding, and DB writes.

SECURITY:
- The API key lives in ``settings.dart_api_key`` as a SecretStr. Only call
  ``.get_secret_value()`` when building outgoing request params -- never log it,
  never put it in exceptions, and mask it as ``***`` in any logged URL/params.

The parsers encode response shapes measured against live DART responses; their
offline regression fixtures document the accepted variants.
"""

import datetime
import io
import json
import logging
import re
import zipfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

import httpx
from defusedxml.ElementTree import fromstring as _defused_fromstring

from app.config import Settings
from app.logging_config import install_source_logger_masking_filter

logger = logging.getLogger(__name__)

# backend/app/clients/dart.py -> parents[2] == backend/
_BACKEND_DIR = Path(__file__).resolve().parents[2]
# 3.5MB ZIP / ~118k records: cache the parsed listed-company subset locally so
# we do not re-download on every call. Regenerable -> gitignored (not committed).
DEFAULT_CACHE_PATH = _BACKEND_DIR / "data" / "corpcode_snapshot.json"

# corpCode.xml is served as a ZIP whose magic bytes are "PK". An error response
# is instead a plain ``<result><status>...</status></result>`` XML (see notes §5).
_ZIP_MAGIC = b"PK"

# Generous timeouts: the corpCode ZIP is roughly 3.5 MB.
_TIMEOUT = httpx.Timeout(60.0, connect=10.0)

# JSON status codes measured from DART. Only these two are non-fatal;
# any other code is surfaced as a DartApiError.
_STATUS_OK = "000"  # 정상 [Verified]
_STATUS_NO_DATA = "013"  # 조회된 데이터 없음(무자료) -> empty result, not an error

# DART filing viewer link. rcept_no is the natural join key to document.xml and
# financials; filings.url is built from it.
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
    """One cleaned filing from ``list.json``.

    This is the raw-ish source object the later ingest step maps onto the
    ``filings`` table. Mapping:
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
# comes from this structured API; we never read an
# amount from LLM output or document prose. Parsing is deliberately over-
# defensive: when a value is ambiguous we return None / skip the row and log --
# we never fabricate a number.

# account_id (IFRS/DART taxonomy) -> standard metric key (MetricCard.key).
# account_nm differs per company (삼성 labels revenue as "영업수익"), so we key
# off the stable account_id and keep account_nm only as a display label.
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
# subset appears under IS/CIS) with identical values. Keep only the IS row
# *per account_id* so the financials
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

    Format rules measured against live responses:
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
    happens to be the integer ``"2131"`` in the reference fixture.

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
    """One cleaned row from ``fnlttSinglAcntAll.json``.

    The raw-ish source object the later ingest step maps onto the ``financials``
    table:
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


# -- document.xml (원문 -> 산문 텍스트) ---------------------------------------
#
# document.xml is the source of filing_chunks.content.
# ★ CORE RULE: only *prose* (narrative) text is embedded. Numbers live solely in
# the financials API (§3), so numeric tables are excluded here -- we never mix a
# figure into prose. This step stops at "raw bytes -> clean prose sections";
# Chunking, embedding, and DB writes live in the downstream ingest modules.

# How much of the document head to sniff for a format marker. The DSD root
# (<DOCUMENT ... dart4.xsd>) or the xforms root (<html>) always appears well
# within the first few KB, even after an <?xml?> declaration (docs §4).
_SNIFF_LEN = 4096

# DSD (정기보고서) markers: the custom DART schema reference and DSD-only tags
# (docs §4 [Verified]). dart4.xsd is the single most reliable marker.
_DSD_MARKERS = ("dart4.xsd", "<document-name", "nonamespaceschemalocation")
# Root <DOCUMENT ...> as a real tag (the trailing class guards against matching
# an unrelated word like <documentation>).
_DSD_ROOT_RE = re.compile(r"<document[\s>/]")

# DSD element tags. DSD is a no-namespace schema (noNamespaceSchemaLocation,
# docs §4), so tags are plain -- SECTION-1/SECTION-2/TITLE/P/TABLE.
_SECTION_PREFIX = "SECTION"  # SECTION-1 / SECTION-2 (nested); chunk boundaries.
_TITLE_TAG = "TITLE"  # section header -> ProseSection.section_title (§4).
_PROSE_TAG = "P"  # narrative paragraph -> ProseSection.content (§4).
_TABLE_TAG = "TABLE"  # numeric/XBRL table -> EXCLUDED from prose (§3/§4 rule).

# Defensive: warn (do not fail) if a document is far larger than the observed
# ~6MB 사업보고서. We parse the whole string for correctness; streaming is not
# implemented.
_LARGE_DOCUMENT_WARN_CHARS = 20_000_000

# Leading noise to skip before sniffing the root tag: a UTF-8 BOM (decode keeps
# it -- we use "utf-8", not "utf-8-sig") plus ordinary whitespace.
_LEADING_SKIP = chr(0xFEFF) + " \t\r\n"

# DART DSD documents are NOT strictly well-formed XML: prose carries literal '&'
# (e.g. "R&D") and literal '<' (e.g. "< TV 시장점유율 추이 >") that expat rejects
# (docs §4 [Verified] -- the 삼성 2023 사업보고서 body has 529 bare '&' + 8 bare
# '<'). We conservatively escape ONLY the clearly-non-markup cases so the genuine
# tag structure is left untouched:
#   - '&' not starting a valid XML entity              -> '&amp;'
#   - '<' not starting a tag/comment/PI (name, /, !, ?) -> '&lt;'
# This lets the XXE-safe defusedxml parser read the whole 6MB body; the recovered
# SECTION/TITLE/P counts then match the docs §4 measurements [Verified].
_BARE_AMP_RE = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)")
_BARE_LT_RE = re.compile(r"<(?![a-zA-Z/!?])")
# Prose pseudo-tags that DO start with a letter: angle-bracket quotations like
# "<ACI 세미나>" (SK Hynix 2025) and "<Manufacturing Excellence>" (Hyundai 2025)
# [Verified] — expat reads them as tags and dies where an attribute belongs.
# A real XML attribute section always contains '=' (and quotes), so a
# letter-led "tag" whose body has whitespace but no '='/'"' can only be prose;
# the trailing character must not be '/' so self-closing tags stay markup.
_PROSE_ANGLE_RE = re.compile(r"<([A-Za-z][\w.-]*\s[^<>=\"']*[^<>=\"'/\s])>")
# Attribute values with unescaped embedded quotes: ENG=""Snow Corporation"
# (NAVER 2025 사업보고서 [Verified]). The stray inner quote is dropped so the
# attribute becomes well-formed; a genuinely empty value followed by another
# attribute never matches because the next attribute carries its own '='.
_DOUBLED_ATTR_QUOTE_RE = re.compile(r'=""([^"<>=]+)"')


@dataclass(frozen=True)
class ProseSection:
    """One narrative section extracted from a DSD document (docs §4).

    The raw-ish source object the downstream chunking step maps onto the
    ``filing_chunks`` table:
        filing_chunks.content     <- content      (prose only; tables excluded)
        filing_chunks.meta        <- {rcept_no, section_title}   (citation anchor)
        filing_chunks.chunk_index <- order  (section order within the document)
    ``section_title`` is the ``<TITLE>`` text (a natural chunk header) or ``None``
    when a section carries no title. No number ever appears here: numeric tables
    (``<TABLE>``) are dropped so figures come only from the financials API (§3).
    """

    section_title: str | None
    content: str
    order: int


@dataclass(frozen=True)
class DocumentPayload:
    """The selected body document from a ``document.xml`` ZIP (docs §4).

    ``content`` is the *raw, undecoded* bytes of the chosen member so the caller
    normalizes encoding explicitly via :func:`decode_dart_bytes` (the DART
    encoding trap -- euc-kr declared but UTF-8 bytes -- means we never trust the
    declaration; docs §4). ``member_names`` lists every ZIP member so attachments
    (``{rcept_no}_NNNNN.xml``) are visible; attachments are deliberately not
    ingested, so only the body member is returned as ``content`` here.
    """

    rcept_no: str
    filename: str  # chosen body member (usually "{rcept_no}.xml")
    content: bytes  # raw bytes of the body member (decode via decode_dart_bytes)
    member_names: tuple[str, ...]  # all ZIP members (body + attachments)


def decode_dart_bytes(raw: bytes) -> str:
    """Decode DART document bytes to ``str``, ignoring any charset declaration.

    The DART encoding trap (docs §4 [Verified]): xforms documents declare
    ``<meta charset=euc-kr>`` but the actual bytes are UTF-8, and DSD documents
    carry no charset at all. So we never trust the declaration -- we decode by
    trying the bytes, UTF-8 first:

    1. UTF-8 strict -- if it succeeds, the bytes *are* UTF-8 (this covers both the
       DSD case and the mislabelled-euc-kr xforms case). Real cp949/euc-kr Korean
       bytes almost always fail UTF-8 strict, so a success is trustworthy.
    2. cp949 fallback -- a superset of euc-kr; covers a genuinely legacy-encoded
       document.

    A charset-detection library would be over-engineering for a two-format,
    two-encoding corpus -- the ordered strict attempts are sufficient [Inferred].
    If *both* strict decodes fail (not observed in the wild), we log and fall back
    to a lossy UTF-8 decode rather than crash the whole ingest for one bad file.

    Pure (no network) so the encoding trap is unit-tested directly.
    """
    for encoding in ("utf-8", "cp949"):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        logger.debug("decode_dart_bytes: decoded %d bytes as %s", len(raw), encoding)
        return text
    # Neither strict decode worked -> never crash/fabricate: lossy utf-8 + warn.
    logger.warning(
        "decode_dart_bytes: %d bytes decoded as neither utf-8 nor cp949; falling "
        "back to lossy utf-8 (some characters replaced)",
        len(raw),
    )
    return raw.decode("utf-8", errors="replace")


def detect_document_format(text: str) -> Literal["dsd", "xforms", "unknown"]:
    """Sniff a decoded document's format from its head (docs §4 [Verified]).

    - ``"dsd"``    -- 정기보고서(사업보고서 등): DART custom XML whose root
      ``<DOCUMENT ... noNamespaceSchemaLocation="dart4.xsd">`` and ``<DOCUMENT-NAME>``
      are DSD-only markers.
    - ``"xforms"`` -- 자율/수시공시: an ``<html>`` root with xforms styling.
    - ``"unknown"``-- neither marker found. We do NOT guess a parser for it; the
      caller logs and skips (project rule: when ambiguous, skip -- never invent).

    Only ``"dsd"`` is parsed downstream in this step; the xforms branch is
    detection-only; unsupported xforms documents are skipped. Pure -> unit-tested.
    """
    head = text[:_SNIFF_LEN].lstrip(_LEADING_SKIP).lower()
    if any(marker in head for marker in _DSD_MARKERS) or _DSD_ROOT_RE.search(head):
        return "dsd"
    if "<html" in head:
        return "xforms"
    return "unknown"


def _strip_xml_declaration(text: str) -> str:
    """Drop a leading ``<?xml ... ?>`` declaration from a decoded document.

    We have already normalized the bytes to ``str`` (:func:`decode_dart_bytes`),
    but a leftover declaration carrying ``encoding="euc-kr"`` makes ``ElementTree``
    reject a ``str`` input ("Unicode strings with encoding declaration are not
    supported"). Removing the declaration lets us parse the already-correct
    ``str`` directly, sidestepping the declaration/bytes mismatch (docs §4). No
    declaration -> returned unchanged.
    """
    head = text.lstrip(_LEADING_SKIP)
    if head.startswith("<?xml"):
        end = head.find("?>")
        if end != -1:
            return head[end + 2 :]
    return text


def _repair_dsd_markup(text: str) -> str:
    """Escape DART's literal ``&``/``<`` in prose so defusedxml can parse (docs §4).

    DART DSD bodies embed unescaped ``&`` and ``<`` in narrative text, which is
    not well-formed XML. Only the clearly-non-markup occurrences are escaped (see
    :data:`_BARE_AMP_RE` / :data:`_BARE_LT_RE`), leaving real tags intact. Pure ->
    unit-tested (indirectly, via :func:`extract_dsd_prose`).
    """
    text = _BARE_AMP_RE.sub("&amp;", text)
    text = _DOUBLED_ATTR_QUOTE_RE.sub(r'="\1"', text)
    text = _PROSE_ANGLE_RE.sub(r"&lt;\1&gt;", text)
    return _BARE_LT_RE.sub("&lt;", text)


def _localname(tag: object) -> str:
    """Return an element's local tag name (namespace stripped; '' if non-string).

    ElementTree yields a callable ``tag`` for comments/PIs -- those coerce to ''.
    DSD is a no-namespace schema, but we strip any ``{ns}`` prefix defensively.
    """
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1] if tag.startswith("{") else tag


def _is_section_tag(tag: object) -> bool:
    """True for a DSD section element (``SECTION-1``/``SECTION-2``/... ; §4)."""
    return _localname(tag).upper().startswith(_SECTION_PREFIX)


def _normalize_ws(text: str) -> str:
    """Collapse whitespace/newline runs to single spaces; strip the ends.

    Deliberately light (docs §4: "과하지 않게") -- it tidies tag-stripped text
    without altering meaning, and drops empty ``<P>`` (``"".split() == []``).
    """
    return " ".join(text.split())


def _collect_section_prose(section_el: Any) -> tuple[str | None, str, int]:
    """Collect ``(title, prose, tables_skipped)`` for ONE section, not its kids.

    Walks the section's subtree but STOPS at nested ``SECTION-*`` boundaries (each
    nested section becomes its own :class:`ProseSection`), so prose is never
    double-counted. Rules (docs §4):
    - first ``<TITLE>`` text -> ``section_title`` (a natural chunk header).
    - each ``<P>`` -> one normalized prose paragraph (its ``<SPAN>`` text included).
    - every ``<TABLE>`` -> skipped and counted. Tables (esp. ``ACLASS="EXTRACTION"``)
      are numeric/XBRL; numbers come only from the financials API (§3), so no table
      text is mixed into prose -- even a table sitting mid-narrative.
    """
    title: str | None = None
    prose_parts: list[str] = []
    tables_skipped = 0

    def _walk(node: Any) -> None:
        nonlocal title, tables_skipped
        for child in node:
            tag = _localname(child.tag)
            if _is_section_tag(tag):
                continue  # nested section -> handled as its own ProseSection
            if tag == _TITLE_TAG:
                if title is None:
                    candidate = _normalize_ws("".join(child.itertext()))
                    if candidate:
                        title = candidate
                continue  # itertext already captured the title's text
            if tag == _TABLE_TAG:
                tables_skipped += 1  # numeric table -> excluded from prose (§3/§4)
                continue
            if tag == _PROSE_TAG:
                paragraph = _normalize_ws("".join(child.itertext()))
                if paragraph:
                    prose_parts.append(paragraph)
                continue
            _walk(child)  # wrapper element -> descend to find TITLE/P/TABLE

    _walk(section_el)
    return title, "\n".join(prose_parts), tables_skipped


def extract_dsd_prose(text: str) -> list[ProseSection]:
    """Extract prose (narrative) sections from a DSD document (docs §4).

    Parses the decoded DSD XML (defusedxml -- XXE/billion-laughs safe) and returns
    one :class:`ProseSection` per ``SECTION-*`` element in document order,
    carrying its ``<TITLE>`` and concatenated ``<P>`` prose. Numeric tables are
    dropped (see :func:`_collect_section_prose`) so figures come only from the
    financials API (§3). Empty sections -- no title *and* no prose (e.g. a shell
    holding only nested sub-sections whose content is emitted separately) -- are
    omitted; a title-only section is kept (it is a meaningful TOC header).

    ProseSection -> filing_chunks mapping (the NEXT step wires this to the DB):
        content <- content, meta <- {rcept_no, section_title}, chunk_index <- order.

    Whole-string parse for correctness on the reference 사업보고서. Pure (no
    network) -> unit-tested.
    """
    if len(text) > _LARGE_DOCUMENT_WARN_CHARS:
        logger.warning(
            "extract_dsd_prose: unusually large document (%d chars); parsing the "
            "whole string (streaming is not implemented)",
            len(text),
        )
    # defusedxml on the decoded str: strip the declaration (so ElementTree accepts
    # a str even when the original declared euc-kr) and repair DART's literal
    # &/< in prose (docs §4). Both are pure string ops -- no XXE surface.
    root = _defused_fromstring(_repair_dsd_markup(_strip_xml_declaration(text)))

    sections: list[ProseSection] = []
    tables_skipped = 0
    for el in root.iter():
        if not _is_section_tag(el.tag):
            continue
        title, content, dropped = _collect_section_prose(el)
        tables_skipped += dropped
        if title is None and not content:
            continue  # empty section -> nothing to embed
        sections.append(
            ProseSection(section_title=title, content=content, order=len(sections))
        )

    logger.info(
        "extract_dsd_prose: %d prose section(s), %d numeric table(s) excluded",
        len(sections),
        tables_skipped,
    )
    return sections


def _select_document_member(names: list[str], rcept_no: str) -> str:
    """Pick the body-document member from a ``document.xml`` ZIP (docs §4).

    The body is ``{rcept_no}.xml``; attachments are ``{rcept_no}_NNNNN.xml``
    (docs §4 [Verified]). Preference: exact ``{rcept_no}.xml`` -> an ``.xml`` member
    whose stem has no ``_`` attachment suffix -> the first candidate. Raises
    :class:`DartApiError` on an empty ZIP. Pure -> unit-tested.
    """
    if not names:
        raise DartApiError("document.xml ZIP contained no members")

    def _base(name: str) -> str:
        return name.rsplit("/", 1)[-1]

    xml_members = [n for n in names if _base(n).lower().endswith(".xml")]
    candidates = xml_members or names

    target = f"{rcept_no}.xml".lower()
    for name in candidates:
        if _base(name).lower() == target:
            return name
    for name in candidates:
        stem = _base(name).rsplit(".", 1)[0]
        if "_" not in stem:  # attachments carry a "_NNNNN" suffix
            return name
    return candidates[0]


def _extract_document_member(
    zip_bytes: bytes, rcept_no: str
) -> tuple[str, bytes, tuple[str, ...]]:
    """Open the document ZIP in memory; return (member, raw_bytes, all_names)."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        member = _select_document_member(names, rcept_no)
        return member, zf.read(member), tuple(names)


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
        # Guarantee the crtfc_key is masked out of httpx's request-URL logs even
        # when the app entry point (app.main -> configure_logging) was not run --
        # e.g. a live test that constructs the client directly. Idempotent.
        install_source_logger_masking_filter()

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

    # -- API endpoints -------------------------------------------------------
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

        Status handling:
        - ``000`` -> parse ``list`` into FilingItems.
        - ``013`` (무자료/no data) -> return ``[]`` (not an error).
        - anything else (``010`` bad key, ``020`` rate limit, ...) -> DartApiError.

        This intentionally fetches one page only; callers that need more than
        ``page_count`` rows must paginate explicitly.
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

    async def fetch_company_eng_name(self, corp_code: str) -> str | None:
        """Fetch a company's English name via ``company.json`` (기업개황).

        Returns ``corp_name_eng`` (stripped) when present, else ``None``. This
        is *enrichment* for the bilingual ``companies.name_en`` column -- it is
        NOT a source of numbers. Status handling mirrors list.json (§5): ``000``
        -> parse; ``013`` (무자료) -> ``None``; anything else -> DartApiError.

        No DB write happens here -- the ingest step persists the value.
        """
        params: dict[str, str] = {
            "crtfc_key": self._api_key(),
            "corp_code": corp_code,
        }
        client = self._get_client()
        # crtfc_key is masked; log only the non-secret query shape.
        logger.info(
            "fetching %s/company.json (crtfc_key=***, corp_code=%s)",
            self._base_url,
            corp_code,
        )
        resp = await client.get(f"{self._base_url}/company.json", params=params)
        resp.raise_for_status()
        return self._parse_company_eng_name(resp.json())

    @staticmethod
    def _parse_company_eng_name(payload: Any) -> str | None:
        """Extract ``corp_name_eng`` from a ``company.json`` body (pure).

        Split from network I/O so offline fixtures exercise status branching and
        field cleaning without a live call. Returns ``None`` (never a fabricated
        name) when the API reports no data or the English name is blank/absent.
        """
        if not isinstance(payload, dict):
            raise DartApiError("company.json: unexpected response (not a JSON object)")

        status = str(payload.get("status", "")).strip()
        message = str(payload.get("message", "")).strip()

        if status == _STATUS_NO_DATA:
            logger.info("company.json: no data (status 013); no English name")
            return None
        if status != _STATUS_OK:
            raise DartApiError(f"company.json returned status {status}: {message}")

        eng = str(payload.get("corp_name_eng") or "").strip()
        return eng or None

    async def fetch_document(self, rcept_no: str) -> DocumentPayload:
        """Fetch a filing's original document via ``document.xml`` (§4).

        Returns the *body* member of the response ZIP as raw bytes. The caller
        normalizes it explicitly: :func:`decode_dart_bytes` (encoding trap) ->
        :func:`detect_document_format` -> :func:`extract_dsd_prose` (DSD only).
        No DB write happens here -- persistence is the later ingest step.

        Error handling mirrors the corpCode ZIP endpoint (docs §5): a normal
        response is a ZIP (magic ``PK``); an error is a ``<result><status>`` XML,
        surfaced as :class:`DartApiError` (the API key is never referenced).

        Attachments (``{rcept_no}_NNNNN.xml``) are listed in ``member_names`` but
        not returned as content; xforms documents are detected but not parsed;
        document bodies are parsed whole.
        """
        content = await self._fetch_document_zip(rcept_no)
        if content[:2] != _ZIP_MAGIC:
            status = _error_status_from_xml(content)
            raise DartApiError(f"document.xml returned status {status}")
        member, body, names = _extract_document_member(content, rcept_no)
        logger.info(
            "document.xml: rcept_no=%s, %d member(s), body=%s (%d bytes)",
            rcept_no,
            len(names),
            member,
            len(body),
        )
        return DocumentPayload(
            rcept_no=rcept_no,
            filename=member,
            content=body,
            member_names=names,
        )

    async def _fetch_document_zip(self, rcept_no: str) -> bytes:
        client = self._get_client()
        # crtfc_key is masked; log only the non-secret query shape.
        logger.info(
            "fetching %s/document.xml (crtfc_key=***, rcept_no=%s)",
            self._base_url,
            rcept_no,
        )
        resp = await client.get(
            f"{self._base_url}/document.xml",
            params={"crtfc_key": self._api_key(), "rcept_no": rcept_no},
        )
        resp.raise_for_status()
        return resp.content

    async def aclose(self) -> None:
        """Close the underlying httpx client if this instance created it."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None
