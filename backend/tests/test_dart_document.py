"""Tests for DART document.xml -> clean prose (docs/dart-api-notes.md §4).

Offline and focused on the three pure functions this step adds:
- ``decode_dart_bytes``     -- the encoding trap (euc-kr declared, UTF-8 bytes).
- ``detect_document_format``-- DSD vs xforms vs unknown from the head.
- ``extract_dsd_prose``     -- prose only; numeric tables excluded; nesting/order.

``fetch_document`` is exercised offline via an ``httpx.MockTransport`` (ZIP body
and error-XML branches) and once live (skipped unless DART_API_KEY is set) on the
삼성 2023 사업보고서 rcept_no recorded in docs §4 -- a single network call whose
assertions are structural (format=dsd, non-empty prose, tables excluded), never a
hardcoded amount. The core project rule -- numbers come only from the financials
API (§3), never from prose -- is enforced here by excluding every ``<TABLE>``.
"""

import asyncio
import io
import logging
import os
import zipfile

import httpx
import pytest
from pydantic import SecretStr

from app.clients.dart import (
    DartApiError,
    DartClient,
    DocumentPayload,
    ProseSection,
    _select_document_member,
    decode_dart_bytes,
    detect_document_format,
    extract_dsd_prose,
)
from app.config import Settings

logger = logging.getLogger(__name__)


# -- decode_dart_bytes: the encoding trap (docs §4 [Verified]) ----------------


def test_decode_plain_utf8_korean() -> None:
    raw = "당사는 반도체를 생산합니다.".encode("utf-8")
    assert decode_dart_bytes(raw) == "당사는 반도체를 생산합니다."


def test_decode_euckr_declared_but_utf8_bytes() -> None:
    # The (B) xforms trap: <meta charset=euc-kr> but the bytes are actually UTF-8.
    # We must ignore the declaration and decode as UTF-8, not mojibake it.
    raw = '<meta charset="euc-kr">돋움체 본문'.encode("utf-8")
    decoded = decode_dart_bytes(raw)
    assert "돋움체" in decoded  # docs §4: \xeb\x8f\x8b... == 돋움체, decoded clean
    assert "charset=\"euc-kr\"" in decoded


def test_decode_genuine_cp949_bytes_fall_back() -> None:
    # A truly legacy-encoded document: cp949/euc-kr Korean bytes fail UTF-8 strict
    # and must fall back to cp949 without corruption.
    text = "한글 인코딩 폴백"
    raw = text.encode("cp949")
    # Sanity: these bytes are NOT valid UTF-8, so the fallback path is taken.
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")
    assert decode_dart_bytes(raw) == text


def test_decode_ascii_is_utf8() -> None:
    assert decode_dart_bytes(b"<DOCUMENT>plain ascii</DOCUMENT>") == (
        "<DOCUMENT>plain ascii</DOCUMENT>"
    )


# -- detect_document_format (docs §4 [Verified]) ------------------------------

# Abbreviated DSD body: root <DOCUMENT> referencing dart4.xsd (docs §4 markers).
# xmlns:xsi is declared as the real body does (the xsi: prefix must be bound).
_DSD_HEAD = (
    '<?xml version="1.0" encoding="euc-kr"?>'
    '<DOCUMENT xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
    'xsi:noNamespaceSchemaLocation="dart4.xsd">'
    '<DOCUMENT-NAME ACODE="11011">사업보고서</DOCUMENT-NAME>'
    "</DOCUMENT>"
)
# Abbreviated xforms: <html> root with an euc-kr meta declaration (docs §4).
_XFORMS_HEAD = (
    "<html><head><meta http-equiv='Content-Type' "
    "content='text/html; charset=euc-kr'></head><body class='xforms'>x</body></html>"
)


def test_detect_dsd() -> None:
    assert detect_document_format(_DSD_HEAD) == "dsd"


def test_detect_xforms() -> None:
    assert detect_document_format(_XFORMS_HEAD) == "xforms"


