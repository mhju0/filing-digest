"""Pure-function tests for the ingest CLI's selection/matching logic.

No network, no DB: `select_latest_annual` and `match_ticker` are the only
decision points the CLI adds on top of the already-tested ingest paths.
"""

import datetime

import pytest

from app.clients.dart import FilingItem
from app.clients.sec import SecCompanyMatch
from app.ingest.__main__ import match_ticker, select_latest_annual


def _item(report_nm: str, rcept_dt: datetime.date | None, rcept_no: str = "R1") -> FilingItem:
    return FilingItem(
        rcept_no=rcept_no,
        corp_code="00126380",
        corp_name="삼성전자",
        report_nm=report_nm,
        flr_nm="삼성전자",
        rcept_dt=rcept_dt,
        rm="",
        stock_code="005930",
        corp_cls="Y",
    )


class TestSelectLatestAnnual:
    def test_picks_annual_and_derives_business_year(self):
        filings = [
            _item("분기보고서 (2024.03)", datetime.date(2024, 5, 16), "R-q1"),
            _item("사업보고서 (2023.12)", datetime.date(2024, 3, 12), "R-annual"),
            _item("반기보고서 (2024.06)", datetime.date(2024, 8, 14), "R-h1"),
        ]
        item, year = select_latest_annual(filings)
        assert item.rcept_no == "R-annual"
        assert year == "2023"

    def test_newest_annual_wins_when_two_are_listed(self):
        filings = [
            _item("사업보고서 (2023.12)", datetime.date(2024, 3, 12), "R-2023"),
            _item("사업보고서 (2024.12)", datetime.date(2025, 3, 11), "R-2024"),
        ]
        item, year = select_latest_annual(filings)
        assert item.rcept_no == "R-2024"
        assert year == "2024"

    def test_correction_prefix_variant_is_still_matched(self):
        # DART lists corrected reports as "사업보고서 (2023.12)" under the same
        # prefix family; a [기재정정] prefix would NOT match and that is
        # deliberate — a correction changes the rcept_no and needs review.
        filings = [_item("[기재정정]사업보고서 (2023.12)", datetime.date(2024, 4, 2))]
        with pytest.raises(ValueError):
            select_latest_annual(filings)

    def test_no_annual_report_raises(self):
        filings = [_item("분기보고서 (2024.03)", datetime.date(2024, 5, 16))]
        with pytest.raises(ValueError):
            select_latest_annual(filings)

    def test_unparseable_rcept_dt_is_skipped(self):
        filings = [
            _item("사업보고서 (2023.12)", None, "R-bad-date"),
            _item("사업보고서 (2022.12)", datetime.date(2023, 3, 10), "R-2022"),
        ]
        item, year = select_latest_annual(filings)
        assert item.rcept_no == "R-2022"
        assert year == "2022"


class TestMatchTicker:
    _matches = [
        SecCompanyMatch(cik="0000789019", ticker="MSFT", title="MICROSOFT CORP"),
        SecCompanyMatch(cik="0001045810", ticker="NVDA", title="NVIDIA CORP"),
    ]

    def test_exact_match_case_insensitive(self):
        assert match_ticker(self._matches, "msft").cik == "0000789019"

    def test_substring_hit_is_not_enough(self):
        # search_company returns substring hits; "MS" must not resolve to MSFT.
        with pytest.raises(ValueError):
            match_ticker(self._matches, "MS")
