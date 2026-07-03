"""Offline tests for the citation contract and its deterministic guard.

Pure unit tests: no Solar, no network, no DB. Exercises the Answer/AnswerSegment
schema, its Solar json_schema builder, and check_citations/assert_citations.
"""

import pytest

from app.llm.answer import Answer, AnswerSegment, build_answer_json_schema
from app.llm.citation_guard import CitationError, check_citations, assert_citations


def _answer(*segments: tuple[str, list[str]]) -> Answer:
    return Answer(
        answer_segments=[
            AnswerSegment(text=text, citations=citations) for text, citations in segments
        ]
    )


def test_check_citations_all_known_is_clean():
    answer = _answer(("Revenue grew 10%.", ["chunk-1"]), ("Net income fell.", ["chunk-2"]))
    assert check_citations(answer, {"chunk-1", "chunk-2"}) == []


def test_check_citations_unknown_id_flagged():
    answer = _answer(("Revenue grew 10%.", ["chunk-1", "chunk-ghost"]))
    violations = check_citations(answer, {"chunk-1"})
    assert len(violations) == 1
    assert violations[0].segment_index == 0
    assert violations[0].kind == "unknown"
    assert violations[0].ids == ("chunk-ghost",)


def test_check_citations_empty_citations_flagged_by_default():
    answer = _answer(("An unsupported claim.", []))
    violations = check_citations(answer, {"chunk-1"})
    assert len(violations) == 1
    assert violations[0].segment_index == 0
    assert violations[0].kind == "empty"
    assert violations[0].ids == ()


def test_check_citations_empty_citations_allowed_when_opted_in():
    answer = _answer(("An unsupported claim.", []))
    assert check_citations(answer, {"chunk-1"}, allow_empty_citations=True) == []


def test_check_citations_mixed_segments_report_correct_indices():
    answer = _answer(
        ("Valid segment.", ["chunk-1"]),
        ("Bad segment.", ["chunk-ghost"]),
        ("Another valid segment.", ["chunk-2"]),
    )
    violations = check_citations(answer, {"chunk-1", "chunk-2"})
    assert len(violations) == 1
    assert violations[0].segment_index == 1
    assert violations[0].kind == "unknown"
    assert violations[0].ids == ("chunk-ghost",)


def test_check_citations_normalizes_ids_defensively():
    # citations is list[str] by the schema, but guard against non-str retrieved_ids
    # entries defensively -- str() both sides of the comparison implicitly.
    answer = _answer(("Revenue grew.", ["123"]))
    assert check_citations(answer, {"123"}) == []


def test_assert_citations_raises_on_violation():
    answer = _answer(("Bad segment.", ["chunk-ghost"]))
    with pytest.raises(CitationError) as exc_info:
        assert_citations(answer, {"chunk-1"})
    assert len(exc_info.value.violations) == 1
    assert exc_info.value.violations[0].kind == "unknown"


def test_assert_citations_passes_when_clean():
    answer = _answer(("Good segment.", ["chunk-1"]))
    assert_citations(answer, {"chunk-1"}) is None


def test_build_answer_json_schema_shape():
    result = build_answer_json_schema()
    assert result["type"] == "json_schema"
    schema = result["json_schema"]["schema"]

    def _walk(node):
        assert "$ref" not in node
        assert "$defs" not in node
        if isinstance(node, dict):
            for value in node.values():
                if isinstance(value, dict):
                    _walk(value)

    _walk(result)

    assert schema["additionalProperties"] is False
    assert schema["required"] == ["answer_segments"]

    segment_schema = schema["properties"]["answer_segments"]["items"]
    assert segment_schema["additionalProperties"] is False
    assert set(segment_schema["required"]) == {"text", "citations"}

    citations_schema = segment_schema["properties"]["citations"]
    assert citations_schema["type"] == "array"
    assert citations_schema["items"] == {"type": "string"}
    # depth check: root(1) -> segment(2) -> citations array(3), no deeper object nesting
    assert "properties" not in citations_schema["items"]
