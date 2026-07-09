"""Tests for DART company.json (기업개황) English-name enrichment.

``DartClient._parse_company_eng_name`` is a pure static parser driven with inline
fixtures: it extracts ``corp_name_eng`` for the bilingual ``companies.name_en``
column. name_en is enrichment, NOT a source of numbers -- so a blank/absent
English name yields ``None`` (never a fabricated name), and the same status
branching as list.json/fnlttSinglAcntAll applies.

A separate live test (skipped unless DART_API_KEY is set) fetches 삼성전자 and
asserts only that some non-empty English name comes back -- never a hardcoded
string, which is not the project's to guarantee.
"""

import asyncio
import logging
import os

import pytest

from app.clients.dart import DartApiError, DartClient
from app.config import Settings

logger = logging.getLogger(__name__)


# -- _parse_company_eng_name (docs: company.json 기업개황) --------------------


def test_parse_company_eng_name_ok_returns_english_name() -> None:
    payload = {
        "status": "000",
        "message": "정상",
        "corp_name": "삼성전자",
        "corp_name_eng": "SAMSUNG ELECTRONICS CO,.LTD",
        "stock_code": "005930",
    }
    assert DartClient._parse_company_eng_name(payload) == "SAMSUNG ELECTRONICS CO,.LTD"


def test_parse_company_eng_name_strips_whitespace() -> None:
    payload = {"status": "000", "corp_name_eng": "  Apple Inc.  "}
    assert DartClient._parse_company_eng_name(payload) == "Apple Inc."


def test_parse_company_eng_name_blank_returns_none() -> None:
    # status 000 but the English field is empty/whitespace -> None, never "".
    assert DartClient._parse_company_eng_name({"status": "000", "corp_name_eng": ""}) is None
    assert DartClient._parse_company_eng_name({"status": "000", "corp_name_eng": "   "}) is None


def test_parse_company_eng_name_missing_field_returns_none() -> None:
    # status 000 with no corp_name_eng key -> None (do NOT invent a name).
    assert DartClient._parse_company_eng_name({"status": "000"}) is None


def test_parse_company_eng_name_no_data_returns_none() -> None:
    # status 013 (무자료) is a valid "nothing here" answer, not an error.
    assert DartClient._parse_company_eng_name({"status": "013", "message": "무자료"}) is None


def test_parse_company_eng_name_rate_limit_raises() -> None:
    with pytest.raises(DartApiError) as exc:
        DartClient._parse_company_eng_name({"status": "020", "message": "요청 제한 초과"})
    assert "020" in str(exc.value)


def test_parse_company_eng_name_bad_key_raises_without_key_leak() -> None:
    with pytest.raises(DartApiError) as exc:
        DartClient._parse_company_eng_name({"status": "010", "message": "등록되지 않은 키"})
    msg = str(exc.value)
    assert "010" in msg
    assert "crtfc_key" not in msg  # never surface the key


def test_parse_company_eng_name_non_dict_raises() -> None:
    with pytest.raises(DartApiError):
        DartClient._parse_company_eng_name(["not", "a", "dict"])


# -- live (skipped unless DART_API_KEY is set) -------------------------------


@pytest.mark.skipif(
    not os.environ.get("DART_API_KEY"),
    reason="DART_API_KEY not set; skipping live company.json fetch",
)
def test_fetch_company_eng_name_live_samsung() -> None:
    # Live: 삼성전자 (corp_code 00126380). Asserts only that a non-empty English
    # name is returned -- the exact string is DART's to define, not hardcoded.
    async def _run() -> str | None:
        client = DartClient(settings=Settings())
        try:
            return await client.fetch_company_eng_name("00126380")
        finally:
            await client.aclose()

    eng = asyncio.run(_run())
    assert eng
    assert eng.strip() == eng
    logger.info("live company.json corp_name_eng for 삼성전자: %r", eng)
