"""Contract tests for the database representation of Normalized Filings."""

from app.db.models import Filing, Financial


def test_financial_fact_identity_is_filing_scoped_and_citation_anchor_is_required() -> None:
    filing_id = Financial.__table__.c.filing_id
    assert filing_id.nullable is False
    assert next(iter(filing_id.foreign_keys)).ondelete == "CASCADE"

    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in Financial.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("filing_id", "period", "metric") in unique_columns
    assert ("company_id", "period", "metric", "source") not in unique_columns


def test_schema_preserves_honest_period_semantics_scale_and_index_readiness() -> None:
    columns = Financial.__table__.c
    assert columns.period_kind.nullable is False
    assert columns.period_start.nullable is True
    assert columns.period_end.nullable is True
    assert columns.scale.nullable is False
    assert Filing.__table__.c.indexed_at.nullable is True
