"""Deterministic guard against inline financial numbers in narrative prose.

Mirror of :mod:`app.llm.citation_guard`: pure validation, no repair, no network,
no DB. In filing-digest, numbers come only from the structured filing API; the
LLM narrates and must never emit figures of its own (CLAUDE.md: "수치 환각은 절대
금지"). This module scans each answer segment's narrated ``text`` for financial
number tokens -- currency amounts, percentages, and multiples -- and reports
every one it finds. It NEVER scans ``segment.citations`` (those are chunk-id
strings / UUIDs and would always false-positive). Callers decide what to do with
the violations reported here.

Calibration: stored prose chunks are structurally prose but retain inline
numbers. Financial figures always carry a ``원``/``%``/``배`` suffix; innocent
tokens (years, article numbers, dates, counts) do not -- so the match rules are
suffix-anchored blocklists rather than a bare digit scan.
"""

import re
import unicodedata
from dataclasses import dataclass

from app.llm.answer import Answer


@dataclass(frozen=True)
class Violation:
    """One inline financial number found in a single answer segment.

    ``span`` is a ``(start, end)`` half-open range into the NFKC-normalized
    segment text (the same normalization the scan runs on), and ``token`` is the
    exact matched substring from that normalized text.
    """

    segment_index: int
    token: str
    span: tuple[int, int]


class NumberInNarrativeError(RuntimeError):
    """Raised by :func:`assert_number_free` when one or more numbers are found."""

    def __init__(self, violations: list[Violation]) -> None:
        self.violations = violations
        super().__init__(
            f"number guard: {len(violations)} violation(s): {violations}"
        )


# Financial number blocklist, suffix-anchored (see module docstring):
# B1 currency -- a digit-led run reaching a ``원`` terminator, where the only
#    chars allowed between the leading digit and ``원`` are more digits, grouping
#    punctuation, whitespace, and the sino-Korean magnitude units (조억만천백).
#    ``원`` is mandatory and is NOT itself in the class, so a bare ``제27조`` (no
#    ``원``) never matches and the run cannot slide past one ``원`` into another.
#    Internal whitespace (e.g. ``258조 9,355억원``) is allowed by the class, but a
#    ``(?<!\s)`` lookbehind forbids whitespace immediately before ``원`` itself --
#    otherwise a separate word starting with ``원`` (원본/원가/원화/원칙) right
#    after a magnitude run would false-positive (``제3조 원칙`` -> ``3조 원``).
# B2 percent  -- a digit-led run, optional space, then ``%``.
# B3 multiple -- a digit-led run, optional space, then ``배``.
_CURRENCY_RE = re.compile(r"\d[\d,.\s조억만천백]*(?<!\s)원")
_PERCENT_RE = re.compile(r"\d[\d,.]*\s*%")
_MULTIPLE_RE = re.compile(r"\d[\d,.]*\s*배")
_NUMBER_RES = (_CURRENCY_RE, _PERCENT_RE, _MULTIPLE_RE)


def find_number_violations(answer: Answer) -> list[Violation]:
    """Return every inline financial number in ``answer``; ``[]`` means clean.

    Each segment's ``text`` is NFKC-normalized first (so full-width digits like
    ``８`` collapse to ``8``), then scanned against the currency/percent/multiple
    blocklist. ``segment.citations`` is never scanned. Violations are ordered by
    segment, then by start position within the segment.
    """
    violations: list[Violation] = []
    for index, segment in enumerate(answer.answer_segments):
        text = unicodedata.normalize("NFKC", segment.text)
        matches: list[Violation] = []
        for pattern in _NUMBER_RES:
            for match in pattern.finditer(text):
                matches.append(Violation(index, match.group(), match.span()))
        matches.sort(key=lambda violation: violation.span)
        violations.extend(matches)
    return violations


def assert_number_free(answer: Answer) -> None:
    """Raise :class:`NumberInNarrativeError` if ``answer`` has any inline number."""
    violations = find_number_violations(answer)
    if violations:
        raise NumberInNarrativeError(violations)
