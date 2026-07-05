"""Smoke tests for the stub/DB-backed endpoints (API CONTRACT v0.1).

Runs without a database -- only in-memory stub data is exercised, EXCEPT
/companies and /companies/{id}/digest, which are DB-backed (they query the real
``companies`` / ``financials`` / ``filings`` tables); their tests below only
assert response shape, not specific rows/counts, since those depend on live DB
content (mirrors the /search, /answer convention: DB-backed behavior is verified
live, not pinned in pytest -- CLAUDE.md "테스트 PASSED만으로 실연동 스텝을 완료로
치지 않는다"). Run from backend/: pytest
"""

import logging
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import digest_narrative
from app.llm.base import LLMResult
from app.llm.deps import get_llm_client
from app.main import app

logger = logging.getLogger(__name__)

client = TestClient(app)


class _ExplodingClient:
    """LLMClient whose complete() must never run (e.g. empty-retrieval path)."""

    async def complete(self, *args, **kwargs):
        raise AssertionError("LLM must not be called when there is nothing to summarize")


class _StubDigestClient:
    """LLMClient returning one canned {summary_ko, summary_en} body (no network)."""

    def __init__(self, text: str) -> None:
        self._text = text

    async def complete(
        self, messages, *, response_format=None, temperature=0.2, max_tokens=1024
    ) -> LLMResult:
        return LLMResult(
            text=self._text,
            model="stub",
            finish_reason="stop",
            input_tokens=1,
            output_tokens=1,
            raw={},
        )

UNKNOWN_ID = "99999999-9999-4999-8999-999999999999"
# /ingest doesn't validate company_id against the DB (see test_ingest_accepted
# below), so any UUID-shaped string works here -- this was formerly imported
# from the now-deleted app.stub_data.SAMSUNG_ID.
_STUB_COMPANY_ID = "11111111-1111-4111-8111-111111111111"


def test_health() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": "0.1.0"}


def test_companies_search_all() -> None:
    resp = client.get("/companies", params={"q": ""})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == len(body["items"])
    for company in body["items"]:
        assert set(company) == {"id", "name", "name_en", "ticker", "market", "source"}
        assert company["source"] in ("dart", "sec")


def test_companies_search_filter() -> None:
    resp = client.get("/companies", params={"q": "zzz-no-such-company"})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"items": [], "total": 0}


def _first_company_id() -> str | None:
    """A real company id from the live DB, or None if the table is empty.

    Digest is DB-backed now, so its 200-path tests need a company that actually
    exists rather than a hardcoded stub id (mirrors the /companies convention).
    """
    body = client.get("/companies", params={"q": ""}).json()
    items = body["items"]
    return items[0]["id"] if items else None


def test_digest_ok(monkeypatch) -> None:
    company_id = _first_company_id()
    if company_id is None:
        pytest.skip("no companies in the live DB to build a digest from")

    # Keep the summary path offline: no chunks -> null summaries, and an exploding
    # LLM proves generation is skipped when there is nothing to summarize. Metrics
    # stay DB-backed (fetch_financials is untouched), so this still exercises the
    # real digest shape over live data.
    async def _no_chunks(*args, **kwargs):
        return []

    monkeypatch.setattr(digest_narrative, "search_chunks", _no_chunks)
    app.dependency_overrides[get_llm_client] = lambda: _ExplodingClient()
    try:
        resp = client.get(f"/companies/{company_id}/digest")
    finally:
        app.dependency_overrides.pop(get_llm_client, None)

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {
        "company_id",
        "company_name",
        "period",
        "metrics",
        "summary_ko",
        "summary_en",
        "citations",
        "generated_at",
    }
    assert body["company_id"] == company_id
    # No grounding chunks -> summaries withheld; figures are still authoritative.
    assert body["summary_ko"] is None
    assert body["summary_en"] is None
    assert isinstance(body["metrics"], list)
    for metric in body["metrics"]:
        assert set(metric) == {
            "key",
            "label_ko",
            "label_en",
            "value",
            "unit",
            "yoy_delta_pct",
            "source",
            "citation_id",
        }


def test_digest_lang_en(monkeypatch) -> None:
    company_id = _first_company_id()
    if company_id is None:
        pytest.skip("no companies in the live DB to build a digest from")

    # Mock retrieval + LLM so the summary path runs fully offline. Contract:
    # /digest returns BOTH summaries regardless of `lang` (a display hint only),
    # so lang=en must still carry a non-null summary_ko AND summary_en.
    digest_narrative.clear_summary_cache()

    async def _one_chunk(*args, **kwargs):
        return [
            SimpleNamespace(
                chunk_id=uuid.uuid4(),
                filing_id=uuid.uuid4(),
                text="사업의 개요: 회사는 반도체와 디스플레이를 생산한다.",
                score=0.7,
            )
        ]

    monkeypatch.setattr(digest_narrative, "search_chunks", _one_chunk)
    app.dependency_overrides[get_llm_client] = lambda: _StubDigestClient(
        '{"summary_ko": "회사 개요 요약입니다.", "summary_en": "A company overview summary."}'
    )
    try:
        resp = client.get(f"/companies/{company_id}/digest", params={"lang": "en"})
    finally:
        app.dependency_overrides.pop(get_llm_client, None)
        digest_narrative.clear_summary_cache()

    assert resp.status_code == 200
    body = resp.json()
    assert body["company_id"] == company_id
    # Both languages are returned regardless of the `lang` param.
    assert body["summary_ko"] == "회사 개요 요약입니다."
    assert body["summary_en"] == "A company overview summary."


def test_digest_unknown_company_404() -> None:
    resp = client.get(f"/companies/{UNKNOWN_ID}/digest")
    assert resp.status_code == 404


def test_ingest_accepted() -> None:
    resp = client.post(
        "/ingest",
        json={"company_id": _STUB_COMPANY_ID, "source": "dart", "filing_types": ["분기보고서"]},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued"
    assert body["job_id"]
