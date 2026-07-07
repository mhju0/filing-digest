"""Offline unit tests for the /digest business-overview narrative builder.

No DB, no model load, no live LLM: ``search_chunks`` is monkeypatched and a stub
``LLMClient`` returns canned JSON bodies (mirrors test_answer_route's stub +
test_narrative's ``asyncio.run`` style). Covers the behaviors the digest summary
contract rests on -- clean generation, guard-block-then-retry-success,
guard-block-twice -> null, the filing_id cache (LLM called once), null-not-cached,
the empty-retrieval short-circuit, the flat schema, and that the number guard
fires on BOTH languages.
"""

import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest

from app import digest_narrative
from app.digest_narrative import (
    DigestSummary,
    _assert_summary_number_free,
    build_company_summary,
    build_digest_json_schema,
    clear_summary_cache,
)
from app.llm.base import LLMResult
from app.llm.number_guard import NumberInNarrativeError

_COMPANY_ID = uuid.UUID("1ba0526a-691f-477e-8508-52d9a96373d3")
_FILING_ID = uuid.UUID("07b006e9-1405-4ed4-9231-580520897f91")

_CLEAN_KO = "이 회사는 반도체와 디스플레이 사업을 중심으로 다양한 제품을 생산한다."
_CLEAN_EN = (
    "The company operates primarily in semiconductors and displays, "
    "producing a broad range of products."
)
_CLEAN_BODY = json.dumps({"summary_ko": _CLEAN_KO, "summary_en": _CLEAN_EN})

# A Korean currency token (…원) is exactly what the suffix-anchored number guard
# catches -- the natural failure the retry/fallback path must handle.
_NUMERIC_KO = "이 회사의 매출은 279조원에 달한다."
_NUMERIC_BODY = json.dumps(
    {"summary_ko": _NUMERIC_KO, "summary_en": "The company is very large."}
)

# A bare count ("232 subsidiaries") passes the suffix-anchored financial guard
# but must be caught by the stricter bare-digit layer -- the real escape the
# live run surfaced.
_BARE_COUNT_BODY = json.dumps(
    {
        "summary_ko": "이 회사는 232개의 종속기업을 보유한다.",
        "summary_en": "The company has 232 subsidiaries.",
    }
)


class _StubClient:
    """LLMClient returning queued bodies in order; counts complete() calls.

    A call with an empty queue is an AssertionError -- so a test that supplies N
    bodies proves the builder makes at most N LLM calls (the cache/short-circuit
    assertions rely on this).
    """

    def __init__(self, bodies: list[str]) -> None:
        self._bodies = list(bodies)
        self.calls = 0

    async def complete(
        self, messages, *, response_format=None, temperature=0.2, max_tokens=1024
    ) -> LLMResult:
        self.calls += 1
        assert self._bodies, "LLM called more times than expected"
        return LLMResult(
            text=self._bodies.pop(0),
            model="stub",
            finish_reason="stop",
            input_tokens=1,
            output_tokens=1,
            raw={},
        )


def _chunk(filing_id: uuid.UUID = _FILING_ID) -> SimpleNamespace:
    """A minimal SearchResult stand-in: build_company_summary uses only
    ``filing_id`` (cache key) and ``text`` (prompt)."""
    return SimpleNamespace(
        chunk_id=uuid.uuid4(),
        filing_id=filing_id,
        text="사업의 개요: 회사는 반도체와 디스플레이를 생산한다.",
        score=0.7,
    )


def _patch_chunks(
    monkeypatch, chunks: list[SimpleNamespace], captured_kwargs: dict | None = None
) -> None:
    async def _fake_search(*args, **kwargs):
        if captured_kwargs is not None:
            captured_kwargs.update(kwargs)
        return list(chunks)

    monkeypatch.setattr(digest_narrative, "search_chunks", _fake_search)


@pytest.fixture(autouse=True)
def _isolate_cache():
    """The module-level summary cache must not leak across tests."""
    clear_summary_cache()
    yield
    clear_summary_cache()


def test_success_returns_both_summaries(monkeypatch) -> None:
    _patch_chunks(monkeypatch, [_chunk()])
    client = _StubClient([_CLEAN_BODY])
    ko, en = asyncio.run(build_company_summary(object(), client, _COMPANY_ID))
    assert ko == _CLEAN_KO
    assert en == _CLEAN_EN
    assert client.calls == 1


def test_guard_block_then_retry_success(monkeypatch) -> None:
    # First body carries a KO currency token -> guard trips -> ONE retry with the
    # clean body succeeds. Proves the retry actually re-calls the LLM.
    _patch_chunks(monkeypatch, [_chunk()])
    client = _StubClient([_NUMERIC_BODY, _CLEAN_BODY])
    ko, en = asyncio.run(build_company_summary(object(), client, _COMPANY_ID))
    assert (ko, en) == (_CLEAN_KO, _CLEAN_EN)
    assert client.calls == 2


