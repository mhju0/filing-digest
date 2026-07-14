"""Cross-language contract for the canonical financial vocabulary."""

import json
from pathlib import Path

from app.financials.vocabulary import DerivedMetric, PeriodKind, ReportedMetric


def test_backend_financial_vocabulary_matches_shared_contract() -> None:
    contract_path = Path(__file__).parents[2] / "contracts" / "financial-vocabulary.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))

    assert contract["version"] == 1
    assert [metric.value for metric in ReportedMetric] == contract["reported_metrics"]
    assert [metric.value for metric in DerivedMetric] == contract["derived_metrics"]
    assert [kind.value for kind in PeriodKind] == contract["period_kinds"]
