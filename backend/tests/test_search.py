"""Offline tests for the pure layer of app.search.service (no DB, no model load).

The impure half (search_chunks: embed_texts + pgvector query) is mostly covered
by the live end-to-end verification, not here -- except for the new
``filing_id`` filter, whose WHERE-clause wiring is cheap to check offline by
compiling the built statement against a fake in-memory session (same pattern
as test_persist.py's ``_FakeUpsertSession``). What is otherwise unit-tested is
score conversion, top_k clamping, and row -> SearchResult assembly, all pure.
"""

import asyncio
import uuid
from types import SimpleNamespace

import pytest

import app.search.service as search_service
from app.search.constants import MAX_TOP_K
from app.search.service import (
    SearchResult,
    _distance_to_similarity,
    _row_to_result,
    clamp_top_k,
    search_chunks,
)

_CHUNK_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
_FILING_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _row(**over) -> SimpleNamespace:
    base = dict(
        id=_CHUNK_ID,
        filing_id=_FILING_ID,
        content="배당 정책에 관한 내용",
        chunk_index=3,
        meta={
            "rcept_no": "20240312000736",
            "section_title": "6. 배당에 관한 사항",
            "section_order": 6,
            "part_index": 0,
        },
        distance=0.2,
    )
    base.update(over)
    return SimpleNamespace(**base)


# -- _distance_to_similarity ---------------------------------------------------


def test_distance_to_similarity_inverts_cosine_distance() -> None:
    assert _distance_to_similarity(0.0) == 1.0
    assert _distance_to_similarity(1.0) == 0.0
    assert _distance_to_similarity(0.25) == pytest.approx(0.75)


# -- clamp_top_k ----------------------------------------------------------------


def test_clamp_top_k_passes_through_within_range() -> None:
    assert clamp_top_k(5) == 5
    assert clamp_top_k(MAX_TOP_K) == MAX_TOP_K


def test_clamp_top_k_caps_at_max() -> None:
    assert clamp_top_k(MAX_TOP_K + 100) == MAX_TOP_K


def test_clamp_top_k_rejects_nonpositive() -> None:
    with pytest.raises(ValueError, match="top_k must be >= 1"):
        clamp_top_k(0)
    with pytest.raises(ValueError, match="top_k must be >= 1"):
        clamp_top_k(-1)


# -- _row_to_result --------------------------------------------------------------


def test_row_to_result_maps_citation_anchor_and_score() -> None:
    result = _row_to_result(_row())
    assert result == SearchResult(
        chunk_id=_CHUNK_ID,
        filing_id=_FILING_ID,
        text="배당 정책에 관한 내용",
        score=pytest.approx(0.8),
        rcept_no="20240312000736",
        section_title="6. 배당에 관한 사항",
        section_order=6,
        part_index=0,
        chunk_index=3,
    )


def test_row_to_result_tolerates_missing_meta_keys() -> None:
    result = _row_to_result(_row(meta={}))
    assert result.rcept_no is None
    assert result.section_title is None
    assert result.section_order is None
    assert result.part_index is None
    # non-meta fields are unaffected
    assert result.chunk_id == _CHUNK_ID
    assert result.chunk_index == 3


def test_row_to_result_tolerates_none_meta() -> None:
    result = _row_to_result(_row(meta=None))
    assert result.rcept_no is None


# -- search_chunks's filing_id filter (additive; WHERE-clause wiring only) ------


class _FakeSearchResult:
    def all(self):
        return []


class _FakeSearchSession:
    """Captures the compiled statement instead of touching a real DB."""

    def __init__(self) -> None:
        self.stmt = None

    async def execute(self, stmt):
        self.stmt = stmt
        return _FakeSearchResult()


def test_search_chunks_filing_id_filter_adds_where_clause(monkeypatch) -> None:
    monkeypatch.setattr(search_service, "embed_texts", lambda texts: [[0.1, 0.2]])
    session = _FakeSearchSession()
    target = uuid.uuid4()

    asyncio.run(search_chunks(session, query="q", filing_id=target))

    compiled = session.stmt.compile()
    assert "filing_chunks.filing_id = " in compiled.string
    assert compiled.params["filing_id_1"] == target


def test_search_chunks_without_filing_id_preserves_prior_behavior(monkeypatch) -> None:
    monkeypatch.setattr(search_service, "embed_texts", lambda texts: [[0.1, 0.2]])
    session = _FakeSearchSession()

    asyncio.run(search_chunks(session, query="q"))

    compiled = session.stmt.compile()
    assert "filing_chunks.filing_id = " not in compiled.string


def test_search_only_reads_fully_indexed_filings(monkeypatch) -> None:
    monkeypatch.setattr(search_service, "embed_texts", lambda texts: [[0.1, 0.2]])
    session = _FakeSearchSession()

    asyncio.run(search_chunks(session, query="q"))

    sql = session.stmt.compile().string
    assert "JOIN filings ON filings.id = filing_chunks.filing_id" in sql
    assert "filings.indexed_at IS NOT NULL" in sql