def test_detect_unknown_on_noise() -> None:
    # No DSD/xforms marker -> "unknown" (we never guess a parser).
    assert detect_document_format("just some random text, not a filing") == "unknown"
    assert detect_document_format("") == "unknown"


def test_detect_dsd_ignores_leading_declaration_and_bom() -> None:
    # A UTF-8 BOM + <?xml?> declaration before the root must not hide the marker.
    assert detect_document_format(chr(0xFEFF) + _DSD_HEAD) == "dsd"


def test_detect_does_not_match_unrelated_document_word() -> None:
    # "<documentation>" must NOT be read as the DSD root <DOCUMENT ...>.
    assert detect_document_format("<documentation>see the docs</documentation>") == (
        "unknown"
    )


# -- extract_dsd_prose: prose only, tables excluded, nesting/order ------------

# Inline DSD modelled on docs §4: SECTION-1 (TITLE + two <P> with a nested <SPAN>
# + an ACLASS="EXTRACTION" numeric table) containing a nested SECTION-2 (its own
# TITLE + <P> + an ACLASS="NORMAL" table), then a truly empty SECTION-1. The
# leading <?xml encoding="euc-kr"?> declaration also proves we parse a str whose
# declaration disagrees with reality (docs §4).
_DSD_DOC = (
    '<?xml version="1.0" encoding="euc-kr"?>'
    '<DOCUMENT xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
    'xsi:noNamespaceSchemaLocation="dart4.xsd">'
    '<DOCUMENT-NAME ACODE="11011">사업보고서</DOCUMENT-NAME>'
    "<SECTION-1>"
    '<TITLE ATOC="Y">1. 회사의 개요</TITLE>'
    "<P>당사는 <SPAN>반도체</SPAN>와 디스플레이를 생산합니다.</P>"
    "<P>주요 제품은 메모리 반도체입니다.</P>"
    '<TABLE ACLASS="EXTRACTION">'
    "<TR><TD>매출액</TD><TE>258935494000000</TE></TR>"
    "</TABLE>"
    "<SECTION-2>"
    "<TITLE>1-1. 사업부문</TITLE>"
    "<P>DX 부문과 DS 부문으로 구성됩니다.</P>"
    '<TABLE ACLASS="NORMAL"><TR><TD>구분</TD><TE>99999</TE></TR></TABLE>'
    "</SECTION-2>"
    "</SECTION-1>"
    "<SECTION-1><PGBRK/></SECTION-1>"  # truly empty section -> omitted
    "</DOCUMENT>"
)

# Numbers that live only in tables; none may leak into extracted prose (§3 rule).
_TABLE_NUMBERS = ("258935494000000", "99999")


def test_extract_dsd_prose_basic_shape() -> None:
    sections = extract_dsd_prose(_DSD_DOC)
    # Two non-empty sections (outer SECTION-1 + nested SECTION-2); the PGBRK-only
    # SECTION-1 is dropped as empty.
    assert len(sections) == 2
    assert all(isinstance(s, ProseSection) for s in sections)

    outer, nested = sections
    # section_title from <TITLE>; order is document-order, gap-free (0, 1).
    assert outer.section_title == "1. 회사의 개요"
    assert outer.order == 0
    assert nested.section_title == "1-1. 사업부문"
    assert nested.order == 1


def test_extract_dsd_prose_merges_span_and_paragraphs() -> None:
    outer = extract_dsd_prose(_DSD_DOC)[0]
    # <SPAN> text is merged into its <P>; the two <P> join with a newline.
    assert outer.content == (
        "당사는 반도체와 디스플레이를 생산합니다.\n주요 제품은 메모리 반도체입니다."
    )


def test_extract_dsd_prose_excludes_all_table_numbers() -> None:
    # ★ Core rule: no number from any table (EXTRACTION or NORMAL) enters prose.
    for section in extract_dsd_prose(_DSD_DOC):
        for number in _TABLE_NUMBERS:
            assert number not in section.content
            assert number not in (section.section_title or "")


