"""Tests for SEC ``companyfacts/CIK##########.json`` parsing -> SecFinancialItem.

Offline: pure functions (``parse_sec_amount``, ``parse_sec_decimal``,
``us_gaap_tag_to_metric``) and ``parse_companyfacts_payload`` are driven with
inline fixtures modeled on the public XBRL frames API shape. This is where the
project's core rule -- "numbers come only from this structured API, never
fabricated" -- is enforced for SEC data, mirroring test_dart_financials.py.
The response shape (``facts.us-gaap.<tag>.units.<unit>[]`` entries) is
[Inferred] -- not verified against a live fetch in this offline step.

Also locks in a live-verified correctness fix: SEC's ``fy``/``fp`` describe the
FILING's fiscal period, not a given fact's own period, so a 10-K's prior-year
comparative facts must not be tagged with the filing's fiscal_year. Only the
fact with the latest ``period_end`` per (accession_number, metric) is kept, and
``fiscal_year`` is derived from that fact's own ``period_end.year``.
"""

import logging
from decimal import Decimal

import pytest

from app.clients.sec import (
    SecApiError,
    SecFinancialItem,
    parse_companyfacts_payload,
    parse_sec_amount,
    parse_sec_decimal,
    us_gaap_tag_to_metric,
)

logger = logging.getLogger(__name__)


# -- parse_sec_amount edge cases ----------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        (True, None),  # bool is an int subclass in Python -- must not sneak through
        (1000, 1000),
        (394_328_000_000, 394_328_000_000),  # large int preserved exactly
        (1000.0, 1000),  # integer-valued float -> int
        (1000.5, None),  # non-integer float is ambiguous for an amount -> None
        ("1000", 1000),
        ("1,234,567", 1_234_567),  # commas stripped defensively
        ("", None),
        ("abc", None),  # non-numeric -> None (do NOT invent a number)
    ],
)
def test_parse_sec_amount(raw, expected) -> None:
    assert parse_sec_amount(raw) == expected


def test_parse_sec_amount_big_value_is_exact_int() -> None:
    parsed = parse_sec_amount(394_328_000_000)
    assert isinstance(parsed, int)
    assert parsed == 394_328_000_000


# -- parse_sec_decimal edge cases (EPS: JSON float, possibly fractional) ------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        (True, None),
        (6, Decimal("6")),
        (6.15, Decimal("6.15")),
        ("6.15", Decimal("6.15")),
        (-1.5, Decimal("-1.5")),
        ("", None),
        ("abc", None),
    ],
)
def test_parse_sec_decimal(raw, expected) -> None:
    assert parse_sec_decimal(raw) == expected


def test_parse_sec_decimal_returns_decimal_type_not_float() -> None:
    assert isinstance(parse_sec_decimal(6.15), Decimal)
    assert isinstance(parse_sec_decimal("6.15"), Decimal)


def test_parse_sec_decimal_no_binary_float_artifact() -> None:
    # Decimal(6.13) directly would carry the binary-float artifact
    # (6.129999999999999893...) -- going through str() first avoids that.
    assert parse_sec_decimal(6.13) == Decimal("6.13")
    assert str(parse_sec_decimal(6.13)) == "6.13"


# -- us_gaap_tag_to_metric -----------------------------------------------------


def test_us_gaap_tag_to_metric_maps_all_six() -> None:
    assert us_gaap_tag_to_metric("Revenues") == "revenue"
    assert (
        us_gaap_tag_to_metric("RevenueFromContractWithCustomerExcludingAssessedTax")
        == "revenue"
    )
    assert us_gaap_tag_to_metric("OperatingIncomeLoss") == "operating_income"
    assert us_gaap_tag_to_metric("NetIncomeLoss") == "net_income"
    assert us_gaap_tag_to_metric("EarningsPerShareBasic") == "eps"
    assert us_gaap_tag_to_metric("EarningsPerShareDiluted") == "eps_diluted"


def test_us_gaap_tag_to_metric_unmapped_is_none() -> None:
    assert us_gaap_tag_to_metric("Assets") is None
    assert us_gaap_tag_to_metric("") is None
    assert us_gaap_tag_to_metric(None) is None


# -- parse_companyfacts_payload: full parse + status-like branching ----------

