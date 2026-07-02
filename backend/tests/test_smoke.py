"""Smoke tests for all 5 stub endpoints (API CONTRACT v0.1).

Runs without a database -- only in-memory stub data is exercised.
Run from backend/: pytest
"""

import logging

from fastapi.testclient import TestClient

from app.main import app
from app.stub_data import APPLE_ID, SAMSUNG_ID

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
    assert body["total"] == 2
    assert len(body["items"]) == 2
    for company in body["items"]:
        assert set(company) == {"id", "name", "name_en", "ticker", "market", "source"}
        assert company["source"] in ("dart", "sec")


def test_companies_search_filter() -> None:
    resp = client.get("/companies", params={"q": "삼성"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == SAMSUNG_ID
    assert body["items"][0]["ticker"] == "005930"

    resp = client.get("/companies", params={"q": "aapl"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == APPLE_ID


def test_digest_ok() -> None:
    resp = client.get(f"/companies/{SAMSUNG_ID}/digest")
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
    assert body["company_id"] == SAMSUNG_ID
    assert body["period"] == "2026Q1"
    assert len(body["citations"]) >= 1
    assert len(body["metrics"]) == 5
    citation_ids = {c["id"] for c in body["citations"]}
    for metric in body["metrics"]:
        # Every stub value links to a stub citation.
        assert metric["citation_id"] in citation_ids


def test_digest_lang_en() -> None:
    resp = client.get(f"/companies/{APPLE_ID}/digest", params={"lang": "en"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["company_id"] == APPLE_ID
    assert body["summary_ko"]
    assert body["summary_en"]


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