def test_extract_dsd_prose_nested_section_not_double_counted() -> None:
    # The nested SECTION-2 prose belongs to SECTION-2 only, not to the outer one.
    outer, nested = extract_dsd_prose(_DSD_DOC)
    assert "DX 부문" in nested.content
    assert "DX 부문" not in outer.content


def test_extract_dsd_prose_title_only_section_kept_with_empty_content() -> None:
    # A section with a TITLE but no <P> is a meaningful TOC header -> kept, empty.
    doc = (
        "<DOCUMENT>"
        "<SECTION-1><TITLE>II. 사업의 내용</TITLE></SECTION-1>"
        "</DOCUMENT>"
    )
    sections = extract_dsd_prose(doc)
    assert len(sections) == 1
    assert sections[0].section_title == "II. 사업의 내용"
    assert sections[0].content == ""


def test_extract_dsd_prose_all_empty_returns_nothing() -> None:
    doc = "<DOCUMENT><SECTION-1><PGBRK/></SECTION-1></DOCUMENT>"
    assert extract_dsd_prose(doc) == []


def test_extract_dsd_prose_repairs_literal_ampersand_and_lt() -> None:
    # DART DSD prose embeds literal '&' (R&D) and '<' (< TV 추이 >) that are not
    # well-formed XML (docs §4 [Verified]). We must repair + parse them, not crash.
    doc = (
        "<DOCUMENT><SECTION-1><TITLE>연구</TITLE>"
        "<P>업계 최고 수준의 R&D 역량을 보유하고 있습니다.</P>"
        "<P>< TV 시장점유율 추이 ></P>"
        "</SECTION-1></DOCUMENT>"
    )
    sections = extract_dsd_prose(doc)
    assert len(sections) == 1
    content = sections[0].content
    # The literal characters survive as text (unescaped back by the parser).
    assert "R&D 역량" in content
    assert "< TV 시장점유율 추이 >" in content


# -- _select_document_member (docs §4 ZIP member selection) -------------------


def test_select_member_prefers_exact_body() -> None:
    names = [
        "20240312000736_00760.xml",  # attachment
        "20240312000736.xml",  # body
        "20240312000736_00761.xml",  # attachment
    ]
    assert _select_document_member(names, "20240312000736") == "20240312000736.xml"


def test_select_member_falls_back_to_non_attachment_stem() -> None:
    # No exact {rcept_no}.xml: prefer an .xml member without a "_" attachment stem.
    names = ["99999999999999_00001.xml", "main.xml"]
    assert _select_document_member(names, "20240312000736") == "main.xml"


def test_select_member_empty_zip_raises() -> None:
    with pytest.raises(DartApiError):
        _select_document_member([], "20240312000736")


# -- fetch_document offline (httpx.MockTransport) -----------------------------


def _make_zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


# A fake key so the offline path builds request params without a real secret.
_FAKE_SETTINGS = Settings(dart_api_key=SecretStr("FAKEKEY123"))


