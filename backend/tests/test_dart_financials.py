"""Tests for DART fnlttSinglAcntAll.json parsing (financials -> FinancialItem).

Almost everything here is offline: pure functions (``parse_dart_amount``,
``account_id_to_metric``, ``_dedup_profit_loss``) and the static
``DartClient._parse_financials_payload`` are driven with inline fixtures modeled
on measured DART responses. This is where the project's core rule -- "numbers
come only from this structured API, and we never fabricate one" -- is enforced,
so amount edge cases are checked exhaustively.

A separate live test (skipped unless DART_API_KEY is set) fetches 삼성전자 and
asserts structural/type/mapping invariants only -- never a hardcoded amount,
which changes every year.
"""

import asyncio
import logging
import os
from decimal import Decimal

import pytest

from app.clients.dart import (
    DartApiError,
    DartClient,
    FinancialItem,
    _dedup_profit_loss,
    account_id_to_metric,
    parse_dart_amount,
    parse_dart_decimal,
)
from app.config import Settings

logger = logging.getLogger(__name__)


# -- parse_dart_amount edge cases (docs §3 [Verified] format) ----------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", None),  # empty cell -> no value (docs §3: absent == "")
        ("  ", None),  # whitespace-only -> no value
        (None, None),  # missing key coerced to None -> no value
        ("1000", 1000),
        ("-500", -500),  # negatives carry a leading '-'
        ("1,234,567", 1234567),  # commas stripped defensively (0 seen in §3)
        ("abc", None),  # non-numeric -> None (do NOT invent a number)
        ("  42  ", 42),  # surrounding whitespace stripped
        ("455905980000000", 455905980000000),  # 조 단위: integer preserved exactly
        ("-4480835000000", -4480835000000),  # large negative (법인세비용 sample)
    ],
)
def test_parse_dart_amount(raw, expected) -> None:
    assert parse_dart_amount(raw) == expected


def test_parse_dart_amount_big_value_is_exact_int() -> None:
    # No float rounding: a trillion-won figure must round-trip as an exact int.
    raw = "258935494000000"  # 삼성 2023 영업수익
    parsed = parse_dart_amount(raw)
    assert isinstance(parsed, int)
    assert parsed == 258_935_494_000_000


def test_parse_dart_amount_stray_decimal_is_none() -> None:
    # A decimal point is ambiguous for an integer-KRW field -> None, not a guess.
    assert parse_dart_amount("2131.5") is None


# -- parse_dart_decimal edge cases (EPS: fractional per-share, docs §3) -------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", None),  # empty cell -> no value
        ("  ", None),  # whitespace-only -> no value
        (None, None),  # missing key coerced to None -> no value
        ("2131", Decimal("2131")),  # 삼성 2023: EPS happens to be integer-valued
        ("123.45", Decimal("123.45")),  # fractional per-share preserved
        ("-45.67", Decimal("-45.67")),  # negative EPS keeps its sign
        ("1,234.5", Decimal("1234.5")),  # commas stripped defensively
        ("abc", None),  # non-numeric -> None (do NOT invent a number)
    ],
)
def test_parse_dart_decimal(raw, expected) -> None:
    assert parse_dart_decimal(raw) == expected


def test_parse_dart_decimal_returns_decimal_type_not_float() -> None:
    # Must be Decimal (exact), never float -- integer and fractional inputs alike.
    assert isinstance(parse_dart_decimal("2131"), Decimal)
    assert isinstance(parse_dart_decimal("123.45"), Decimal)


def test_parse_dart_decimal_no_binary_float_rounding() -> None:
    # 0.1 + 0.2 != 0.3 in binary float; Decimal must be exact so equality holds.
    assert parse_dart_decimal("0.1") + parse_dart_decimal("0.2") == Decimal("0.3")
    # And a fractional value round-trips with no lost precision.
    assert parse_dart_decimal("123.45") == Decimal("123.45")


# -- account_id -> metric mapping (docs §3 [Verified]) -----------------------


