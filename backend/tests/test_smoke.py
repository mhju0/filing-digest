"""Smoke tests for all 5 stub endpoints (API CONTRACT v0.1).

Runs without a database -- only in-memory stub data is exercised, EXCEPT
/companies and /companies/{id}/digest, which are DB-backed (they query the real
``companies`` / ``financials`` / ``filings`` tables); their tests below only
assert response shape, not specific rows/counts, since those depend on live DB
content (mirrors the /search, /answer convention: DB-backed behavior is verified
live, not pinned in pytest -- CLAUDE.md "테스트 PASSED만으로 실연동 스텝을 완료로
치지 않는다"). Run from backend/: pytest
"""

import logging

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.stub_data import SAMSUNG_ID

logger = logging.getLogger(__name__)

client = TestClient(app)

UNKNOWN_ID = "99999999-9999-4999-8999-999999999999"


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


def test_digest_ok() -> None:
    company_id = _first_company_id()
    if company_id is None:
        pytest.skip("no companies in the live DB to build a digest from")
    resp = client.get(f"/companies/{company_id}/digest")
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
    # MVP DB-backed digest: numbers only, narrative deferred to /answer.
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


def test_digest_lang_en() -> None:
    company_id = _first_company_id()
    if company_id is None:
        pytest.skip("no companies in the live DB to build a digest from")
    resp = client.get(f"/companies/{company_id}/digest", params={"lang": "en"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["company_id"] == company_id
    # `lang` is a display hint only; both summaries are None in the MVP digest.
    assert body["summary_ko"] is None
    assert body["summary_en"] is None


def test_digest_unknown_company_404() -> None:
    resp = client.get(f"/companies/{UNKNOWN_ID}/digest")
    assert resp.status_code == 404


def test_chat() -> None:
    resp = client.post(
        "/chat",
        json={"company_id": SAMSUNG_ID, "question": "최근 분기 실적은?"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"answer", "language", "citations"}
    assert body["language"] == "ko"
    assert len(body["citations"]) >= 1


def test_chat_without_company_en() -> None:
    resp = client.post(
        "/chat",
        json={"question": "What is a 10-Q?", "language": "en"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["language"] == "en"
    assert len(body["citations"]) >= 1


def test_ingest_accepted() -> None:
    resp = client.post(
        "/ingest",
        json={"company_id": SAMSUNG_ID, "source": "dart", "filing_types": ["분기보고서"]},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued"
    assert body["job_id"]
