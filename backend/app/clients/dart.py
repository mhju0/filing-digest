"""DART (OpenDART) client stub.

SECURITY: never log the API key. Settings keep it as SecretStr; do not call
.get_secret_value() outside of building the outgoing request params, and
never include it in log messages or exceptions.

TODO(Phase 2): implement real HTTP calls with httpx.AsyncClient.
Actual OpenDART response formats are [Unknown] until verified against the
live API in Phase 2.
"""

import logging
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


class DartClient:
    """Stub client for the OpenDART API (https://opendart.fss.or.kr).

    Settings are injected; an httpx.AsyncClient may be injected for testing.
    The client itself is a placeholder -- no request is made in Phase 1.
    """

    def __init__(
        self, settings: Settings, client: httpx.AsyncClient | None = None
    ) -> None:
        self._settings = settings
        self._base_url = settings.dart_base_url
        # Placeholder: created lazily in Phase 2 (do not open connections here).
        self._client: httpx.AsyncClient | None = client

    async def search_company(self, name: str) -> Any:
        """Search DART corp codes by company name.

        TODO(Phase 2): call the corpCode.xml endpoint (zip/XML) and parse.
        Actual response format is [Unknown] -- verify against the live API.
        """
        raise NotImplementedError("DartClient.search_company: TODO(Phase 2)")

    async def list_filings(
        self, corp_code: str, filing_types: list[str] | None = None
    ) -> Any:
        """List filings (공시) for a corp_code.

        TODO(Phase 2): call the list.json endpoint.
        Actual response format is [Unknown] -- verify against the live API.
        """
        raise NotImplementedError("DartClient.list_filings: TODO(Phase 2)")

    async def fetch_financials(self, corp_code: str, year: int, quarter: int) -> Any:
        """Fetch structured financial statements (재무제표).

        Numbers must come only from this structured API, never from LLM text.

        TODO(Phase 2): call fnlttSinglAcntAll.json (or similar).
        Actual response format is [Unknown] -- verify against the live API.
        """
        raise NotImplementedError("DartClient.fetch_financials: TODO(Phase 2)")

    async def aclose(self) -> None:
        """Close the underlying httpx client.

        TODO(Phase 2): close self._client when real requests are implemented.
        """
        raise NotImplementedError("DartClient.aclose: TODO(Phase 2)")