def test_account_id_to_metric_maps_four_standard_keys() -> None:
    assert account_id_to_metric("ifrs-full_Revenue") == "revenue"
    assert account_id_to_metric("dart_OperatingIncomeLoss") == "operating_income"
    assert account_id_to_metric("ifrs-full_ProfitLoss") == "net_income"
    assert account_id_to_metric("ifrs-full_BasicEarningsLossPerShare") == "eps"


def test_account_id_to_metric_maps_attributable_and_diluted() -> None:
    # 4 -> 6 metric expansion: 지배주주귀속 net income + 희석 EPS (docs §3).
    assert (
        account_id_to_metric("ifrs-full_ProfitLossAttributableToOwnersOfParent")
        == "net_income_attributable"
    )
    assert (
        account_id_to_metric("ifrs-full_DilutedEarningsLossPerShare") == "eps_diluted"
    )


def test_account_id_to_metric_unmapped_is_none() -> None:
    # Unmapped/blank/None account ids -> None (caller keeps them with metric=None).
    assert account_id_to_metric("ifrs-full_Assets") is None
    assert account_id_to_metric("") is None
    assert account_id_to_metric(None) is None


# -- ProfitLoss dedup (docs §3 [Verified] pitfall) ---------------------------


def _fin(account_id: str, sj_div: str, amount: int | None = 1) -> FinancialItem:
    """Minimal FinancialItem for dedup tests (only account_id/sj_div matter)."""
    return FinancialItem(
        rcept_no="20240312000736",
        reprt_code="11011",
        bsns_year="2023",
        sj_div=sj_div,
        sj_nm="",
        account_id=account_id,
        account_nm="당기순이익(손실)",
        thstrm_amount=amount,
        frmtrm_amount=None,
        ord=1,
        currency="KRW",
        metric=account_id_to_metric(account_id),
    )


def test_dedup_profit_loss_keeps_only_is_row() -> None:
    # ifrs-full_ProfitLoss appears identically under IS/CIS/CF -> keep IS only.
    items = [
        _fin("ifrs-full_ProfitLoss", "IS", 15_487_100_000_000),
        _fin("ifrs-full_ProfitLoss", "CIS", 15_487_100_000_000),
        _fin("ifrs-full_ProfitLoss", "CF", 15_487_100_000_000),
    ]
    deduped = _dedup_profit_loss(items)
    assert len(deduped) == 1
    assert deduped[0].sj_div == "IS"
    assert deduped[0].metric == "net_income"


def test_dedup_profit_loss_keeps_total_and_attributable_separately() -> None:
    # Two distinct profit-loss accounts: total ProfitLoss (IS/CIS/CF) and the
    # owners-of-parent subset (IS). Dedup groups by account_id -> each keeps its
    # single IS row; both survive (net_income AND net_income_attributable).
    items = [
        _fin("ifrs-full_ProfitLoss", "IS", 15_487_100_000_000),
        _fin("ifrs-full_ProfitLoss", "CIS", 15_487_100_000_000),
        _fin("ifrs-full_ProfitLoss", "CF", 15_487_100_000_000),
        _fin(
            "ifrs-full_ProfitLossAttributableToOwnersOfParent",
            "IS",
            14_473_401_000_000,
        ),
    ]
    deduped = _dedup_profit_loss(items)
    by_metric = {it.metric: it for it in deduped}
    assert set(by_metric) == {"net_income", "net_income_attributable"}
    assert by_metric["net_income"].sj_div == "IS"
    assert by_metric["net_income_attributable"].sj_div == "IS"
    # Total > attributable (the gap is 비지배지분/non-controlling interest).
    assert (
        by_metric["net_income"].thstrm_amount
        > by_metric["net_income_attributable"].thstrm_amount
    )


def test_dedup_profit_loss_leaves_other_accounts_untouched() -> None:
    # Non-ProfitLoss rows (even multi-statement) must pass through unchanged.
    items = [
        _fin("ifrs-full_Revenue", "IS"),
        _fin("ifrs-full_ProfitLoss", "IS"),
        _fin("ifrs-full_ProfitLoss", "CF"),  # dropped
        _fin("ifrs-full_Assets", "BS"),
        _fin("ifrs-full_CashAndCashEquivalents", "CF"),  # legit CF row, kept
    ]
    deduped = _dedup_profit_loss(items)
    account_ids = [it.account_id for it in deduped]
    assert account_ids == [
        "ifrs-full_Revenue",
        "ifrs-full_ProfitLoss",  # the IS one
        "ifrs-full_Assets",
        "ifrs-full_CashAndCashEquivalents",
    ]


