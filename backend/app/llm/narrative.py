"""Prose-only narrative generation orchestrator.

Ties the pieces of a citation-grounded RAG answer together WITHOUT owning any of
them: retrieval, the LLM transport, the answer contract, and the citation guard
are all injected or imported. This module's single job is the orchestration in
between -- present retrieved chunks to the LLM under stable positional labels,
turn the LLM's label citations back into real chunk ids, and hand the result to
the deterministic guard.

Design (locked):
- INJECTION: chunks arrive already retrieved. This function never calls
  ``search_chunks`` -- it is pure orchestration over whatever the caller passes.
- POSITIONAL LABELS: chunks are shown to the LLM as ``[1] [2] ... [n]`` (1-based).
  Real ``chunk_id`` values are ``uuid.UUID`` and are NEVER put in the prompt --
  the LLM cannot cite (or hallucinate) something shaped like a real id.
- ONE SOURCE OF TRUTH: the label->chunk_id map is built from the SAME injected
  list used to format the prompt, so citations remap and the guard's
  ``retrieved_ids`` cannot drift apart.
- Numbers policy (CLAUDE.md: "수치 환각은 절대 금지"): the system prompt forbids
  producing/inventing numbers, and the deterministic number guard
  (:func:`app.llm.number_guard.assert_number_free`) enforces it on the result.
"""

import logging
import re
import uuid
from typing import Protocol

from app.llm.answer import Answer, AnswerSegment, build_answer_json_schema
from app.llm.base import ChatMessage, LLMClient
from app.llm.citation_guard import assert_citations
from app.llm.number_guard import assert_number_free

logger = logging.getLogger(__name__)


class NarrativeChunk(Protocol):
    """Minimal structural shape a chunk must have to be narrated.

    Only ``chunk_id`` + ``text`` are needed here. Declared as a ``Protocol`` so
    both ``app.search.service.SearchResult`` and ``app.schemas.SearchHit`` (each
    already exposing these attributes) qualify with no conversion -- this module
    couples to neither layer.
    """

    @property
    def chunk_id(self) -> uuid.UUID: ...

    @property
    def text(self) -> str: ...


class NarrativeError(RuntimeError):
    """Raised when the LLM response cannot be turned into a grounded answer.

    Covers an unparseable response body and a cited label that does not exist in
    the injected chunk list (out-of-range/fabricated). Citation-set violations
    (unknown/empty ids after remap) are raised by the citation guard as
    :class:`app.llm.citation_guard.CitationError`, not here.
    """


# One citation as the LLM writes it: a 1-based label, bare (``1``) or bracketed
# (``[1]``), maybe padded. Anything else is treated as a fabricated citation.
_LABEL_RE = re.compile(r"\s*\[?\s*(\d+)\s*\]?\s*\Z")

_SYSTEM_PROMPT = (
    "You are a filings analyst. Answer ONLY from the numbered source chunks the "
    "user provides. Every segment of your answer must cite the chunk label(s) it "
    "relies on, e.g. [1] or [2]; cite only labels that appear in the sources. "
    "DO NOT produce, compute, or invent any numbers, figures, dates, or amounts -- "
    "narrate qualitatively and let the citations carry the data. The user "
    "separately receives an authoritative figures table with the exact reported "
    "values (e.g. revenue, operating income, net income, EPS) drawn directly from "
    "the filings, so you do not need to state numbers yourself -- they are already "
    "delivered. When the question asks for a specific amount or figure, still "
    "answer: describe the metric qualitatively (direction, drivers, context from "
    "the sources) and explicitly point the reader to the accompanying figures for "
    "exact values. If the sources do not support a claim, do not make it. Every "
    "segment must include at least one citation; do not return an empty "
    "answer_segments array when the sources contain any relevant discussion -- "
    "produce at least one cited qualitative segment. Respond with JSON matching "
    "the required schema: an object with an 'answer_segments' array, each item "
    "having 'text' and a 'citations' array of chunk labels."
)


def _format_chunks(chunks: list[NarrativeChunk]) -> str:
    """Render chunks as a ``[n] <text>`` block -- the only view the LLM gets.

    Uses the list order as the 1-based label, matching the map built by
    :func:`_build_label_map`. No ``chunk_id`` ever appears here.
    """
    return "\n\n".join(f"[{i}] {chunk.text}" for i, chunk in enumerate(chunks, start=1))


def _build_label_map(chunks: list[NarrativeChunk]) -> dict[int, str]:
    """Map 1-based label -> ``str(chunk_id)`` from the injected list order."""
    return {i: str(chunk.chunk_id) for i, chunk in enumerate(chunks, start=1)}


def _remap_segment(
    segment: AnswerSegment, label_map: dict[int, str]
) -> AnswerSegment:
    """Rewrite one segment's label citations to real chunk-id strings.

    Fails loud (:class:`NarrativeError`) on any citation that is not a known,
    in-range label -- a citation the retrieval never produced is a fabrication,
    not something to silently drop.
    """
    remapped: list[str] = []
    for raw in segment.citations:
        match = _LABEL_RE.fullmatch(raw)
        label = int(match.group(1)) if match else None
        if label is None or label not in label_map:
            raise NarrativeError(
                f"LLM cited unknown chunk label {raw!r} "
                f"(valid labels: 1..{len(label_map)})"
            )
        remapped.append(label_map[label])
    return AnswerSegment(text=segment.text, citations=remapped)


async def generate_narrative(
    client: LLMClient,
    question: str,
    chunks: list[NarrativeChunk],
    *,
    allow_empty_citations: bool = False,
) -> Answer:
    """Generate a citation-grounded prose answer from injected chunks.

    Formats ``chunks`` under positional labels, asks ``client`` for a schema-bound
    answer, remaps the label citations back to real chunk ids, and runs the
    deterministic citation guard against exactly those ids. Returns the remapped
    :class:`Answer`. Raises :class:`NarrativeError` for an unparseable response or
    a fabricated label, and
    :class:`app.llm.citation_guard.CitationError` for a citation-set violation.
    """
    label_map = _build_label_map(chunks)
    messages: list[ChatMessage] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Question: {question}\n\nSources:\n{_format_chunks(chunks)}",
        },
    ]

    result = await client.complete(
        messages, response_format=build_answer_json_schema()
    )

    try:
        raw_answer = Answer.model_validate_json(result.text)
    except ValueError as exc:
        raise NarrativeError("LLM response was not valid answer JSON") from exc

    remapped = Answer(
        answer_segments=[_remap_segment(seg, label_map) for seg in raw_answer.answer_segments]
    )

    retrieved_ids = set(label_map.values())
    assert_citations(
        remapped, retrieved_ids, allow_empty_citations=allow_empty_citations
    )
    assert_number_free(remapped)
    logger.info(
        "narrative generated: %d segment(s) from %d chunk(s)",
        len(remapped.answer_segments),
        len(chunks),
    )
    return remapped
