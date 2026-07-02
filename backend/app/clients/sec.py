"""SEC EDGAR client stub.

NOTE: SEC requires a User-Agent header that includes contact information
(e.g. "app-name contact@example.com") -- use settings.sec_user_agent on
every request (see https://www.sec.gov/os/accessing-edgar-data).

SECURITY: never log API keys or secrets of any kind (EDGAR itself needs no
key, but this module must not log settings values verbatim either).

TODO(Phase 2): implement real HTTP calls with httpx.AsyncClient.
Actual EDGAR response formats are [Unknown] until verified against the
live API in Phase 2.
"""

import logging
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


class SecClient:
    """Stub client for SEC EDGAR (https://data.sec.gov).

    Settings are injected; an httpx.AsyncClient may be injected for testing.
    The client itself is a placeholder -- no request is made in Phase 1.
    """

    def __init__(
        self, settings: Settings, client: httpx.AsyncClient | None = None
    ) -> None:
        self._settings = settings
        self._base_url = settings.sec_base_url
        # Required by SEC: User-Agent with contact info on every request.
        self._user_agent = settings.sec_user_agent
        # Placeholder: created lazily in Phase 2 (do not open connections here).
        self._client: httpx.AsyncClient | None = client

    async def search_company(self, query: str) -> Any:
        """Search companies / CIKs.

        TODO(Phase 2): use the company_tickers.json mapping or full-text search.
        Actual response format is [Unknown] -- verify against the live API.
        """
        raise NotImplementedError("SecClient.search_company: TODO(Phase 2)")

    async def list_filings(
        self, cik: str, filing_types: list[str] | None = None
    ) -> Any:
        """List filings for a CIK (submissions API).

        TODO(Phase 2): GET /submissions/CIK##########.json.
        Actual response format is [Unknown] -- verify against the live API.
        """
        raise NotImplementedError("SecClient.list_filings: TODO(Phase 2)")

    async def fetch_company_facts(self, cik: str) -> Any:
        """Fetch structured XBRL company facts.

        Numbers must come only from this structured API, never from LLM text.

        TODO(Phase 2): GET /api/xbrl/companyfacts/CIK##########.json.
        Actual response format is [Unknown] -- verify against the live API.
        """
        raise NotImplementedError("SecClient.fetch_company_facts: TODO(Phase 2)")

    async def aclose(self) -> None:
        """Close the underlying httpx client.

        TODO(Phase 2): close self._client when real requests are implemented.
        """
        raise NotImplementedError("SecClient.aclose: TODO(Phase 2)")