# -- _parse_financials_payload: status branching + full parse ----------------

# Abbreviated 삼성전자 2023 CFS response (docs §3). Includes: the four mapped
# metrics, ProfitLoss triplicated across IS/CIS/CF (must dedup to IS), an
# unmapped account (metric=None, account_nm retained), an empty frmtrm cell
# ("" -> None), and a negative amount.
_FIN_OK_PAYLOAD = {
    "status": "000",
    "message": "정상",
    "list": [
        {
            "rcept_no": "20240312000736",
            "reprt_code": "11011",
            "bsns_year": "2023",
            "corp_code": "00126380",
            "sj_div": "IS",
            "sj_nm": "손익계산서",
            "account_id": "ifrs-full_Revenue",
            "account_nm": "영업수익",  # 삼성 labels revenue this way, not 매출액
            "thstrm_amount": "258935494000000",
            "frmtrm_amount": "302231360000000",
            "ord": "1",
            "currency": "KRW",
        },
        {
            "rcept_no": "20240312000736",
            "reprt_code": "11011",
            "bsns_year": "2023",
            "sj_div": "IS",
            "sj_nm": "손익계산서",
            "account_id": "dart_OperatingIncomeLoss",
            "account_nm": "영업이익",
            "thstrm_amount": "6566976000000",
            "frmtrm_amount": "43376630000000",
            "ord": "24",
            "currency": "KRW",
        },
        {  # ProfitLoss #1 (IS) -- the one to keep
            "sj_div": "IS",
            "sj_nm": "손익계산서",
            "account_id": "ifrs-full_ProfitLoss",
            "account_nm": "당기순이익(손실)",
            "thstrm_amount": "15487100000000",
            "frmtrm_amount": "55654077000000",
            "ord": "30",
            "currency": "KRW",
        },
        {  # ProfitLoss #2 (CIS) -- duplicate, dropped
            "sj_div": "CIS",
            "account_id": "ifrs-full_ProfitLoss",
            "account_nm": "당기순이익(손실)",
            "thstrm_amount": "15487100000000",
            "currency": "KRW",
        },
        {  # ProfitLoss #3 (CF) -- duplicate, dropped
            "sj_div": "CF",
            "account_id": "ifrs-full_ProfitLoss",
            "account_nm": "당기순이익(손실)",
            "thstrm_amount": "15487100000000",
            "currency": "KRW",
        },
        {
            "sj_div": "IS",
            "account_id": "ifrs-full_BasicEarningsLossPerShare",
            "account_nm": "기본주당이익",
            "thstrm_amount": "2131",  # per-share KRW, not an absolute amount
            "frmtrm_amount": "8057",
            "ord": "40",
            "currency": "KRW",
        },
        {  # unmapped account: metric=None but account_nm/amount retained
            "sj_div": "BS",
            "account_id": "ifrs-full_Assets",
            "account_nm": "자산총계",
            "thstrm_amount": "455905980000000",
            "frmtrm_amount": "",  # empty cell -> None, not 0
            "ord": "5",
            "currency": "KRW",
        },
        {  # negative amount survives with its sign
            "sj_div": "IS",
            "account_id": "dart_IncomeTaxExpenseBenefit",
            "account_nm": "법인세비용(수익)",
            "thstrm_amount": "-4480835000000",
            "currency": "KRW",
        },
    ],
}

_FIN_NO_DATA_PAYLOAD = {"status": "013", "message": "조회된 데이타가 없습니다."}
_FIN_RATE_LIMIT_PAYLOAD = {"status": "020", "message": "요청 제한을 초과하였습니다."}
_FIN_BAD_KEY_PAYLOAD = {"status": "010", "message": "등록되지 않은 키입니다."}


def _by_metric(items: list[FinancialItem]) -> dict[str, FinancialItem]:
    return {it.metric: it for it in items if it.metric is not None}


