"""Offline tests for the pure layer of app.figures.service (no DB, no network).

The impure half (the ``financials`` query) is covered by live verification, not
here. What is unit-tested is row -> Figure shaping: Decimal precision, raw-metric
passthrough, per-figure citation anchoring, and the fail-loud on a missing
filing_id. Fixtures mimic real Samsung shapes (raw KRW integer revenue, 4-decimal
EPS) with Decimal values, exactly as ``financials.value`` (numeric(24,4)) yields.
"""

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.figures.service import FigureError, build_figures
from app.schemas import Figure

_FILING_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")


def _revenue_row(**over) -> SimpleNamespace:
    base = dict(
        metric="revenue",
        value=Decimal("258935494000000.0000"),  # raw KRW integer, 4-dp numeric
        unit="KRW",
        currency="KRW",
        period="2023",
        fiscal_year=2023,
        fiscal_quarter=None,  # annual
        filing_id=_FILING_ID,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _eps_row(**over) -> SimpleNamespace:
    base = dict(
        metric="eps",
        value=Decimal("2131.0000"),  # 4 decimal places must survive
        unit="KRW_PER_SHARE",
        currency="KRW",
        period="2023",
        fiscal_year=2023,
        fiscal_quarter=None,
        filing_id=_FILING_ID,
    )
    base.update(over)
    return SimpleNamespace(**base)


# -- build_figures: shaping ------------------------------------------------------


def test_build_figures_shapes_one_figure_per_row() -> None:
    figures = build_figures([_revenue_row(), _eps_row()])
    assert len(figures) == 2
    assert all(isinstance(f, Figure) for f in figures)
    assert [f.metric for f in figures] == ["revenue", "eps"]


def test_build_figures_preserves_eps_decimal_precision() -> None:
    [eps] = build_figures([_eps_row()])
    # Not a float, and the trailing-zero precision is intact.
    assert isinstance(eps.value, Decimal)
    assert eps.value == Decimal("2131.0000")
    assert str(eps.value) == "2131.0000"


def test_build_figures_preserves_large_integer_revenue_losslessly() -> None:
    [rev] = build_figures([_revenue_row()])
    assert isinstance(rev.value, Decimal)
    assert rev.value == Decimal("258935494000000.0000")
    # No float rounding: the integer part is exact to the last digit.
    assert int(rev.value) == 258935494000000


def test_build_figures_passes_metric_through_raw_snake_case() -> None:
    figures = build_figures([_revenue_row(metric="operating_income"), _eps_row()])
    assert figures[0].metric == "operating_income"  # no display-label mapping
    assert figures[1].metric == "eps"


def test_build_figures_carries_per_row_filing_id_and_annual_fields() -> None:
    other = uuid.UUID("44444444-4444-4444-4444-444444444444")
    figures = build_figures([_revenue_row(), _eps_row(filing_id=other)])
    assert figures[0].filing_id == _FILING_ID
    assert figures[1].filing_id == other  # each figure self-anchors
    assert figures[0].fiscal_quarter is None  # annual
    assert figures[0].currency == "KRW"


# -- build_figures: fail loud / edges -------------------------------------------


def test_build_figures_raises_on_missing_filing_id() -> None:
    with pytest.raises(FigureError, match="no filing_id"):
        build_figures([_revenue_row(filing_id=None)])


def test_build_figures_empty_rows_yields_empty_list() -> None:
    assert build_figures([]) == []