def test_bare_count_blocks_then_retry_success(monkeypatch) -> None:
    # A bare count ("232 subsidiaries") escapes the financial guard but the
    # stricter bare-digit layer trips it -> one retry with clean prose succeeds.
    _patch_chunks(monkeypatch, [_chunk()])
    client = _StubClient([_BARE_COUNT_BODY, _CLEAN_BODY])
    ko, en = asyncio.run(build_company_summary(object(), client, _COMPANY_ID))
    assert (ko, en) == (_CLEAN_KO, _CLEAN_EN)
    assert client.calls == 2


def test_guard_block_twice_returns_null(monkeypatch) -> None:
    # Both attempts carry a number -> null summaries after exactly two calls.
    _patch_chunks(monkeypatch, [_chunk()])
    client = _StubClient([_NUMERIC_BODY, _NUMERIC_BODY])
    ko, en = asyncio.run(build_company_summary(object(), client, _COMPANY_ID))
    assert ko is None
    assert en is None
    assert client.calls == 2


def test_cache_hit_calls_llm_once(monkeypatch) -> None:
    # Only ONE body is queued: a second LLM call would AssertionError, so the
    # second build must be served from the filing_id cache.
    _patch_chunks(monkeypatch, [_chunk()])
    client = _StubClient([_CLEAN_BODY])
    first = asyncio.run(build_company_summary(object(), client, _COMPANY_ID))
    second = asyncio.run(build_company_summary(object(), client, _COMPANY_ID))
    assert first == second == (_CLEAN_KO, _CLEAN_EN)
    assert client.calls == 1


def test_null_result_is_not_cached(monkeypatch) -> None:
    # Guard blocks twice (null, not cached), then a later attempt succeeds --
    # a transient bad generation must not permanently disable the summary.
    _patch_chunks(monkeypatch, [_chunk()])
    client = _StubClient([_NUMERIC_BODY, _NUMERIC_BODY, _CLEAN_BODY])
    first = asyncio.run(build_company_summary(object(), client, _COMPANY_ID))
    assert first == (None, None)
    assert client.calls == 2
    second = asyncio.run(build_company_summary(object(), client, _COMPANY_ID))
    assert second == (_CLEAN_KO, _CLEAN_EN)
    assert client.calls == 3


def test_explicit_filing_id_is_forwarded_to_search_and_used_as_cache_key(
    monkeypatch,
) -> None:
    # A different filing's chunk happens to rank first in retrieval (e.g. a
    # multi-filing company's search hit), but the caller-provided filing_id --
    # not the top-retrieved chunk's own filing_id -- must be both the
    # search_chunks scope and the cache key.
    _OTHER_FILING_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    captured: dict = {}
    _patch_chunks(monkeypatch, [_chunk(filing_id=_OTHER_FILING_ID)], captured)
    client = _StubClient([_CLEAN_BODY])

    ko, en = asyncio.run(
        build_company_summary(object(), client, _COMPANY_ID, _FILING_ID)
    )

    assert (ko, en) == (_CLEAN_KO, _CLEAN_EN)
    assert captured["filing_id"] == _FILING_ID
    # Cached under the caller-provided filing_id, not the chunk's own.
    assert digest_narrative._SUMMARY_CACHE[_FILING_ID] == (_CLEAN_KO, _CLEAN_EN)
    assert _OTHER_FILING_ID not in digest_narrative._SUMMARY_CACHE


def test_no_chunks_short_circuits_without_llm(monkeypatch) -> None:
    _patch_chunks(monkeypatch, [])
    client = _StubClient([])  # any LLM call would AssertionError
    ko, en = asyncio.run(build_company_summary(object(), client, _COMPANY_ID))
    assert ko is None
    assert en is None
    assert client.calls == 0


def test_digest_schema_is_flat_no_refs() -> None:
    schema = build_digest_json_schema()
    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text
    inner = schema["json_schema"]["schema"]
    assert inner["additionalProperties"] is False
    assert set(inner["required"]) == {"summary_ko", "summary_en"}
    assert inner["properties"]["summary_ko"] == {"type": "string"}
    assert inner["properties"]["summary_en"] == {"type": "string"}


def test_number_guard_applies_to_both_languages() -> None:
    # Clean prose passes; a violation in EITHER segment trips the guard.
    _assert_summary_number_free(
        DigestSummary(summary_ko="반도체 사업을 한다.", summary_en="It makes chips.")
    )
    # Layer 1 (financial, suffix-anchored): KO currency, EN percent.
    with pytest.raises(NumberInNarrativeError):
        _assert_summary_number_free(
            DigestSummary(summary_ko="매출은 279조원이다.", summary_en="It is large.")
        )
    with pytest.raises(NumberInNarrativeError):
        _assert_summary_number_free(
            DigestSummary(summary_ko="성장했다.", summary_en="Margins are near 50%.")
        )
    # Layer 2 (bare-digit floor): a KO count and an English "$5 billion" that the
    # suffix-anchored financial guard alone would miss.
    with pytest.raises(NumberInNarrativeError):
        _assert_summary_number_free(
            DigestSummary(
                summary_ko="232개의 종속기업이 있다.", summary_en="It is large."
            )
        )
    with pytest.raises(NumberInNarrativeError):
        _assert_summary_number_free(
            DigestSummary(summary_ko="크다.", summary_en="Revenue was $5 billion.")
        )