def test_fetch_document_offline_zip_then_full_prose_chain() -> None:
    # ZIP body (docs §4): "{rcept_no}.xml" + one attachment. After fetch, run the
    # whole normalize chain offline: decode -> detect -> extract.
    rcept_no = "20240312000736"
    zip_bytes = _make_zip(
        {
            f"{rcept_no}.xml": _DSD_DOC.encode("utf-8"),
            f"{rcept_no}_00760.xml": b"<attach/>",
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/document.xml")
        assert request.url.params["rcept_no"] == rcept_no
        return httpx.Response(200, content=zip_bytes)

    async def _run() -> DocumentPayload:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        dart = DartClient(settings=_FAKE_SETTINGS, client=client)
        try:
            return await dart.fetch_document(rcept_no)
        finally:
            await client.aclose()

    payload = asyncio.run(_run())
    assert payload.filename == f"{rcept_no}.xml"
    assert len(payload.member_names) == 2  # body + attachment both visible

    text = decode_dart_bytes(payload.content)
    assert detect_document_format(text) == "dsd"
    sections = extract_dsd_prose(text)
    assert len(sections) == 2
    assert "반도체" in sections[0].content


def test_fetch_document_offline_error_xml_raises_without_key_leak() -> None:
    # Not a ZIP (no "PK" magic) -> DART error XML -> DartApiError, no key leak.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"<result><status>013</status><message>no data</message></result>"
        )

    async def _run() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        dart = DartClient(settings=_FAKE_SETTINGS, client=client)
        try:
            await dart.fetch_document("20240312000736")
        finally:
            await client.aclose()

    with pytest.raises(DartApiError) as exc:
        asyncio.run(_run())
    msg = str(exc.value)
    assert "013" in msg
    assert "crtfc_key" not in msg
    assert "FAKEKEY123" not in msg  # the key value is never surfaced


# -- live-log path: constructing DartClient masks httpx URL logs --------------


def test_constructing_dart_client_masks_httpx_live_log() -> None:
    # Regression for the live-log leak: the live test constructs DartClient
    # directly and never calls configure_logging(), yet httpx logs the full
    # request URL (with crtfc_key) at INFO. DartClient.__init__ must install the
    # source-logger mask so that path is covered too. Offline + fake key: we just
    # emit an httpx-shaped record and confirm the key is redacted at the record,
    # independent of any handler (this is what --log-cli-level=INFO would show).
    fake_key = "FAKEKEY123"
    url = f"https://opendart.fss.or.kr/api/document.xml?crtfc_key={fake_key}&rcept_no=1"

    DartClient(settings=_FAKE_SETTINGS)  # __init__ installs the source-logger mask

    httpx_logger = logging.getLogger("httpx")
    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    sink = _Capture()
    httpx_logger.addHandler(sink)
    prev_level, prev_propagate = httpx_logger.level, httpx_logger.propagate
    httpx_logger.setLevel(logging.INFO)
    httpx_logger.propagate = False
    try:
        httpx_logger.info("HTTP Request: %s %s", "GET", url)
    finally:
        httpx_logger.removeHandler(sink)
        httpx_logger.setLevel(prev_level)
        httpx_logger.propagate = prev_propagate

    assert len(captured) == 1
    message = captured[0].getMessage()
    assert fake_key not in message
    assert "crtfc_key=***" in message


# -- live (skipped unless DART_API_KEY is set) -------------------------------


def _has_hangul(text: str) -> bool:
    return any("가" <= ch <= "힣" for ch in text)


@pytest.mark.skipif(
    not os.environ.get("DART_API_KEY"),
    reason="DART_API_KEY not set; skipping live document.xml fetch",
)
def test_fetch_document_live_samsung_dsd() -> None:
    # Live, single call: 삼성 2023 사업보고서 rcept_no recorded in docs §4.
    # Structural asserts only (format=dsd, non-empty prose, hangul present, no
    # table numbers hardcoded). Logs the first section's title + a prose preview
    # so a human can eyeball that decoding is correct (public filing text, not a
    # secret). Run with: pytest -s --log-cli-level=INFO to see the preview.
    rcept_no = "20240312000736"

    async def _run() -> DocumentPayload:
        client = DartClient(settings=Settings())
        try:
            return await client.fetch_document(rcept_no)
        finally:
            await client.aclose()

    payload = asyncio.run(_run())
    assert payload.rcept_no == rcept_no
    assert len(payload.content) > 0

    text = decode_dart_bytes(payload.content)
    assert detect_document_format(text) == "dsd"

    sections = extract_dsd_prose(text)
    assert len(sections) >= 1

    # At least one section has real Korean prose (proves clean decoding).
    prose_sections = [s for s in sections if s.content]
    assert prose_sections, "no section carried any prose content"
    assert any(_has_hangul(s.content) for s in prose_sections)

    # Human-eyeball preview: first prose section's title + first 200 chars.
    preview = prose_sections[0]
    logger.info(
        "live document.xml prose preview -- title=%r content[:200]=%r",
        preview.section_title,
        preview.content[:200],
    )
