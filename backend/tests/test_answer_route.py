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

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.db.session import get_db_session
from app.llm.base import LLMResult
from app.llm.citation_guard import CitationError
from app.llm.deps import get_llm_client
from app.llm.number_guard import NumberInNarrativeError
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
    assert len(body["figures"]) == 1
    assert body["figures"][0]["metric"] == "revenue"
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
                chunk_id=_CHUNK_ID, filing_id=_FILING_ID, text="Revenue grew on demand.", score=0.9
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
    # citations[] resolves the cited chunk id (anchor) to its source filing.
    assert len(body["citations"]) == 1
    assert body["citations"][0]["id"] == str(_CHUNK_ID)
    assert body["citations"][0]["title"] == "2026 Q1 Business Report"
    assert body["citations"][0]["source"] == "dart"


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
    assert body["citations"] == []
    assert len(body["figures"]) == 1
    assert body["figures"][0]["filing_id"] == str(_FILING_ID)


def test_answer_citation_error_propagates_as_500(api_client, monkeypatch):
    # CitationError signals a broken contract, not a graceful case -- the endpoint
    # must NOT catch it, so it propagates as a 500 (TestClient re-raises server
    # exceptions by default).
    app.dependency_overrides[get_llm_client] = lambda: _StubClient("{}")

    async def _one_chunk(*args, **kwargs):
        return [SimpleNamespace(chunk_id=_CHUNK_ID, text="Revenue grew on demand.", score=0.9)]

    async def _one_financial(*args, **kwargs):
        return [_financial_row()]

    async def _raise_citation_error(*args, **kwargs):
        raise CitationError([])

    monkeypatch.setattr(routes, "search_chunks", _one_chunk)
    monkeypatch.setattr(routes, "fetch_financials", _one_financial)
    monkeypatch.setattr(routes, "generate_narrative", _raise_citation_error)

    with pytest.raises(CitationError):
        api_client.post(
            "/answer",
            json={"query": "How did revenue do?", "company_id": str(_COMPANY_ID)},
        )