# Trimmed, real-shaped companyfacts payload (Apple Inc. FY2022 10-K). Revenues
# carries three entries: FY2022 own-period annual, FY2021 comparative annual
# (repeated for context in the same 10-K, under the same accn, and -- per the
# real SEC bug this locks in -- carrying the FILING's raw fy=2022, not its own
# 2021), and a Q4 10-Q duration fact that must be excluded by the annual-only
# (form == "10-K") filter. Assets is an unmapped tag included to prove it is
# never emitted (not even with metric=None).
_COMPANYFACTS_PAYLOAD = {
    "cik": 320193,
    "entityName": "Apple Inc.",
    "facts": {
        "us-gaap": {
            "Revenues": {
                "label": "Revenues",
                "units": {
                    "USD": [
                        {
                            "start": "2021-09-26",
                            "end": "2022-09-24",
                            "val": 394_328_000_000,
                            "accn": "0000320193-22-000108",
                            "fy": 2022,
                            "fp": "FY",
                            "form": "10-K",
                            "filed": "2022-10-28",
                        },
                        {  # prior-year comparative -- same accn+raw fy as above,
                            # own period is actually FY2021 (end=2021-09-25)
                            "start": "2020-09-27",
                            "end": "2021-09-25",
                            "val": 365_817_000_000,
                            "accn": "0000320193-22-000108",
                            "fy": 2022,
                            "fp": "FY",
                            "form": "10-K",
                            "filed": "2022-10-28",
                        },
                        {  # 10-Q duration fact -> excluded (annual-only filter)
                            "start": "2022-06-26",
                            "end": "2022-09-24",
                            "val": 90_146_000_000,
                            "accn": "0000320193-22-000105",
                            "fy": 2022,
                            "fp": "Q4",
                            "form": "10-Q",
                            "filed": "2022-07-28",
                        },
                    ]
                },
            },
            "OperatingIncomeLoss": {
                "units": {
                    "USD": [
                        {
                            "end": "2022-09-24",
                            "val": 119_437_000_000,
                            "accn": "0000320193-22-000108",
                            "fy": 2022,
                            "fp": "FY",
                            "form": "10-K",
                            "filed": "2022-10-28",
                        }
                    ]
                }
            },
            "NetIncomeLoss": {
                "units": {
                    "USD": [
                        {
                            "end": "2022-09-24",
                            "val": 99_803_000_000,
                            "accn": "0000320193-22-000108",
                            "fy": 2022,
                            "fp": "FY",
                            "form": "10-K",
                            "filed": "2022-10-28",
                        }
                    ]
                }
            },
            "EarningsPerShareBasic": {
                "units": {
                    "USD/shares": [
                        {
                            "end": "2022-09-24",
                            "val": 6.15,
                            "accn": "0000320193-22-000108",
                            "fy": 2022,
                            "fp": "FY",
                            "form": "10-K",
                            "filed": "2022-10-28",
                        }
                    ]
                }
            },
            "EarningsPerShareDiluted": {
                "units": {
                    "USD/shares": [
                        {
                            "end": "2022-09-24",
                            "val": 6.11,
                            "accn": "0000320193-22-000108",
                            "fy": 2022,
                            "fp": "FY",
                            "form": "10-K",
                            "filed": "2022-10-28",
                        }
                    ]
                }
            },
            "Assets": {  # unmapped tag -> never emitted
                "units": {
                    "USD": [
                        {
                            "end": "2022-09-24",
                            "val": 352_755_000_000,
                            "accn": "0000320193-22-000108",
                            "fy": 2022,
                            "fp": "FY",
                            "form": "10-K",
                            "filed": "2022-10-28",
                        }
                    ]
                }
            },
        }
    },
}


def _by_metric(items: list[SecFinancialItem]) -> dict[str, list[SecFinancialItem]]:
    out: dict[str, list[SecFinancialItem]] = {}
    for it in items:
        out.setdefault(it.metric, []).append(it)
    return out


def test_parse_companyfacts_maps_five_metrics_annual_only() -> None:
    items = parse_companyfacts_payload(_COMPANYFACTS_PAYLOAD)
    metrics = {it.metric for it in items}
    assert metrics == {"revenue", "operating_income", "net_income", "eps", "eps_diluted"}
    # The Q4 10-Q Revenues row must be excluded by the annual-only filter.
    assert all(it.form == "10-K" for it in items)


def test_parse_companyfacts_revenue_drops_comparative_year_keeps_latest_period() -> None:
    # The FY2021 comparative fact shares accn+raw fy with the FY2022 own-period
    # fact; only the fact with the latest period_end (the filing's own period)
    # must survive -- this is the bug this parser locks in.
    by_metric = _by_metric(parse_companyfacts_payload(_COMPANYFACTS_PAYLOAD))
    revenue_items = by_metric["revenue"]
    assert len(revenue_items) == 1
    assert revenue_items[0].fiscal_year == 2022
    assert revenue_items[0].period_end.isoformat() == "2022-09-24"
    assert revenue_items[0].value == 394_328_000_000


