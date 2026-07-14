"""Offline route test for POST /answer (no DB, no model load, no live LLM).

Patches the impure boundaries the endpoint calls -- ``search_chunks`` (KURE embed
+ pgvector) and ``fetch_financials`` (DB read) -- and injects the LLM client via
FastAPI's dependency override, so the wiring in :func:`app.api.routes.answer` runs
end-to-end with no external dependency. Focus of this step: the empty-retrieval
branch must NOT call the LLM (project rule: no narrative over zero sources), and
``AnswerResponse`` must serialize with the figures track intact. The real Solar
round-trip is a later live step.
"""

import uuid
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.db.session import get_db_session
from app.llm.answer import Answer
from app.llm.base import LLMResult
from app.llm.citation_guard import CitationError, CitationViolation
from app.llm.deps import get_llm_client
from app.llm.number_guard import NumberInNarrativeError
from app.llm.solar import SolarApiError, SolarClientError
from app.main import app

_COMPANY_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
_FILING_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")
_CHUNK_ID = uuid.UUID("55555555-5555-5555-5555-555555555555")


class _ExplodingClient:
    """Fake LLMClient whose complete() must never run in the empty-chunks path."""

    async def complete(self, *args, **kwargs):
        raise AssertionError("LLM must not be called when there are no chunks")


class _StubClient:
    """Fake LLMClient returning a canned Answer-JSON body (no network)."""

    def __init__(self, text: str) -> None:
        self._text = text

    async def complete(self, messages, *, response_format=None, temperature=0.2, max_tokens=1024):
        return LLMResult(
            text=self._text,
            model="stub",
            finish_reason="stop",
            input_tokens=1,
            output_tokens=1,
            raw={},
        )


def _override_session():
    # search_chunks / fetch_financials are patched, so the session is never used.
    return object()


def _financial_row(**over) -> SimpleNamespace:
    base = dict(
        metric="revenue",
        value=Decimal("279600000000000.0000"),
        unit="KRW",
        currency="KRW",
        period="2026Q1",
        period_kind="duration",
        fiscal_year=2026,
        fiscal_quarter=1,
        filing_id=_FILING_ID,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _filing_row(**over) -> SimpleNamespace:
    base = dict(
        id=_FILING_ID,
        source="dart",
        rcept_no="20260215000001",
        sec_accession_no=None,
        title="2026 Q1 Business Report",
        url="https://dart.example/1",
        filed_at=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


class _FakeFilingSession:
    """Fake AsyncSession exposing only the ``execute`` shape the citation
    resolution join needs (mirrors /digest's ``scalars().all()`` usage)."""

    def __init__(self, filings: list[SimpleNamespace]) -> None:
        self._filings = filings

    async def execute(self, *args, **kwargs):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: self._filings))


@pytest.fixture
def api_client():
    app.dependency_overrides[get_db_session] = _override_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_llm_client, None)


