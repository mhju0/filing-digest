"""Source-independent value objects for reported Financial Facts."""

import datetime
from dataclasses import dataclass
from decimal import Decimal

from app.financials.vocabulary import PeriodKind, ReportedMetric


@dataclass(frozen=True)
class ReportingPeriod:
    """The temporal scope of a Financial Fact plus its presentation label.

    Exact dates remain absent when the regulatory source does not disclose them;
    they are never inferred from a label. A duration range is useful only when
    both endpoints are known, so a half-known range is rejected.
    """

    label: str
    kind: PeriodKind
    start_date: datetime.date | None = None
    end_date: datetime.date | None = None

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("Reporting Period label must not be blank")
        if self.kind is PeriodKind.instant and self.start_date is not None:
            raise ValueError("an instant Reporting Period cannot have a start date")
        if self.kind is PeriodKind.duration and (
            (self.start_date is None) != (self.end_date is None)
        ):
            raise ValueError("duration start_date and end_date must both be provided")
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.start_date > self.end_date
        ):
            raise ValueError("Reporting Period start_date must not follow end_date")


@dataclass(frozen=True)
class FinancialFact:
    """One directly reported value in a Normalized Filing snapshot."""

    metric: ReportedMetric
    period: ReportingPeriod
    value: Decimal
    unit: str
    currency: str | None
    scale: int = 1

    def __post_init__(self) -> None:
        if isinstance(self.value, float):
            raise TypeError("Financial Fact value must not be a float")
        if not isinstance(self.value, Decimal):
            object.__setattr__(self, "value", Decimal(self.value))
        if not self.unit.strip():
            raise ValueError("Financial Fact unit must not be blank")
        if self.scale < 1:
            raise ValueError("Financial Fact scale must be positive")
