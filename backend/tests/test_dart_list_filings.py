"""Tests for DART list.json parsing (filing list -> FilingItem).

All tests here are offline: they drive ``DartClient._parse_list_payload`` with
inline JSON fixtures modeled on docs/dart-api-notes.md §2, so field cleaning and
status branching (000 / 013 / error) are checked without any network call.

A separate live test (skipped unless DART_API_KEY is set) resolves 삼성전자 and
asserts structural/type invariants only -- no hardcoded rcept_no, which drifts
over time.
"""

import asyncio
import datetime
import logging
import os

import pytest

from app.clients.dart import DartApiError, DartClient, FilingItem
from app.config import Settings

logger = logging.getLogger(__name__)

# One 정상(000) response with two rows. report_nm carries trailing space padding
# (as DART sends it) so we can assert .strip() removes it. Fields mirror §2.
_OK_PAYLOAD = {
    "status": "000",
    "message": "정상",
    "page_no": 1,
    "page_count": 10,
    "total_count": 114,
    "total_page": 12,
    "list": [
        {
            "corp_code": "00126380",
            "corp_name": "삼성전자",
            "stock_code": "005930",
            "corp_cls": "Y",
            "report_nm": "지속가능경영보고서등관련사항(자율공시)              ",
            "rcept_no": "20240628800773",
            "flr_nm": "삼성전자",
            "rcept_dt": "20240628",
            "rm": "유",
        },
        {
            "corp_code": "00126380",
            "corp_name": "삼성전자",
            "stock_code": "005930",
            "corp_cls": "Y",
            "report_nm": "사업보고서 (2023.12)",
            "rcept_no": "20240312000736",
            "flr_nm": "삼성전자",
            "rcept_dt": "20240312",
            "rm": "",
        },
    ],
}

_NO_DATA_PAYLOAD = {"status": "013", "message": "조회된 데이타가 없습니다."}
_RATE_LIMIT_PAYLOAD = {"status": "020", "message": "요청 제한을 초과하였습니다."}
_BAD_KEY_PAYLOAD = {"status": "010", "message": "등록되지 않은 키입니다."}


def test_parse_list_payload_ok_cleans_fields() -> None:
    items = DartClient._parse_list_payload(_OK_PAYLOAD)
    assert len(items) == 2
    first = items[0]
    assert isinstance(first, FilingItem)
    # report_nm right-padding stripped (docs §2 [Verified]).
    assert first.report_nm == "지속가능경영보고서등관련사항(자율공시)"
    assert not first.report_nm.endswith(" ")
    # rcept_dt parsed YYYYMMDD -> date.
    assert first.rcept_dt == datetime.date(2024, 6, 28)
    # join keys preserved.
    assert first.rcept_no == "20240628800773"
    assert first.corp_code == "00126380"
    assert first.corp_cls == "Y"
    # viewer_url (filings.url source) derives from rcept_no.
    assert first.viewer_url.endswith("rcpNo=20240628800773")


def test_parse_list_payload_no_data_returns_empty() -> None:
    # status 013 is a valid "nothing in range" answer, not an error.
    assert DartClient._parse_list_payload(_NO_DATA_PAYLOAD) == []


def test_parse_list_payload_rate_limit_raises() -> None:
    with pytest.raises(DartApiError) as exc:
        DartClient._parse_list_payload(_RATE_LIMIT_PAYLOAD)
    assert "020" in str(exc.value)


def test_parse_list_payload_bad_key_raises_without_key_leak() -> None:
    with pytest.raises(DartApiError) as exc:
        DartClient._parse_list_payload(_BAD_KEY_PAYLOAD)
    msg = str(exc.value)
    assert "010" in msg
    # The error surfaces status/message but never the API key value.
    assert "crtfc_key" not in msg


def test_parse_list_payload_bad_date_is_defensive() -> None:
    payload = {
        "status": "000",
        "list": [{"rcept_no": "X", "report_nm": "T", "rcept_dt": "notadate"}],
    }
    items = DartClient._parse_list_payload(payload)
    assert len(items) == 1
    assert items[0].rcept_dt is None  # unparseable date -> None, no raise


def test_parse_list_payload_missing_list_returns_empty() -> None:
    # status 000 but no 'list' array -> defensive empty result.
    assert DartClient._parse_list_payload({"status": "000"}) == []


@pytest.mark.skipif(
    not os.environ.get("DART_API_KEY"),
    reason="DART_API_KEY not set; skipping live list.json fetch",
)
def test_list_filings_live_samsung() -> None:
    # Live: resolve 삼성전자 -> corp_code, then list ~1y of 정기공시 (pblntf_ty=A).
    # Structural/type asserts only -- specific rcept_no values change over time.
    async def _run() -> list[FilingItem]:
        client = DartClient(settings=Settings())
        try:
            corp_code = await client.resolve_corp_code("005930")
            assert corp_code == "00126380"
            # bgn_de = one year before end_de; avoid Date.now(): derive from today.
            today = datetime.date.today()
            bgn = today.replace(year=today.year - 1)
            return await client.list_filings(
                corp_code,
                bgn_de=bgn.strftime("%Y%m%d"),
                end_de=today.strftime("%Y%m%d"),
                pblntf_ty="A",
            )
        finally:
            await client.aclose()

    items = asyncio.run(_run())
    assert len(items) >= 1
    for it in items:
        # no leading/trailing whitespace on report_nm.
        assert it.report_nm == it.report_nm.strip()
        assert it.report_nm != ""
        # rcept_dt parsed to a real date.
        assert isinstance(it.rcept_dt, datetime.date)
