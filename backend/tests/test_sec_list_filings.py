"""Tests for SEC ``submissions/CIK##########.json`` parsing and ``format_cik``.

All tests here are offline: ``parse_submissions_payload`` is driven with an
inline JSON fixture modeled on the public submissions API shape (column-oriented
``filings.recent`` arrays), and ``SecClient.list_filings`` is exercised via an
``httpx.MockTransport`` so the URL/User-Agent are checked without any network
call. The response shape (field names, array layout) is [Inferred] -- not
verified against a live fetch in this offline step.
"""

import asyncio
import datetime
import logging

import httpx
import pytest

from app.clients.sec import (
    SecApiError,
    SecClient,
    SecClientError,
    format_cik,
    parse_submissions_payload,
)
from app.config import Settings

logger = logging.getLogger(__name__)

# Trimmed, real-shaped submissions payload (Apple Inc., CIK 0000320193): two
# 10-K rows and one 10-Q row so form filtering has something to exclude.
_SUBMISSIONS_PAYLOAD = {
    "cik": "320193",
    "entityType": "operating",
    "sic": "3571",
    "name": "Apple Inc.",
    "tickers": ["AAPL"],
    "exchanges": ["Nasdaq"],
    "filings": {
        "recent": {
            "accessionNumber": [
                "0000320193-23-000106",
                "0000320193-23-000077",
                "0000320193-22-000108",
            ],
            "filingDate": ["2023-11-03", "2023-08-04", "2022-10-28"],
            "reportDate": ["2023-09-30", "2023-07-01", "2022-09-24"],
            "form": ["10-K", "10-Q", "10-K"],
            "primaryDocument": [
                "aapl-20230930.htm",
                "aapl-20230701.htm",
                "aapl-20220924.htm",
            ],
        },
        "files": [],
    },
}

_FAKE_SETTINGS = Settings(sec_user_agent="filing-digest-test test@example.com")


# -- format_cik ---------------------------------------------------------------


def test_format_cik_zero_pads_int_and_str() -> None:
    assert format_cik(320193) == "0000320193"
    assert format_cik("320193") == "0000320193"
    assert format_cik("0000320193") == "0000320193"


def test_format_cik_rejects_non_numeric() -> None:
    with pytest.raises(SecClientError):
        format_cik("AAPL")


def test_format_cik_rejects_too_long() -> None:
    with pytest.raises(SecClientError):
        format_cik("12345678901")


# -- parse_submissions_payload --------------------------------------------


def test_parse_submissions_payload_filters_10k() -> None:
    items = parse_submissions_payload(_SUBMISSIONS_PAYLOAD, filing_types=["10-K"])
    assert len(items) == 2
    assert all(it.form == "10-K" for it in items)
    first = items[0]
    assert first.accession_number == "0000320193-23-000106"
    assert first.filing_date == datetime.date(2023, 11, 3)
    assert first.report_date == datetime.date(2023, 9, 30)
    assert first.primary_document == "aapl-20230930.htm"


def test_parse_submissions_payload_case_insensitive_filter() -> None:
    items = parse_submissions_payload(_SUBMISSIONS_PAYLOAD, filing_types=["10-k"])
    assert len(items) == 2


def test_parse_submissions_payload_no_filter_returns_all() -> None:
    items = parse_submissions_payload(_SUBMISSIONS_PAYLOAD)
    assert len(items) == 3


def test_parse_submissions_payload_missing_recent_raises() -> None:
    with pytest.raises(SecApiError):
        parse_submissions_payload({"filings": {}})


def test_parse_submissions_payload_non_dict_raises() -> None:
    with pytest.raises(SecApiError):
        parse_submissions_payload(["not", "a", "dict"])


def test_parse_submissions_payload_missing_arrays_returns_empty() -> None:
    # status-000-equivalent "nothing here" -- defensive empty result, not a raise.
    assert parse_submissions_payload({"filings": {"recent": {}}}) == []


# -- SecClient.list_filings offline (httpx.MockTransport) ---------------------


def test_list_filings_offline_sends_user_agent_and_builds_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/submissions/CIK0000320193.json"
        assert request.headers["User-Agent"] == _FAKE_SETTINGS.sec_user_agent
        return httpx.Response(200, json=_SUBMISSIONS_PAYLOAD)

    async def _run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        sec = SecClient(settings=_FAKE_SETTINGS, client=client)
        try:
            return await sec.list_filings(320193, filing_types=["10-K"])
        finally:
            await client.aclose()

    items = asyncio.run(_run())
    assert len(items) == 2
    assert all(it.form == "10-K" for it in items)