def test_parse_companyfacts_fiscal_year_derived_from_period_end_not_raw_fy() -> None:
    # Both Revenues entries carry the FILING's raw fy (2022) -- fiscal_year must
    # come from period_end.year, never from the raw fy, so the surviving fact's
    # fiscal_year (2022) matches its own period_end.year, not a copy of fy.
    by_metric = _by_metric(parse_companyfacts_payload(_COMPANYFACTS_PAYLOAD))
    rev = by_metric["revenue"][0]
    assert rev.fiscal_year == rev.period_end.year
    assert rev.filed_fiscal_year == 2022


def test_parse_companyfacts_eps_fields_are_decimal() -> None:
    by_metric = _by_metric(parse_companyfacts_payload(_COMPANYFACTS_PAYLOAD))
    eps = by_metric["eps"][0]
    assert isinstance(eps.value, Decimal)
    assert eps.value == Decimal("6.15")
    assert eps.unit == "USD/shares"

    eps_diluted = by_metric["eps_diluted"][0]
    assert isinstance(eps_diluted.value, Decimal)
    assert eps_diluted.value == Decimal("6.11")


def test_parse_companyfacts_amount_fields_are_int() -> None:
    by_metric = _by_metric(parse_companyfacts_payload(_COMPANYFACTS_PAYLOAD))
    op = by_metric["operating_income"][0]
    assert isinstance(op.value, int)
    assert op.value == 119_437_000_000
    assert op.unit == "USD"


def test_parse_companyfacts_unmapped_tag_never_emitted() -> None:
    items = parse_companyfacts_payload(_COMPANYFACTS_PAYLOAD)
    assert all(it.tag != "Assets" for it in items)


def test_parse_companyfacts_accession_number_and_period_fields_populated() -> None:
    by_metric = _by_metric(parse_companyfacts_payload(_COMPANYFACTS_PAYLOAD))
    rev_2022 = next(it for it in by_metric["revenue"] if it.fiscal_year == 2022)
    assert rev_2022.accession_number == "0000320193-22-000108"
    assert rev_2022.filed_fiscal_year == 2022
    assert rev_2022.fiscal_period == "FY"
    assert rev_2022.period_start.isoformat() == "2021-09-26"
    assert rev_2022.period_end.isoformat() == "2022-09-24"
    assert rev_2022.filed.isoformat() == "2022-10-28"


def test_parse_companyfacts_dedup_prefers_revenues_tag_over_contract_tag() -> None:
    # Two distinct tags both map to "revenue"; if both appear for the same
    # (accession_number, metric) and tie on period_end, only the first-seen
    # ("Revenues") survives.
    payload = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {
                                "val": 1000,
                                "accn": "A1",
                                "fy": 2022,
                                "fp": "FY",
                                "form": "10-K",
                                "end": "2022-12-31",
                            }
                        ]
                    }
                },
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            {
                                "val": 999,
                                "accn": "A1",
                                "fy": 2022,
                                "fp": "FY",
                                "form": "10-K",
                                "end": "2022-12-31",
                            }
                        ]
                    }
                },
            }
        }
    }
    items = parse_companyfacts_payload(payload)
    revenue_items = [it for it in items if it.metric == "revenue"]
    assert len(revenue_items) == 1
    assert revenue_items[0].tag == "Revenues"
    assert revenue_items[0].value == 1000
    assert revenue_items[0].fiscal_year == 2022


def test_parse_companyfacts_missing_us_gaap_returns_empty() -> None:
    assert parse_companyfacts_payload({"facts": {}}) == []
    assert parse_companyfacts_payload({}) == []


def test_parse_companyfacts_non_dict_raises() -> None:
    with pytest.raises(SecApiError):
        parse_companyfacts_payload(["not", "a", "dict"])


def test_parse_companyfacts_entry_missing_fy_is_skipped() -> None:
    payload = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {
                                "val": 1000,
                                "accn": "A1",
                                "form": "10-K",
                                "end": "2022-12-31",
                            }  # no "fy"
                        ]
                    }
                }
            }
        }
    }
    assert parse_companyfacts_payload(payload) == []


def test_parse_companyfacts_entry_missing_end_is_skipped() -> None:
    # fiscal_year is derived from period_end -- an entry with no parseable
    # "end" cannot be assigned a fiscal_year and must be dropped, not fabricated.
    payload = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {"val": 1000, "accn": "A1", "fy": 2022, "form": "10-K"}  # no "end"
                        ]
                    }
                }
            }
        }
    }
    assert parse_companyfacts_payload(payload) == []
