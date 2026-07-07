"""Tests for SEC EDGAR archive document fetch (``SecClient.fetch_document``).

Offline: ``_archive_url`` (pure) and ``fetch_document`` (via
``httpx.MockTransport``) are checked without any network call. Fetch only --
no HTML parsing here (that is a later step, mirroring DART's
document.xml -> prose split). The archive URL shape is [Inferred] -- not
verified against a live fetch in this offline step.
"""

import asyncio
import logging

import httpx

from app.clients.sec import SecClient, _archive_url
from app.config import Settings

logger = logging.getLogger(__name__)

_FAKE_SETTINGS = Settings(sec_user_agent="filing-digest-test test@example.com")


def test_archive_url_strips_dashes_and_leading_zeros() -> None:
    url = _archive_url("0000320193", "0000320193-23-000106", "aapl-20230930.htm")
    assert url == (
        "https://www.sec.gov/Archives/edgar/data/320193/"
        "000032019323000106/aapl-20230930.htm"
    )


def test_fetch_document_offline_builds_url_and_sends_user_agent() -> None:
    cik = 320193
    accession_number = "0000320193-23-000106"
    primary_document = "aapl-20230930.htm"
    body = b"<html>filing body</html>"

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == (
            "https://www.sec.gov/Archives/edgar/data/320193/"
            "000032019323000106/aapl-20230930.htm"
        )
        assert request.headers["User-Agent"] == _FAKE_SETTINGS.sec_user_agent
        return httpx.Response(200, content=body)

    async def _run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        sec = SecClient(settings=_FAKE_SETTINGS, client=client)
        try:
            return await sec.fetch_document(cik, accession_number, primary_document)
        finally:
            await client.aclose()

    payload = asyncio.run(_run())
    assert payload.raw_bytes == body
    assert payload.cik == "0000320193"
    assert payload.accession_number == accession_number
    assert payload.primary_document == primary_document
    assert payload.url.endswith("/aapl-20230930.htm")
