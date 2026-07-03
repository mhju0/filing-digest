"""Citation-bearing answer contract for LLM chat completions.

Every claim the LLM produces must carry a citation back to a retrieved chunk
(CLAUDE.md: "수치 환각은 절대 금지" / every claim needs a citation). This module is
pure data + schema-building: no HTTP, no Solar call, no guard logic (see
:mod:`app.llm.citation_guard` for the validation side).

Nesting is kept to exactly 3 levels -- root -> segment -> citations array --
both in the Pydantic models and in :func:`build_answer_json_schema`.
"""

from typing import Any

from pydantic import BaseModel


class AnswerSegment(BaseModel):
    """One narrated span of the answer and the citation ids backing it."""

    text: str
    citations: list[str]


class Answer(BaseModel):
    """Full LLM answer: an ordered list of citation-bearing segments."""

    answer_segments: list[AnswerSegment]


def build_answer_json_schema() -> dict[str, Any]:
    """Build the Solar ``response_format`` dict for :class:`Answer`.

    Hand-written (not ``Answer.model_json_schema()``) because Pydantic emits
    ``$defs``/``$ref`` for nested models, and Solar rejects recursive/ref
    schemas -- every level here is inlined instead.
    """
    segment_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "citations": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["text", "citations"],
        "additionalProperties": False,
    }
    answer_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "answer_segments": {
                "type": "array",
                "items": segment_schema,
            },
        },
        "required": ["answer_segments"],
        "additionalProperties": False,
    }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "answer",
            "schema": answer_schema,
        },
    }