def test_parse_financials_ok_maps_and_dedups() -> None:
    items = DartClient._parse_financials_payload(_FIN_OK_PAYLOAD)

    # 8 input rows - 2 duplicate ProfitLoss rows dropped = 6 kept.
    assert len(items) == 6

    metrics = _by_metric(items)
    # All four standard metrics resolved off account_id (not account_nm).
    assert set(metrics) == {"revenue", "operating_income", "net_income", "eps"}

    rev = metrics["revenue"]
    assert isinstance(rev.thstrm_amount, int)
    assert rev.thstrm_amount == 258_935_494_000_000
    assert rev.frmtrm_amount == 302_231_360_000_000  # YoY source preserved
    assert rev.account_nm == "영업수익"  # company label kept as-is
    assert rev.currency == "KRW"
    assert rev.ord == 1  # "ord" string parsed to int

    # ProfitLoss de-duplicated to the single IS row.
    net = metrics["net_income"]
    assert net.sj_div == "IS"
    assert net.thstrm_amount == 15_487_100_000_000
    assert sum(1 for it in items if it.account_id == "ifrs-full_ProfitLoss") == 1


def test_parse_financials_keeps_unmapped_and_handles_empty_and_negative() -> None:
    items = DartClient._parse_financials_payload(_FIN_OK_PAYLOAD)

    # Unmapped account retained with metric=None and its raw account_nm.
    assets = next(it for it in items if it.account_id == "ifrs-full_Assets")
    assert assets.metric is None
    assert assets.account_nm == "자산총계"
    assert assets.thstrm_amount == 455_905_980_000_000
    assert assets.frmtrm_amount is None  # "" -> None (not 0), so it is skippable

    # Negative amount keeps its sign.
    tax = next(it for it in items if it.account_id == "dart_IncomeTaxExpenseBenefit")
    assert tax.thstrm_amount == -4_480_835_000_000


# Extended payload: adds 지배주주귀속 net income (IS/CIS, must dedup to IS) and
# diluted EPS on top of the six mapped metrics -- exercises the 4->6 expansion
# and EPS Decimal parsing through the full parse path.
_FIN_EXTENDED_PAYLOAD = {
    "status": "000",
    "message": "정상",
    "list": [
        {  # total net income (IS) -- keep
            "sj_div": "IS",
            "account_id": "ifrs-full_ProfitLoss",
            "account_nm": "당기순이익(손실)",
            "thstrm_amount": "15487100000000",
            "currency": "KRW",
        },
        {  # total net income (CF) -- duplicate, dropped
            "sj_div": "CF",
            "account_id": "ifrs-full_ProfitLoss",
            "account_nm": "당기순이익(손실)",
            "thstrm_amount": "15487100000000",
            "currency": "KRW",
        },
        {  # 지배주주귀속 (IS) -- keep as net_income_attributable
            "sj_div": "IS",
            "account_id": "ifrs-full_ProfitLossAttributableToOwnersOfParent",
            "account_nm": "지배기업 소유주지분 순이익",
            "thstrm_amount": "14473401000000",
            "currency": "KRW",
        },
        {  # 지배주주귀속 (CIS) -- duplicate, dropped
            "sj_div": "CIS",
            "account_id": "ifrs-full_ProfitLossAttributableToOwnersOfParent",
            "account_nm": "지배기업 소유주지분 순이익",
            "thstrm_amount": "14473401000000",
            "currency": "KRW",
        },
        {  # basic EPS -> Decimal
            "sj_div": "IS",
            "account_id": "ifrs-full_BasicEarningsLossPerShare",
            "account_nm": "기본주당이익",
            "thstrm_amount": "2131",
            "currency": "KRW",
        },
        {  # diluted EPS -> Decimal (new metric)
            "sj_div": "IS",
            "account_id": "ifrs-full_DilutedEarningsLossPerShare",
            "account_nm": "희석주당이익",
            "thstrm_amount": "2131",
            "currency": "KRW",
        },
    ],
}


