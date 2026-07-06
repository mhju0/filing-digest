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
numbers. Financial figures always carry a ``원``/``%``/``배`` suffix (Korean)
or a ``$``/currency-word/``x``/``times`` anchor (English); innocent tokens
(years, article numbers, dates, counts, section references) do not -- so the
match rules are suffix/prefix-anchored blocklists rather than a bare digit
scan.
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
# B4 currency ($) -- a ``$``-anchored digit run, optionally followed by a
#    magnitude word (thousand/million/billion/trillion) after whitespace, or a
#    single-letter abbreviation (K/M/B/T) attached with no intervening
#    whitespace (``$5B``). The leading ``$`` is mandatory, so a bare ``5B`` or
#    ``Item 7`` never matches -- there is no ``$`` to anchor on.
# B5 currency (word/code) -- a digit run followed by whitespace, an optional
#    magnitude word, then a currency word (dollar/dollars) or ISO code
#    (USD/KRW/EUR). The currency term is mandatory, so bare years/ordinals
#    (``2024``, ``Item 7``, ``10-K``, ``Q4``) never match -- nothing after the
#    digit run satisfies the required suffix.
# B6 multiple (x) -- a digit-led run, optional space, then ``x``/``X``
#    (``2x``, ``2.5x``).
# B7 multiple (times) -- a digit-led run, whitespace, then the word ``times``.
#    A literal digit is mandatory before ``times``, so the idiom "one time"
#    (spelled-out, no digit) never matches.
_CURRENCY_RE = re.compile(r"\d[\d,.\s조억만천백]*(?<!\s)원")
_PERCENT_RE = re.compile(r"\d[\d,.]*\s*%")
_MULTIPLE_RE = re.compile(r"\d[\d,.]*\s*배")
_CURRENCY_USD_RE = re.compile(
    r"\$\d[\d,]*(?:\.\d+)?(?:\s+(?:thousand|million|billion|trillion)\b|[kmbt]\b)?",
    re.IGNORECASE,
)
_CURRENCY_WORD_RE = re.compile(
    r"\d[\d,]*(?:\.\d+)?\s+(?:(?:thousand|million|billion|trillion)\s+)?"
    r"(?:dollars?|USD|KRW|EUR)\b",
    re.IGNORECASE,
)
_MULTIPLE_X_RE = re.compile(r"\d[\d.]*\s?x\b", re.IGNORECASE)
_MULTIPLE_TIMES_RE = re.compile(r"\d[\d,.]*\s+times\b", re.IGNORECASE)
_NUMBER_RES = (
    _CURRENCY_RE,
    _PERCENT_RE,
    _MULTIPLE_RE,
    _CURRENCY_USD_RE,
    _CURRENCY_WORD_RE,
    _MULTIPLE_X_RE,
    _MULTIPLE_TIMES_RE,
)


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
