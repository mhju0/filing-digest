"""Offline tests for the prose-only narrative orchestrator.

No network: a ``SolarClient`` backed by an ``httpx.MockTransport`` stands in for
the LLM, so the full path (format -> complete -> parse -> remap -> guard) runs
against a canned Solar-style chat/completions body. We assert the happy path,
the fabricated-label failure, both empty-citation paths, and two prompt
invariants: response_format is passed through and no raw UUID reaches the prompt.
"""

import asyncio
import json
import uuid
from dataclasses import dataclass

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.llm.answer import build_answer_json_schema
from app.llm.citation_guard import CitationError
from app.llm.narrative import NarrativeError, generate_narrative
from app.llm.number_guard import NumberInNarrativeError
from app.llm.solar import SolarClient

_FAKE_SETTINGS = Settings(solar_api_key=SecretStr("SOLARKEY123"))

# Two chunks with real UUID ids -- distinctive so a leak into the prompt is
# trivial to catch, and so remapped citations are checkable exactly.
_CHUNK_A_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
_CHUNK_B_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


@dataclass(frozen=True)
class _Chunk:
    """Minimal NarrativeChunk: exactly the chunk_id + text the orchestrator needs."""

    chunk_id: uuid.UUID
    text: str


_CHUNKS = [
    _Chunk(_CHUNK_A_ID, "Revenue grew on strong overseas demand."),
    _Chunk(_CHUNK_B_ID, "Management flagged supply-chain risk."),
]


def _solar_body(answer_obj: dict) -> dict:
    """Wrap an Answer-shaped dict as the assistant message of a Solar response."""
    return {
        "model": "solar-pro3",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": json.dumps(answer_obj)},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _run(answer_obj, *, chunks=_CHUNKS, capture=None, allow_empty_citations=False):
    """Drive generate_narrative against a MockTransport serving ``answer_obj``."""

    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture["body"] = json.loads(request.content)
        return httpx.Response(200, json=_solar_body(answer_obj))

    async def _go():
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = SolarClient(settings=_FAKE_SETTINGS, client=http)
        try:
            return await generate_narrative(
                client,
                "How did revenue do?",
                chunks,
                allow_empty_citations=allow_empty_citations,
            )
        finally:
            await http.aclose()

    return asyncio.run(_go())


def test_happy_path_remaps_labels_to_chunk_ids_and_passes_guard() -> None:
    answer_obj = {
        "answer_segments": [
            {"text": "Revenue rose overseas.", "citations": ["[1]"]},
            {"text": "But supply-chain risk was noted.", "citations": ["[2]", "1"]},
        ]
    }
    answer = _run(answer_obj)

    segs = answer.answer_segments
    assert [s.text for s in segs] == [
        "Revenue rose overseas.",
        "But supply-chain risk was noted.",
    ]
    # Labels remapped to the real chunk-id strings (bare "1" and "[1]" both work).
    assert segs[0].citations == [str(_CHUNK_A_ID)]
    assert segs[1].citations == [str(_CHUNK_B_ID), str(_CHUNK_A_ID)]


def test_out_of_range_label_fails_loud() -> None:
    # Only 2 chunks injected; [3] was never retrieved -> fabricated citation.
    answer_obj = {
        "answer_segments": [{"text": "Fabricated claim.", "citations": ["[3]"]}]
    }
    with pytest.raises(NarrativeError) as exc:
        _run(answer_obj)
    assert "[3]" in str(exc.value) or "'[3]'" in str(exc.value)


def test_unparseable_label_fails_loud() -> None:
    # "abc" never matches _LABEL_RE (non-numeric) -> not just out-of-range, but
    # unparseable as a label at all.
    answer_obj = {
        "answer_segments": [{"text": "Fabricated claim.", "citations": ["[abc]"]}]
    }
    with pytest.raises(NarrativeError) as exc:
        _run(answer_obj)
    assert "abc" in str(exc.value)


def test_empty_citations_rejected_by_default() -> None:
    answer_obj = {
        "answer_segments": [{"text": "Ungrounded prose.", "citations": []}]
    }
    # Empty citations survive remap untouched, then the guard rejects them.
    with pytest.raises(CitationError):
        _run(answer_obj)


def test_empty_citations_allowed_when_opted_in() -> None:
    answer_obj = {
        "answer_segments": [{"text": "Ungrounded prose.", "citations": []}]
    }
    answer = _run(answer_obj, allow_empty_citations=True)
    assert answer.answer_segments[0].citations == []


def test_number_in_narrative_fails_loud() -> None:
    # Valid citation label ([1] remaps against the injected chunk) so the
    # citation guard passes cleanly -- only the number guard should fire on
    # the currency token embedded in the segment text.
    answer_obj = {
        "answer_segments": [{"text": "매출은 279조원이다.", "citations": ["[1]"]}]
    }
    with pytest.raises(NumberInNarrativeError):
        _run(answer_obj)


def test_prompt_passes_response_format_and_hides_raw_uuids() -> None:
    answer_obj = {
        "answer_segments": [{"text": "Revenue rose.", "citations": ["[1]"]}]
    }
    capture: dict = {}
    _run(answer_obj, capture=capture)

    body = capture["body"]
    # Schema passthrough: the outgoing request carried our answer schema verbatim.
    assert body["response_format"] == build_answer_json_schema()

    # Raw UUIDs must never reach the LLM -- only positional labels do.
    prompt_text = json.dumps(body["messages"])
    assert str(_CHUNK_A_ID) not in prompt_text
    assert str(_CHUNK_B_ID) not in prompt_text
    assert "[1]" in prompt_text and "[2]" in prompt_text