def test_parse_financials_splits_net_income_and_parses_eps_decimal() -> None:
    items = DartClient._parse_financials_payload(_FIN_EXTENDED_PAYLOAD)

    metrics = _by_metric(items)
    # Both net income flavours and both EPS flavours survive dedup.
    assert set(metrics) == {
        "net_income",
        "net_income_attributable",
        "eps",
        "eps_diluted",
    }

    # net income: total kept from IS, attributable kept from IS, total > 지배주주.
    assert metrics["net_income"].sj_div == "IS"
    assert metrics["net_income_attributable"].sj_div == "IS"
    assert isinstance(metrics["net_income"].thstrm_amount, int)
    assert metrics["net_income"].thstrm_amount == 15_487_100_000_000
    assert (
        metrics["net_income"].thstrm_amount
        > metrics["net_income_attributable"].thstrm_amount
    )

    # EPS parsed as exact Decimal (not int, not float), even for an integer value.
    for eps_key in ("eps", "eps_diluted"):
        amt = metrics[eps_key].thstrm_amount
        assert isinstance(amt, Decimal)
        assert amt == Decimal("2131")


def test_parse_financials_no_data_returns_empty() -> None:
    # status 013 (무자료) is a valid "nothing here" answer, not an error.
    assert DartClient._parse_financials_payload(_FIN_NO_DATA_PAYLOAD) == []


def test_parse_financials_rate_limit_raises() -> None:
    with pytest.raises(DartApiError) as exc:
        DartClient._parse_financials_payload(_FIN_RATE_LIMIT_PAYLOAD)
    assert "020" in str(exc.value)


def test_parse_financials_bad_key_raises_without_key_leak() -> None:
    with pytest.raises(DartApiError) as exc:
        DartClient._parse_financials_payload(_FIN_BAD_KEY_PAYLOAD)
    msg = str(exc.value)
    assert "010" in msg
    assert "crtfc_key" not in msg  # never surface the key


def test_parse_financials_missing_list_returns_empty() -> None:
    # status 000 but no 'list' array -> defensive empty result.
    assert DartClient._parse_financials_payload({"status": "000"}) == []


def test_parse_financials_non_dict_raises() -> None:
    with pytest.raises(DartApiError):
        DartClient._parse_financials_payload(["not", "a", "dict"])


# -- live (skipped unless DART_API_KEY is set) -------------------------------


@pytest.mark.skipif(
    not os.environ.get("DART_API_KEY"),
    reason="DART_API_KEY not set; skipping live fnlttSinglAcntAll.json fetch",
)
def test_fetch_financials_live_samsung() -> None:
    # Live: 삼성전자 2023 annual (11011) consolidated (CFS). Structural/type/sign/
    # mapping asserts only -- amounts change per year, so none are hardcoded.
    async def _run() -> list[FinancialItem]:
        client = DartClient(settings=Settings())
        try:
            return await client.fetch_financials(
                corp_code="00126380",
                bsns_year="2023",
                reprt_code="11011",
                fs_div="CFS",
            )
        finally:
            await client.aclose()

    items = asyncio.run(_run())
    assert len(items) > 0

    metrics = _by_metric(items)
    # All six target metrics were found and mapped off account_id (docs §3).
    assert {
        "revenue",
        "operating_income",
        "net_income",
        "net_income_attributable",
        "eps",
        "eps_diluted",
    } <= set(metrics)

    # Absolute-KRW metrics are ints; EPS-family metrics are exact Decimals.
    for key in ("revenue", "operating_income", "net_income", "net_income_attributable"):
        amt = metrics[key].thstrm_amount
        assert isinstance(amt, int), f"{key} thstrm_amount not int: {amt!r}"
    for key in ("eps", "eps_diluted"):
        amt = metrics[key].thstrm_amount
        assert isinstance(amt, Decimal), f"{key} thstrm_amount not Decimal: {amt!r}"

    # Revenue is a positive absolute amount.
    assert metrics["revenue"].thstrm_amount > 0
    # Total net income exceeds the 지배주주귀속(owners-of-parent) subset.
    assert (
        metrics["net_income"].thstrm_amount
        > metrics["net_income_attributable"].thstrm_amount
    )
    # Profit-loss family de-duplicated: exactly one IS row per account_id.
    assert sum(1 for it in items if it.account_id == "ifrs-full_ProfitLoss") == 1
    assert metrics["net_income"].sj_div == "IS"
    assert metrics["net_income_attributable"].sj_div == "IS"
