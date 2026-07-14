"""Canonical backend vocabulary for reported and derived financial measures."""

from enum import StrEnum


class ReportedMetric(StrEnum):
    """Financial measures disclosed directly in a Corporate Filing."""

    revenue = "revenue"
    operating_income = "operating_income"
    net_income = "net_income"
    net_income_attributable = "net_income_attributable"
    eps = "eps"
    eps_diluted = "eps_diluted"


class DerivedMetric(StrEnum):
    """Financial measures calculated from reported inputs."""

    operating_margin = "operating_margin"


class PeriodKind(StrEnum):
    """Temporal shape of a Financial Fact."""

    instant = "instant"
    duration = "duration"