def test_answer_empty_chunks_skips_llm_and_still_returns_figures(api_client, monkeypatch):
    # An exploding client proves the LLM is never called: if generate_narrative
    # ran, complete() would raise and the response would be 500, not 200.
    app.dependency_overrides[get_llm_client] = lambda: _ExplodingClient()

    async def _no_chunks(*args, **kwargs):
        return []

    async def _one_financial(*args, **kwargs):
        return [_financial_row()]

    monkeypatch.setattr(routes, "search_chunks", _no_chunks)
    monkeypatch.setattr(routes, "fetch_financials", _one_financial)

    resp = api_client.post(
        "/answer",
        json={
            "query": "How did revenue do?",
            "company_id": str(_COMPANY_ID),
            "period": "2026Q1",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    # Empty retrieval: no prose to cite over, so answer is withheld but figures
    # (always authoritative) are still returned.
    assert body["answer"] is None
    assert body["narrative_status"] == "no_results"
    assert body["company_id"] == str(_COMPANY_ID)
    assert body["citations"] == []
    assert body["filing_sources"] == []
    assert body["blocked_reason"] is None
    assert len(body["figures"]) == 1
    assert body["figures"][0]["metric"] == "revenue"
    assert body["figures"][0]["period_kind"] == "duration"
    assert body["figures"][0]["filing_id"] == str(_FILING_ID)


def test_answer_low_score_chunk_returns_no_results(api_client, monkeypatch):
    # Non-empty retrieval whose best (only) chunk scores below
    # SIMILARITY_THRESHOLD (routes.py's no_results gate also checks
    # chunks[0].score, not just emptiness) must skip the LLM the same way
    # empty retrieval does -- weak grounding is not real grounding.
    app.dependency_overrides[get_llm_client] = lambda: _ExplodingClient()

    async def _weak_chunk(*args, **kwargs):
        return [SimpleNamespace(chunk_id=_CHUNK_ID, filing_id=_FILING_ID, text="unrelated", score=0.30)]

    async def _one_financial(*args, **kwargs):
        return [_financial_row()]

    monkeypatch.setattr(routes, "search_chunks", _weak_chunk)
    monkeypatch.setattr(routes, "fetch_financials", _one_financial)

    resp = api_client.post(
        "/answer",
        json={
            "query": "What's the weather like?",
            "company_id": str(_COMPANY_ID),
            "period": "2026Q1",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] is None
    assert body["narrative_status"] == "no_results"
    assert body["citations"] == []
    assert len(body["figures"]) == 1


def test_answer_with_chunks_narrates_and_serializes(api_client, monkeypatch):
    # LLM cites label [1]; the endpoint remaps it to the real chunk id and the
    # guards pass (prose has no numbers), so the wired narrative path returns 200.
    app.dependency_overrides[get_llm_client] = lambda: _StubClient(
        '{"answer_segments": [{"text": "Revenue rose overseas.", "citations": ["[1]"]}]}'
    )
    app.dependency_overrides[get_db_session] = lambda: _FakeFilingSession(
        [_filing_row()]
    )

    async def _one_chunk(*args, **kwargs):
        return [
            SimpleNamespace(
                chunk_id=_CHUNK_ID,
                filing_id=_FILING_ID,
                text="Revenue grew on demand.",
                score=0.9,
                section_title="Business overview",
                section_order=2,
                part_index=1,
                chunk_index=9,
            )
        ]

    async def _one_financial(*args, **kwargs):
        return [_financial_row()]

    monkeypatch.setattr(routes, "search_chunks", _one_chunk)
    monkeypatch.setattr(routes, "fetch_financials", _one_financial)

    resp = api_client.post(
        "/answer",
        json={"query": "How did revenue do?", "company_id": str(_COMPANY_ID)},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["narrative_status"] == "ok"
    segs = body["answer"]["answer_segments"]
    assert len(segs) == 1
    assert segs[0]["text"] == "Revenue rose overseas."
    # Positional label [1] was remapped to the real chunk id string.
    assert segs[0]["citations"] == [str(_CHUNK_ID)]
    assert len(body["figures"]) == 1
    # Citations remain chunk-level evidence; Filing Sources carry filing metadata.
    assert len(body["citations"]) == 1
    assert body["citations"][0]["id"] == str(_CHUNK_ID)
    assert body["citations"][0]["filing_source_id"] == "dart:20260215000001"
    assert body["citations"][0]["excerpt"] == "Revenue grew on demand."
    assert body["citations"][0]["anchor"] == {
        "section_title": "Business overview",
        "section_order": 2,
        "part_index": 1,
        "chunk_index": 9,
    }
    assert body["filing_sources"] == [
        {
            "id": "dart:20260215000001",
            "source": "dart",
            "source_filing_id": "20260215000001",
            "title": "2026 Q1 Business Report",
            "url": "https://dart.example/1",
            "filed_at": None,
        }
    ]
    assert body["blocked_reason"] is None


def test_answer_unopenable_filing_source_blocks_narrative(api_client, monkeypatch):
    app.dependency_overrides[get_llm_client] = lambda: _StubClient(
        '{"answer_segments": [{"text": "Revenue rose.", "citations": ["[1]"]}]}'
    )
    app.dependency_overrides[get_db_session] = lambda: _FakeFilingSession(
        [_filing_row(url=None)]
    )

    async def _one_chunk(*args, **kwargs):
        return [
            SimpleNamespace(
                chunk_id=_CHUNK_ID,
                filing_id=_FILING_ID,
                text="Revenue grew on demand.",
                score=0.9,
                section_title="Business overview",
                section_order=2,
                part_index=1,
                chunk_index=9,
            )
        ]

    async def _one_financial(*args, **kwargs):
        return [_financial_row()]

    monkeypatch.setattr(routes, "search_chunks", _one_chunk)
    monkeypatch.setattr(routes, "fetch_financials", _one_financial)

    resp = api_client.post(
        "/answer",
        json={"query": "How did revenue do?", "company_id": str(_COMPANY_ID)},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] is None
    assert body["narrative_status"] == "blocked"
    assert body["blocked_reason"] == "evidence_integrity"
    assert body["citations"] == []
    assert body["filing_sources"] == []
    assert len(body["figures"]) == 1


def test_answer_number_guard_blocked_returns_figures_only(api_client, monkeypatch):
    # The number guard tripping is graceful: the endpoint suppresses the prose
    # (answer=None, status=blocked) but returns 200 with figures intact.
    app.dependency_overrides[get_llm_client] = lambda: _StubClient("{}")

    async def _one_chunk(*args, **kwargs):
        return [SimpleNamespace(chunk_id=_CHUNK_ID, text="Revenue grew on demand.", score=0.9)]

    async def _one_financial(*args, **kwargs):
        return [_financial_row()]

    async def _raise_number_guard(*args, **kwargs):
        raise NumberInNarrativeError([])

    monkeypatch.setattr(routes, "search_chunks", _one_chunk)
    monkeypatch.setattr(routes, "fetch_financials", _one_financial)
    monkeypatch.setattr(routes, "generate_narrative", _raise_number_guard)

    resp = api_client.post(
        "/answer",
        json={"query": "How did revenue do?", "company_id": str(_COMPANY_ID)},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] is None
    assert body["narrative_status"] == "blocked"
    assert body["blocked_reason"] == "number_guard"
    assert body["citations"] == []
    assert body["filing_sources"] == []
    assert len(body["figures"]) == 1
    assert body["figures"][0]["filing_id"] == str(_FILING_ID)


@pytest.mark.parametrize(
    "error",
    [
        SolarClientError("SOLAR_API_KEY is not configured"),
        SolarApiError(429, "rate limited"),
        httpx.ConnectError("network unavailable"),
    ],
    ids=["configuration", "api", "network"],
)
def test_answer_llm_service_failure_returns_figures_only(
    api_client, monkeypatch, error
):
    """An unavailable narrative service must not discard authoritative figures."""
    app.dependency_overrides[get_llm_client] = lambda: _StubClient("{}")

    async def _one_chunk(*args, **kwargs):
        return [SimpleNamespace(chunk_id=_CHUNK_ID, text="Revenue grew.", score=0.9)]

    async def _one_financial(*args, **kwargs):
        return [_financial_row()]

    async def _raise_service_error(*args, **kwargs):
        raise error

    monkeypatch.setattr(routes, "search_chunks", _one_chunk)
    monkeypatch.setattr(routes, "fetch_financials", _one_financial)
    monkeypatch.setattr(routes, "generate_narrative", _raise_service_error)

    resp = api_client.post(
        "/answer",
        json={"query": "How did revenue do?", "company_id": str(_COMPANY_ID)},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] is None
    assert body["narrative_status"] == "blocked"
    assert body["blocked_reason"] == "narrative_unavailable"
    assert body["citations"] == []
    assert body["filing_sources"] == []
    assert len(body["figures"]) == 1
    assert body["figures"][0]["filing_id"] == str(_FILING_ID)


def test_answer_fabricated_citation_blocks_narrative_and_preserves_figures(
    api_client, monkeypatch
):
    # A fabricated chunk id is an Evidence Integrity Failure, not an HTTP 500.
    app.dependency_overrides[get_llm_client] = lambda: _StubClient("{}")

    async def _one_chunk(*args, **kwargs):
        return [SimpleNamespace(chunk_id=_CHUNK_ID, text="Revenue grew on demand.", score=0.9)]

    async def _one_financial(*args, **kwargs):
        return [_financial_row()]

    async def _raise_citation_error(*args, **kwargs):
        raise CitationError([CitationViolation(0, "unknown", ("999",))])

    monkeypatch.setattr(routes, "search_chunks", _one_chunk)
    monkeypatch.setattr(routes, "fetch_financials", _one_financial)
    monkeypatch.setattr(routes, "generate_narrative", _raise_citation_error)

    resp = api_client.post(
        "/answer",
        json={"query": "How did revenue do?", "company_id": str(_COMPANY_ID)},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] is None
    assert body["narrative_status"] == "blocked"
    assert body["blocked_reason"] == "evidence_integrity"
    assert body["citations"] == []
    assert body["filing_sources"] == []
    assert len(body["figures"]) == 1


def test_answer_fabricated_positional_label_blocks_narrative(api_client, monkeypatch):
    """The real narrative remapper must fail closed on an out-of-range label."""
    app.dependency_overrides[get_llm_client] = lambda: _StubClient(
        '{"answer_segments":[{"text":"Unsupported claim","citations":["[2]"]}]}'
    )

    async def _one_chunk(*args, **kwargs):
        return [SimpleNamespace(chunk_id=_CHUNK_ID, text="Revenue grew on demand.", score=0.9)]

    async def _one_financial(*args, **kwargs):
        return [_financial_row()]

    monkeypatch.setattr(routes, "search_chunks", _one_chunk)
    monkeypatch.setattr(routes, "fetch_financials", _one_financial)

    resp = api_client.post(
        "/answer",
        json={"query": "How did revenue do?", "company_id": str(_COMPANY_ID)},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] is None
    assert body["narrative_status"] == "blocked"
    assert body["blocked_reason"] == "evidence_integrity"
    assert body["citations"] == []
    assert body["filing_sources"] == []
    assert len(body["figures"]) == 1


def test_answer_mixed_citation_violations_block_narrative(api_client, monkeypatch):
    app.dependency_overrides[get_llm_client] = lambda: _StubClient("{}")

    async def _one_chunk(*args, **kwargs):
        return [SimpleNamespace(chunk_id=_CHUNK_ID, text="Revenue grew on demand.", score=0.9)]

    async def _one_financial(*args, **kwargs):
        return [_financial_row()]

    async def _raise_citation_error(*args, **kwargs):
        raise CitationError(
            [
                CitationViolation(0, "empty", ()),
                CitationViolation(1, "unknown", ("999",)),
            ]
        )

    monkeypatch.setattr(routes, "search_chunks", _one_chunk)
    monkeypatch.setattr(routes, "fetch_financials", _one_financial)
    monkeypatch.setattr(routes, "generate_narrative", _raise_citation_error)

    resp = api_client.post(
        "/answer",
        json={"query": "How did revenue do?", "company_id": str(_COMPANY_ID)},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["narrative_status"] == "blocked"
    assert body["blocked_reason"] == "evidence_integrity"
    assert len(body["figures"]) == 1


def test_answer_empty_segments_returns_no_results(api_client, monkeypatch):
    # generate_narrative can succeed (guards pass trivially on an empty
    # answer_segments list) but produce nothing to show the user -- that must
    # map to the same no_results shape as the other "nothing to say" branches,
    # not fall through to narrative_status=ok with an empty answer.
    app.dependency_overrides[get_llm_client] = lambda: _StubClient("{}")

    async def _one_chunk(*args, **kwargs):
        return [SimpleNamespace(chunk_id=_CHUNK_ID, text="Revenue grew on demand.", score=0.9)]

    async def _one_financial(*args, **kwargs):
        return [_financial_row()]

    async def _empty_answer(*args, **kwargs):
        return Answer(answer_segments=[])

    monkeypatch.setattr(routes, "search_chunks", _one_chunk)
    monkeypatch.setattr(routes, "fetch_financials", _one_financial)
    monkeypatch.setattr(routes, "generate_narrative", _empty_answer)

    resp = api_client.post(
        "/answer",
        json={"query": "How did revenue do?", "company_id": str(_COMPANY_ID)},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] is None
    assert body["narrative_status"] == "no_results"
    assert body["citations"] == []
    assert len(body["figures"]) == 1
    assert body["figures"][0]["filing_id"] == str(_FILING_ID)


def test_answer_claim_without_citation_is_evidence_integrity_block(api_client, monkeypatch):
    app.dependency_overrides[get_llm_client] = lambda: _StubClient("{}")

    async def _one_chunk(*args, **kwargs):
        return [SimpleNamespace(chunk_id=_CHUNK_ID, text="Revenue grew on demand.", score=0.9)]

    async def _one_financial(*args, **kwargs):
        return [_financial_row()]

    async def _raise_citation_error(*args, **kwargs):
        raise CitationError([CitationViolation(0, "empty", ())])

    monkeypatch.setattr(routes, "search_chunks", _one_chunk)
    monkeypatch.setattr(routes, "fetch_financials", _one_financial)
    monkeypatch.setattr(routes, "generate_narrative", _raise_citation_error)

    resp = api_client.post(
        "/answer",
        json={"query": "삼성 매출액 2024년꺼 알려줘", "company_id": str(_COMPANY_ID)},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] is None
    assert body["narrative_status"] == "blocked"
    assert body["blocked_reason"] == "evidence_integrity"
    assert body["citations"] == []
    assert body["filing_sources"] == []
    assert len(body["figures"]) == 1
