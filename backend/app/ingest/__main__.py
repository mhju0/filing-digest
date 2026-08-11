"""Ingest CLI: one command ingests a company's latest annual filing.

Usage (run from ``backend/`` with the repo venv):

    python -m app.ingest --source dart --ticker 005930
    python -m app.ingest --source sec --ticker MSFT

DART and SEC resolve their source-specific identities and build a Normalized
Filing through their adapters. The shared ingestion module then persists and
indexes either source through the same lifecycle.

Selection/matching logic lives in pure functions (``select_latest_annual``,
``match_ticker``) so it is unit-testable without network or DB.
"""

import argparse
import asyncio
import datetime
import logging
import re

from app.clients.dart import DartClient, FilingItem
from app.clients.sec import SecClient, SecCompanyMatch
from app.config import get_settings
from app.db.session import get_async_engine, get_async_session
from app.ingest import ingest_filing
from app.ingest.dart import DartFilingAdapter
from app.ingest.sec_ingest import SecFilingAdapter
from app.logging_config import configure_logging

logger = logging.getLogger(__name__)

_ANNUAL_REPRT_CODE = "11011"
# "사업보고서 (2023.12)" -> business year 2023. [Verified] report_nm shape in
# DART annual-report naming convention.
_REPORT_YEAR_RE = re.compile(r"\((\d{4})\.")
# One page of 정기공시 over ~15 months always contains the latest annual report.
_LOOKBACK_DAYS = 450


def select_latest_annual(filings: list[FilingItem]) -> tuple[FilingItem, str]:
    """Pick the newest 사업보고서 from a list.json page and its business year.

    Pure: filters ``report_nm`` starting with "사업보고서", derives
    ``bsns_year`` from the "(YYYY.MM)" suffix, orders by ``rcept_dt``.
    Raises ``ValueError`` when the page has no parseable annual report.
    """
    candidates: list[tuple[datetime.date, FilingItem, str]] = []
    for item in filings:
        if not item.report_nm.startswith("사업보고서"):
            continue
        match = _REPORT_YEAR_RE.search(item.report_nm)
        if match is None or item.rcept_dt is None:
            continue
        candidates.append((item.rcept_dt, item, match.group(1)))
    if not candidates:
        raise ValueError("no annual report (사업보고서) found in the listing window")
    rcept_dt, item, year = max(candidates, key=lambda c: c[0])
    return item, year


def match_ticker(matches: list[SecCompanyMatch], ticker: str) -> SecCompanyMatch:
    """Exact (case-insensitive) ticker match from SEC company search results.

    Pure. Raises ``ValueError`` when the ticker is not present — substring
    hits from ``search_company`` must not silently ingest the wrong company.
    """
    wanted = ticker.upper()
    for m in matches:
        if m.ticker.upper() == wanted:
            return m
    raise ValueError(f"ticker {ticker!r} not found in SEC company_tickers")


async def _run_dart(ticker: str) -> None:
    client = DartClient(get_settings())
    try:
        corp_code = await client.resolve_corp_code(ticker)
        if corp_code is None:
            raise SystemExit(f"ticker {ticker!r} not found in DART corpCode map")

        today = datetime.date.today()
        bgn = today - datetime.timedelta(days=_LOOKBACK_DAYS)
        filings = await client.list_filings(
            corp_code,
            bgn_de=bgn.strftime("%Y%m%d"),
            end_de=today.strftime("%Y%m%d"),
            pblntf_ty="A",  # 정기공시
        )
        item, bsns_year = select_latest_annual(filings)
        logger.info(
            "selected %s (rcept_no=%s, bsns_year=%s)",
            item.report_nm,
            item.rcept_no,
            bsns_year,
        )

        result = await ingest_filing(
            DartFilingAdapter(
                client=client,
                corp_code=corp_code,
                filing_item=item,
                bsns_year=bsns_year,
                reprt_code=_ANNUAL_REPRT_CODE,
            ),
            get_async_session,
        )
        logger.info(
            "done: company=%s filing=%s financials=%d chunks=%d embeddings=%d",
            result.company_id,
            result.filing_id,
            result.financials_written,
            result.chunks_written,
            result.embeddings_backfilled,
        )
    finally:
        await client.aclose()
        await get_async_engine().dispose()


async def _run_sec(ticker: str) -> None:
    client = SecClient(get_settings())
    try:
        matches = await client.search_company(ticker)
        match = match_ticker(matches, ticker)
        logger.info("selected %s (CIK %s)", match.title, match.cik)

        result = await ingest_filing(
            SecFilingAdapter(client=client, cik=match.cik), get_async_session
        )
        logger.info(
            "done: company=%s filing=%s financials=%d chunks=%d embeddings=%d",
            result.company_id,
            result.filing_id,
            result.financials_written,
            result.chunks_written,
            result.embeddings_backfilled,
        )
    finally:
        await client.aclose()
        await get_async_engine().dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.ingest",
        description="Ingest a company's latest annual filing (DART 사업보고서 / SEC 10-K).",
    )
    parser.add_argument("--source", choices=["dart", "sec"], required=True)
    parser.add_argument(
        "--ticker",
        required=True,
        help="stock ticker (DART: 6-digit code e.g. 005930; SEC: symbol e.g. MSFT)",
    )
    args = parser.parse_args()

    # configure_logging (not bare basicConfig): installs the crtfc_key masking
    # filters so httpx URL logs never leak the DART API key from the CLI path.
    configure_logging()

    if args.source == "dart":
        asyncio.run(_run_dart(args.ticker))
    else:
        asyncio.run(_run_sec(args.ticker))


if __name__ == "__main__":
    main()
