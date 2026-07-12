"""Tests for DART corpCode.xml parsing and ticker -> corp_code resolution.

The parser test runs offline against an inline XML fixture. The live-resolution
test is skipped unless DART_API_KEY is set in the environment.
"""

import asyncio
import json
import logging
import os

import pytest

from app.clients.dart import DartClient, parse_corpcode_xml
from app.config import Settings

logger = logging.getLogger(__name__)

# Inline fixture: one listed company (삼성전자) and one non-listed (blank/space
# stock_code, which DART pads with a single space -- must be filtered out).
_FIXTURE_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    "<result>"
    "<list>"
    "<corp_code>00126380</corp_code>"
    "<corp_name>삼성전자</corp_name>"
    "<stock_code>005930</stock_code>"
    "<modify_date>20251201</modify_date>"
    "</list>"
    "<list>"
    "<corp_code>00164779</corp_code>"
    "<corp_name>비상장회사</corp_name>"
    "<stock_code> </stock_code>"  # non-listed: single-space padding
    "<modify_date>20200101</modify_date>"
    "</list>"
    "</result>"
).encode()


def test_parse_corpcode_xml_filters_non_listed() -> None:
    records = parse_corpcode_xml(_FIXTURE_XML)
    # Only the listed company survives.
    assert len(records) == 1
    rec = records[0]
    assert rec == {
        "corp_code": "00126380",
        "corp_name": "삼성전자",
        "stock_code": "005930",
        "modify_date": "20251201",
    }


def test_resolve_corp_code_uses_cache_no_network(tmp_path) -> None:
    # Pre-seed the snapshot cache so resolve_corp_code never hits the network.
    cache = tmp_path / "corpcode_snapshot.json"
    cache.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "corp_code": "00126380",
                        "corp_name": "삼성전자",
                        "stock_code": "005930",
                        "modify_date": "20251201",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    client = DartClient(settings=Settings(), cache_path=cache)
    assert asyncio.run(client.resolve_corp_code("005930")) == "00126380"
    # Unknown ticker -> None.
    assert asyncio.run(client.resolve_corp_code("999999")) is None


@pytest.mark.skipif(
    not os.environ.get("DART_API_KEY"),
    reason="DART_API_KEY not set; skipping live corpCode fetch",
)
def test_resolve_corp_code_live_samsung(tmp_path) -> None:
    # Live: fetch the real corpCode ZIP once into a temp snapshot, then resolve.
    # Fetch + close must share one event loop (the httpx client is bound to it).
    cache = tmp_path / "corpcode_snapshot.json"

    async def _run() -> str | None:
        client = DartClient(settings=Settings(), cache_path=cache)
        try:
            return await client.resolve_corp_code("005930")
        finally:
            await client.aclose()

    result = asyncio.run(_run())
    assert result == "00126380"  # 삼성전자 (docs/dart-api-notes.md §1)
    assert cache.exists()
