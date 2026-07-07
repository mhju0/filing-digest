"""Tests for SEC ``company_tickers.json`` parsing and ``search_company`` matching.

Offline: ``parse_company_tickers_payload`` / ``search_company_matches`` are
driven with an inline fixture modeled on the public mapping file's shape (a
dict of index -> {cik_str, ticker, title}), and ``SecClient.search_company`` is
exercised via an ``httpx.MockTransport`` to check the URL/User-Agent and the
in-process cache without any network call. The response shape is [Inferred] --
not verified against a live fetch in this offline step.
"""

import asyncio
import logging

import httpx
import pytest

from app.clients.sec import (
    SecApiError,
    SecClient,
    SecCompanyMatch,
    parse_company_tickers_payload,
    search_company_matches,
)
from app.config import Settings

logger = logging.getLogger(__name__)

_COMPANY_TICKERS_PAYLOAD = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
    "2": {"cik_str": 1018724, "ticker": "AMZN", "title": "AMAZON COM INC"},
    "3": {"cik_str": 1652044, "ticker": "GOOGL", "title": "Alphabet Inc."},
}

_FAKE_SETTINGS = Settings(sec_user_agent="filing-digest-test test@example.com")


# -- parse_company_tickers_payload --------------------------------------------


def test_parse_company_tickers_payload_basic() -> None:
    records = parse_company_tickers_payload(_COMPANY_TICKERS_PAYLOAD)
    assert len(records) == 4
    aapl = next(r for r in records if r.ticker == "AAPL")
    assert aapl.cik == "0000320193"  # zero-padded to 10 digits
    assert aapl.title == "Apple Inc."


def test_parse_company_tickers_payload_skips_malformed_cik() -> None:
    payload = {"0": {"cik_str": "not-a-number", "ticker": "X", "title": "Bad"}}
    assert parse_company_tickers_payload(payload) == []


def test_parse_company_tickers_payload_non_dict_raises() -> None:
    with pytest.raises(SecApiError):
        parse_company_tickers_payload(["not", "a", "dict"])


# -- search_company_matches -----------------------------------------------


def test_search_company_matches_exact_ticker_case_insensitive() -> None:
    records = parse_company_tickers_payload(_COMPANY_TICKERS_PAYLOAD)
    matches = search_company_matches(records, "aapl")
    assert len(matches) == 1
    assert matches[0].cik == "0000320193"


def test_search_company_matches_falls_back_to_name_substring() -> None:
    records = parse_company_tickers_payload(_COMPANY_TICKERS_PAYLOAD)
    matches = search_company_matches(records, "microsoft")
    assert len(matches) == 1
    assert matches[0].ticker == "MSFT"


def test_search_company_matches_name_substring_can_return_multiple() -> None:
    records = parse_company_tickers_payload(_COMPANY_TICKERS_PAYLOAD) + [
        SecCompanyMatch(cik="0000000001", ticker="XYZ", title="Apple Orchard Co")
    ]
    matches = search_company_matches(records, "apple")
    assert {m.ticker for m in matches} == {"AAPL", "XYZ"}


def test_search_company_matches_empty_query_returns_empty() -> None:
    records = parse_company_tickers_payload(_COMPANY_TICKERS_PAYLOAD)
    assert search_company_matches(records, "") == []
    assert search_company_matches(records, "   ") == []


def test_search_company_matches_no_hit_returns_empty() -> None:
    records = parse_company_tickers_payload(_COMPANY_TICKERS_PAYLOAD)
    assert search_company_matches(records, "nonexistent") == []


# -- SecClient.search_company offline (httpx.MockTransport + cache) ----------


def test_search_company_offline_sends_user_agent_and_caches() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert str(request.url) == "https://www.sec.gov/files/company_tickers.json"
        assert request.headers["User-Agent"] == _FAKE_SETTINGS.sec_user_agent
        return httpx.Response(200, json=_COMPANY_TICKERS_PAYLOAD)

    async def _run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        sec = SecClient(settings=_FAKE_SETTINGS, client=client)
        try:
            first = await sec.search_company("AAPL")
            second = await sec.search_company("MSFT")
            return first, second
        finally:
            await client.aclose()

    first, second = asyncio.run(_run())
    assert len(calls) == 1  # second search_company call reused the in-process cache
    assert first[0].cik == "0000320193"
    assert second[0].ticker == "MSFT"
