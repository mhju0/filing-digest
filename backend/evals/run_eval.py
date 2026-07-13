"""Live eval harness for filing-digest. Calls the HTTP API (httpx), never
service functions directly. See README.md for usage.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

try:
    import yaml
except ImportError:
    print(
        "PyYAML is required to load golden_set.yaml but is not installed "
        "in this environment.",
        file=sys.stderr,
    )
    sys.exit(1)

DEFAULT_BASE_URL = "http://127.0.0.1:8001"
EVALS_DIR = Path(__file__).parent
GOLDEN_SET_PATH = EVALS_DIR / "golden_set.yaml"
REPORTS_DIR = EVALS_DIR / "reports"

# company_slug -> GET /companies?q=<...> search string. company_id itself is
# not a fixed constant anywhere in the codebase (server-generated UUID), so it
# is resolved live against whatever DB the target API is running against.
COMPANY_QUERY = {
    "삼성전자": "삼성전자",
    "apple": "Apple",
    "msft": "Microsoft",
}

# Static filing_id -> fiscal-year hints for the reference local corpus. A fresh
# database may generate different UUIDs; see evals/README.md for refresh guidance.
FILING_FY_MAP = {
    # Samsung Electronics (DART, rcept_no 20240312000736)
    "07b006e9-1405-4ed4-9231-580520897f91": "FY2023",
    # Microsoft (SEC 10-K)
    "5a87c459-d2c0-4639-a154-90512c1d5731": "FY2025",
    "4ab8ad13-de30-44d2-8498-9ceedde4bb3f": "FY2024",
    "2371f4c6-b101-4ba3-a191-6a0da371cd75": "FY2023",
    # Apple (SEC 10-K, verified 2026-07-09 via filings table sec_accession_no)
    "b74ffd49-c05b-4bbc-a629-8784ea8fa490": "FY2025",  # accession 0000320193-25-000079
    "d5fe2fb7-6189-4d33-b31d-2d096ee80377": "FY2024",  # accession 0000320193-24-000123
    "a764e853-7275-4d90-bd00-9c50271c5f1a": "FY2023",  # accession 0000320193-23-000106
}


def load_golden_set(path: Path) -> list[dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def resolve_company_ids(client: httpx.Client, base_url: str) -> dict[str, str]:
    ids: dict[str, str] = {}
    for slug, query in COMPANY_QUERY.items():
        resp = client.get(f"{base_url}/companies", params={"q": query})
        resp.raise_for_status()
        items = resp.json()["items"]
        if not items:
            print(
                f"[warn] no company found for company_slug={slug!r} "
                f"(query={query!r}) -- cases referencing it will fail",
                file=sys.stderr,
            )
            continue
        ids[slug] = items[0]["id"]
    return ids


def run_retrieval_case(
    client: httpx.Client,
    base_url: str,
    case: dict,
    company_id: str | None,
) -> dict:
    if company_id is None:
        return {
            "id": case["id"],
            "tier": "retrieval",
            "passed": False,
            "status": "FAIL",
            "reason": f"company_id not resolved for company_slug={case['company_slug']!r}",
            "top1_score": None,
            "response": None,
        }

    body = {"query": case["query"], "top_k": 10, "company_id": company_id}
    resp = client.post(f"{base_url}/search", json=body)
    resp.raise_for_status()
    data = resp.json()
    items = data["items"]

    filing_ids = {item["filing_id"] for item in items}
    hints_seen = {FILING_FY_MAP[fid] for fid in filing_ids if fid in FILING_FY_MAP}
    unknown_filing_ids = filing_ids - FILING_FY_MAP.keys()
    expected = case.get("expected_filing_hint")
    top1_score = items[0]["score"] if items else None

    if expected is not None:
        passed = expected in hints_seen
        if not passed and unknown_filing_ids:
            status = "UNKNOWN_FILING"
            reason = (
                f"expected_filing_hint={expected!r} not confirmed in observed="
                f"{sorted(hints_seen)}; unmapped filing_id(s) {sorted(unknown_filing_ids)} "
                "-- possible new ingest, update FILING_FY_MAP"
            )
        else:
            status = "PASS" if passed else "FAIL"
            reason = f"expected_filing_hint={expected!r} in observed={sorted(hints_seen)}"
    else:
        passed = len(items) > 0
        status = "PASS" if passed else "FAIL"
        reason = f"no expected_filing_hint; top_k non-empty={passed} (top1_score={top1_score})"

    return {
        "id": case["id"],
        "tier": "retrieval",
        "passed": passed,
        "status": status,
        "reason": reason,
        "top1_score": top1_score,
        "response": data,
    }


def run_full_case(
    client: httpx.Client, base_url: str, case: dict, company_id: str | None
) -> dict:
    if company_id is None:
        return {
            "id": case["id"],
            "tier": "full",
            "passed": False,
            "status": "FAIL",
            "reason": f"company_id not resolved for company_slug={case['company_slug']!r}",
            "response": None,
        }

    body = {"query": case["query"], "company_id": company_id}
    resp = client.post(f"{base_url}/answer", json=body)
    resp.raise_for_status()
    data = resp.json()

    actual_state = data["narrative_status"]
    expected_states = case["expected_states"]
    passed = actual_state in expected_states
    reason = f"state={actual_state!r} in [{','.join(expected_states)}]"

    if actual_state == "ok":
        segments = (data.get("answer") or {}).get("answer_segments", [])
        narrative_text = "".join(seg["text"] for seg in segments).strip()
        citations_count = len(data.get("citations", []))
        if not narrative_text or citations_count < 1:
            passed = False
            reason += (
                f"; groundedness check failed "
                f"(narrative_len={len(narrative_text)}, citations={citations_count})"
            )

    expected_figure = case.get("expected_figure")
    if expected_figure is not None:
        figures = data.get("figures", [])
        found = any(
            f["metric"] == expected_figure["metric"]
            and f["period"] == expected_figure["period"]
            for f in figures
        )
        if not found:
            passed = False
        reason += (
            f"; figure {expected_figure['metric']}/{expected_figure['period']}: "
            f"{'FOUND' if found else 'NOT FOUND'}"
        )

    return {
        "id": case["id"],
        "tier": "full",
        "passed": passed,
        "status": "PASS" if passed else "FAIL",
        "reason": reason,
        "response": data,
    }


def print_summary_table(results: list[dict]) -> None:
    header = f"{'id':<32} {'tier':<10} {'result':<14} {'top1_score':<10}  reason"
    print(header)
    print("-" * len(header))
    for r in results:
        top1_score = r.get("top1_score")
        score_str = f"{top1_score:.4f}" if top1_score is not None else "-"
        print(
            f"{r['id']:<32} {r['tier']:<10} {r['status']:<14} {score_str:<10}  {r['reason']}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="filing-digest live eval harness")
    parser.add_argument("--tier", choices=["retrieval", "full", "all"], default="all")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--only", default=None, help="run a single case id")
    args = parser.parse_args()

    cases = load_golden_set(GOLDEN_SET_PATH)
    if args.tier != "all":
        cases = [c for c in cases if c["tier"] == args.tier]
    if args.only:
        cases = [c for c in cases if c["id"] == args.only]
        if not cases:
            print(f"No case found with id={args.only!r}", file=sys.stderr)
            return 1

    with httpx.Client(timeout=60.0) as client:
        try:
            company_ids = resolve_company_ids(client, args.base_url)
        except httpx.ConnectError:
            print(
                f"Could not connect to {args.base_url}. "
                "Confirm the API server is running "
                "(uvicorn app.main:app --reload --port 8001).",
                file=sys.stderr,
            )
            return 1

        results = []
        for case in cases:
            company_id = company_ids.get(case["company_slug"])
            if case["tier"] == "retrieval":
                result = run_retrieval_case(client, args.base_url, case, company_id)
            else:
                result = run_full_case(client, args.base_url, case, company_id)
            results.append(result)

    print_summary_table(results)

    REPORTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_path = REPORTS_DIR / f"eval_{timestamp}.json"
    report_path.write_text(
        json.dumps(
            {
                "base_url": args.base_url,
                "generated_at": timestamp,
                "company_ids": company_ids,
                "results": results,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"\nFull report written to {report_path}")

    failures = [r for r in results if r["status"] == "FAIL"]
    unknown_filings = [r for r in results if r["status"] == "UNKNOWN_FILING"]
    passed_count = len([r for r in results if r["status"] == "PASS"])
    print(f"\n{passed_count}/{len(results)} passed")
    if unknown_filings:
        print(
            f"{len(unknown_filings)} case(s) flagged UNKNOWN_FILING "
            "(unmapped filing_id -- FILING_FY_MAP may need updating; not counted as failures)"
        )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
