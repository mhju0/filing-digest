"""Behavior tests for the live eval harness' public case interface."""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from evals import run_eval
from evals.run_eval import (
    load_golden_set,
    run_full_case,
    run_retrieval_case,
    summarize_retrieval,
)


def _client_returning(payload: dict) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_full_case_requires_one_exact_narrative_state() -> None:
    case = {
        "id": "strict-state",
        "tier": "full",
        "company_slug": "apple",
        "query": "What does Apple sell?",
        "expected_state": "blocked",
    }
    payload = {
        "narrative_status": "ok",
        "answer": {
            "answer_segments": [{"text": "Apple sells devices.", "citations": ["c1"]}]
        },
        "figures": [],
        "citations": [{"id": "c1", "filing_source_id": "sec:apple"}],
        "filing_sources": [
            {"id": "sec:apple", "source": "sec", "source_filing_id": "apple-10k"}
        ],
    }

    with _client_returning(payload) as client:
        result = run_full_case(client, "http://eval.test", case, "company-1")

    assert result["passed"] is False
    assert "expected_state='blocked'" in result["reason"]


def test_retrieval_case_reports_rank_aware_metrics() -> None:
    case = {
        "id": "rank-aware",
        "tier": "retrieval",
        "company_slug": "apple",
        "query": "Apple fiscal year 2024 revenue",
        "expected_filing_hint": "FY2024",
        "expected_max_rank": 3,
    }
    payload = {
        "items": [
            {
                "filing_id": "b74ffd49-c05b-4bbc-a629-8784ea8fa490",
                "score": 0.71,
            },
            {
                "filing_id": "d5fe2fb7-6189-4d33-b31d-2d096ee80377",
                "score": 0.68,
            },
        ]
    }

    with _client_returning(payload) as client:
        result = run_retrieval_case(client, "http://eval.test", case, "company-1")

    assert result["passed"] is True
    assert result["expected_rank"] == 2
    assert result["hit_at_1"] is False
    assert result["hit_at_3"] is True
    assert result["reciprocal_rank"] == 0.5


def test_full_case_rejects_wrong_citation_source() -> None:
    case = {
        "id": "source-contract",
        "tier": "full",
        "company_slug": "apple",
        "query": "What does Apple sell?",
        "expected_state": "ok",
        "expected_source": "sec",
        "expected_source_filing_id": "0000320193-25-000079",
    }
    payload = {
        "narrative_status": "ok",
        "answer": {
            "answer_segments": [{"text": "Apple sells devices.", "citations": ["c1"]}]
        },
        "figures": [],
        "citations": [{"id": "c1", "filing_source_id": "sec:older-10k"}],
        "filing_sources": [
            {
                "id": "sec:older-10k",
                "source": "sec",
                "source_filing_id": "0000320193-24-000123",
            }
        ],
    }

    with _client_returning(payload) as client:
        result = run_full_case(client, "http://eval.test", case, "company-1")

    assert result["passed"] is False
    assert "expected citation source sec/0000320193-25-000079: NOT FOUND" in result["reason"]


def test_cli_exits_nonzero_for_unknown_filing(
    tmp_path, monkeypatch
) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
            body = json.dumps({"items": [{"id": "company-1"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
            body = json.dumps(
                {
                    "items": [
                        {
                            "filing_id": "00000000-0000-0000-0000-000000000000",
                            "score": 0.71,
                        }
                    ]
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    golden_set = tmp_path / "golden_set.yaml"
    golden_set.write_text(
        """\
- id: unknown-filing
  query: Apple revenue
  company_slug: apple
  tier: retrieval
  expected_filing_hint: FY2025
  expected_max_rank: 1
""",
        encoding="utf-8",
    )
    reports_dir = tmp_path / "reports"
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    monkeypatch.setattr(run_eval, "GOLDEN_SET_PATH", golden_set)
    monkeypatch.setattr(run_eval, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_eval.py",
            "--tier",
            "retrieval",
            "--base-url",
            f"http://127.0.0.1:{server.server_port}",
        ],
    )
    try:
        exit_code = run_eval.main()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert exit_code == 1


def test_golden_set_rejects_multiple_expected_states(tmp_path) -> None:
    golden_set = tmp_path / "golden_set.yaml"
    golden_set.write_text(
        """\
- id: permissive-state
  query: What does Apple sell?
  company_slug: apple
  tier: full
  expected_states: [ok, blocked]
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="one exact expected_state"):
        load_golden_set(golden_set)


def test_full_case_rejects_a_figure_for_an_unavailable_period() -> None:
    case = {
        "id": "wrong-year",
        "tier": "full",
        "company_slug": "apple",
        "query": "What was Apple's 2019 revenue?",
        "expected_state": "no_results",
        "expected_absent_figure": {"metric": "revenue", "period": "2019-annual"},
    }
    payload = {
        "narrative_status": "no_results",
        "answer": None,
        "figures": [{"metric": "revenue", "period": "2019-annual"}],
        "citations": [],
        "filing_sources": [],
    }

    with _client_returning(payload) as client:
        result = run_full_case(client, "http://eval.test", case, "company-1")

    assert result["passed"] is False
    assert "absent figure revenue/2019-annual: FOUND" in result["reason"]


def test_retrieval_summary_reports_hit_rates_and_mrr() -> None:
    results = [
        {"tier": "retrieval", "hit_at_1": True, "hit_at_3": True, "reciprocal_rank": 1.0},
        {"tier": "retrieval", "hit_at_1": False, "hit_at_3": True, "reciprocal_rank": 0.5},
        {"tier": "full"},
    ]

    assert summarize_retrieval(results) == {
        "case_count": 2,
        "hit_at_1": 0.5,
        "hit_at_3": 1.0,
        "mrr": 0.75,
    }


def test_full_case_accepts_bounded_safe_states_with_a_figure_contract() -> None:
    case = {
        "id": "bounded-state",
        "tier": "full",
        "company_slug": "apple",
        "query": "What was Apple's 2025 revenue?",
        "allowed_states": ["ok", "blocked"],
        "expected_figure": {"metric": "revenue", "period": "2025-annual"},
    }
    payload = {
        "narrative_status": "blocked",
        "answer": None,
        "figures": [{"metric": "revenue", "period": "2025-annual"}],
        "citations": [],
        "filing_sources": [],
    }

    with _client_returning(payload) as client:
        result = run_full_case(client, "http://eval.test", case, "company-1")

    assert result["passed"] is True
    assert "allowed_states=['ok', 'blocked']" in result["reason"]


def test_retrieval_rank_miss_is_not_mislabeled_as_unknown_filing() -> None:
    case = {
        "id": "rank-miss-with-unrelated-unknown",
        "tier": "retrieval",
        "company_slug": "apple",
        "query": "Apple fiscal year 2025 revenue",
        "expected_filing_hint": "FY2025",
        "expected_max_rank": 3,
    }
    payload = {
        "items": [
            {"filing_id": "00000000-0000-0000-0000-000000000000", "score": 0.75},
            {"filing_id": "d5fe2fb7-6189-4d33-b31d-2d096ee80377", "score": 0.70},
            {"filing_id": "a764e853-7275-4d90-bd00-9c50271c5f1a", "score": 0.68},
            {"filing_id": "b74ffd49-c05b-4bbc-a629-8784ea8fa490", "score": 0.65},
        ]
    }

    with _client_returning(payload) as client:
        result = run_retrieval_case(client, "http://eval.test", case, "company-1")

    assert result["passed"] is False
    assert result["status"] == "FAIL"
    assert result["expected_rank"] == 4
