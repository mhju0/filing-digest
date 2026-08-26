"""Canonical Reported Metrics eligible for the company digest."""

from app.financials.vocabulary import ReportedMetric

DIGEST_METRICS: tuple[ReportedMetric, ...] = (
    ReportedMetric.revenue,
    ReportedMetric.operating_income,
    ReportedMetric.net_income,
    ReportedMetric.eps,
)
