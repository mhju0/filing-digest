"""Public-contract tests for answer Citation and Filing Source resolution."""

import uuid
from types import SimpleNamespace

import pytest

from app.evidence import EvidenceIntegrityError, resolve_evidence
from app.llm.answer import Answer, AnswerSegment

_FILING_DART = uuid.UUID("11111111-1111-1111-1111-111111111111")
_FILING_SEC = uuid.UUID("22222222-2222-2222-2222-222222222222")
_CHUNK_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_CHUNK_B = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
_CHUNK_C = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")


def _chunk(
    chunk_id: uuid.UUID,
    filing_id: uuid.UUID,
    text: str,
    *,
    section_title: str = "Business",
    section_order: int = 3,
    part_index: int = 1,
    chunk_index: int = 7,
) -> SimpleNamespace:
    return SimpleNamespace(
        chunk_id=chunk_id,
        filing_id=filing_id,
        text=text,
        section_title=section_title,
        section_order=section_order,
        part_index=part_index,
        chunk_index=chunk_index,
    )


def _filing(
    filing_id: uuid.UUID,
    source: str,
    source_filing_id: str,
    *,
    title: str,
    url: str,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=filing_id,
        source=source,
        rcept_no=source_filing_id if source == "dart" else None,
        sec_accession_no=source_filing_id if source == "sec" else None,
        title=title,
        url=url,
        filed_at=None,
    )


def test_resolve_evidence_preserves_first_appearance_and_deduplicates_sources() -> None:
    answer = Answer(
        answer_segments=[
            AnswerSegment(
                text="First claim.",
                citations=[str(_CHUNK_B), str(_CHUNK_A), str(_CHUNK_B)],
            ),
            AnswerSegment(text="Second claim.", citations=[str(_CHUNK_C)]),
        ]
    )
    chunks = [
        _chunk(_CHUNK_A, _FILING_DART, "  DART evidence.  ", chunk_index=2),
        _chunk(_CHUNK_C, _FILING_SEC, "Second SEC excerpt.", chunk_index=8),
        _chunk(_CHUNK_B, _FILING_SEC, "First SEC excerpt.", chunk_index=4),
    ]
    filings = [
        _filing(
            _FILING_DART,
            "dart",
            "20240312000736",
            title="DART annual report",
            url="https://dart.fss.or.kr/report/1",
        ),
        _filing(
            _FILING_SEC,
            "sec",
            "0000320193-24-000123",
            title="SEC 10-K",
            url="https://www.sec.gov/Archives/edgar/data/320193/report.htm",
        ),
    ]

    evidence = resolve_evidence(answer, chunks, filings)

    assert [citation.id for citation in evidence.citations] == [
        str(_CHUNK_B),
        str(_CHUNK_A),
        str(_CHUNK_C),
    ]
    assert [source.id for source in evidence.filing_sources] == [
        "sec:0000320193-24-000123",
        "dart:20240312000736",
    ]
    assert evidence.citations[0].filing_source_id == "sec:0000320193-24-000123"
    assert evidence.citations[1].excerpt == "DART evidence."
    assert evidence.citations[0].anchor.section_title == "Business"
    assert evidence.citations[0].anchor.section_order == 3
    assert evidence.citations[0].anchor.part_index == 1
    assert evidence.citations[0].anchor.chunk_index == 4


def test_resolve_evidence_rejects_a_claim_without_a_citation() -> None:
    answer = Answer(
        answer_segments=[AnswerSegment(text="Unsupported claim.", citations=[])]
    )

    with pytest.raises(EvidenceIntegrityError, match="claim has no Citation"):
        resolve_evidence(answer, [], [])


def test_resolve_evidence_bounds_the_filing_chunk_excerpt() -> None:
    answer = Answer(
        answer_segments=[
            AnswerSegment(text="Supported claim.", citations=[str(_CHUNK_A)])
        ]
    )
    chunk = _chunk(_CHUNK_A, _FILING_DART, "x" * 1_500)
    filing = _filing(
        _FILING_DART,
        "dart",
        "20240312000736",
        title="DART annual report",
        url="https://dart.fss.or.kr/report/1",
    )

    evidence = resolve_evidence(answer, [chunk], [filing])

    assert evidence.citations[0].excerpt == "x" * 1_200


@pytest.mark.parametrize(
    ("filings", "message"),
    [
        ([], "cannot resolve filing"),
        (
            [
                _filing(
                    _FILING_DART,
                    "dart",
                    "20240312000736",
                    title="DART annual report",
                    url="file:///tmp/not-an-original-filing",
                )
            ],
            "no openable URL",
        ),
        (
            [
                _filing(
                    _FILING_DART,
                    "dart",
                    "",
                    title="DART annual report",
                    url="https://dart.fss.or.kr/report/1",
                )
            ],
            "no immutable dart filing identifier",
        ),
    ],
)
def test_resolve_evidence_rejects_an_unresolvable_filing_source(
    filings: list[SimpleNamespace], message: str
) -> None:
    answer = Answer(
        answer_segments=[
            AnswerSegment(text="Supported claim.", citations=[str(_CHUNK_A)])
        ]
    )
    chunk = _chunk(_CHUNK_A, _FILING_DART, "DART evidence.")

    with pytest.raises(EvidenceIntegrityError, match=message):
        resolve_evidence(answer, [chunk], filings)
