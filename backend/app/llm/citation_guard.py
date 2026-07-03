"""Deterministic guard against citation hallucination in LLM answers.

Pure validation: no repair, no network, no DB. Given an :class:`app.llm.answer.Answer`
and the set of chunk ids actually retrieved for the query, this module detects
citations that were fabricated (not in the retrieved set) or omitted entirely.
Repairing/re-prompting on a violation is out of scope here -- callers decide
what to do with the violations this reports.
"""

from dataclasses import dataclass
from typing import Literal

from app.llm.answer import Answer

ViolationKind = Literal["unknown", "empty"]


@dataclass(frozen=True)
class CitationViolation:
    """One citation problem in a single answer segment.

    ``ids`` holds the offending citation ids for ``kind="unknown"`` (already
    ``str()``-normalized), and is empty for ``kind="empty"`` since there are no
    ids to name.
    """

    segment_index: int
    kind: ViolationKind
    ids: tuple[str, ...]


class CitationError(RuntimeError):
    """Raised by :func:`assert_citations` when one or more violations are found."""

    def __init__(self, violations: list[CitationViolation]) -> None:
        self.violations = violations
        super().__init__(f"citation guard: {len(violations)} violation(s): {violations}")


def check_citations(
    answer: Answer,
    retrieved_ids: set[str],
    *,
    allow_empty_citations: bool = False,
) -> list[CitationViolation]:
    """Return every citation violation in ``answer``; ``[]`` means clean.

    A segment fails for:
    (a) any citation id not present in ``retrieved_ids`` ("unknown"), or
    (b) an empty ``citations`` list when ``allow_empty_citations=False`` ("empty").
    """
    violations: list[CitationViolation] = []
    for index, segment in enumerate(answer.answer_segments):
        normalized = [str(citation_id) for citation_id in segment.citations]

        if not normalized:
            if not allow_empty_citations:
                violations.append(CitationViolation(index, "empty", ()))
            continue

        unknown = tuple(cid for cid in normalized if cid not in retrieved_ids)
        if unknown:
            violations.append(CitationViolation(index, "unknown", unknown))

    return violations


def assert_citations(
    answer: Answer,
    retrieved_ids: set[str],
    *,
    allow_empty_citations: bool = False,
) -> None:
    """Raise :class:`CitationError` if ``answer`` has any citation violation."""
    violations = check_citations(
        answer, retrieved_ids, allow_empty_citations=allow_empty_citations
    )
    if violations:
        raise CitationError(violations)
