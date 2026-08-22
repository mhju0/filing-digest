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

def load_golden_set(path: Path) -> list[dict]:
    cases = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("golden set must be a non-empty list of cases")

    seen_ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("each golden-set case must be a mapping")
        missing = {"id", "query", "company_slug", "tier"} - case.keys()
        if missing:
            raise ValueError(f"golden-set case missing fields: {sorted(missing)}")
        if case["id"] in seen_ids:
            raise ValueError(f"duplicate golden-set case id: {case['id']!r}")
        seen_ids.add(case["id"])

        if case["tier"] == "full":
            if "expected_states" in case:
                raise ValueError(
                    f"full case {case['id']!r} must declare one exact expected_state"
                )
            has_exact_state = "expected_state" in case
            has_allowed_states = "allowed_states" in case
            if has_exact_state == has_allowed_states:
                raise ValueError(
                    f"full case {case['id']!r} must declare expected_state or "
                    "allowed_states, but not both"
                )
            if has_exact_state and case["expected_state"] not in {
                "ok",
                "blocked",
                "no_results",
            }:
                raise ValueError(
                    f"full case {case['id']!r} has invalid expected_state "
                    f"{case['expected_state']!r}"
                )
            if has_allowed_states:
                allowed_states = case["allowed_states"]
                if (
                    not isinstance(allowed_states, list)
                    or not 1 <= len(allowed_states) <= 2
                    or len(set(allowed_states)) != len(allowed_states)
                    or not set(allowed_states) <= {"ok", "blocked", "no_results"}
                ):
                    raise ValueError(
                        f"full case {case['id']!r} allowed_states must contain "
                        "one or two distinct narrative states"
                    )
                if len(allowed_states) > 1 and not (
                    case.get("expected_figure")
                    or case.get("expected_absent_figure")
                ):
                    raise ValueError(
                        f"full case {case['id']!r} with multiple allowed_states "
                        "must declare a figure presence or absence contract"
                    )
        elif case["tier"] == "retrieval":
            if "expected_filing_period" not in case or "expected_max_rank" not in case:
                raise ValueError(
                    f"retrieval case {case['id']!r} must declare "
                    "expected_filing_period and expected_max_rank"
                )
            expected_max_rank = case["expected_max_rank"]
            if (
                not isinstance(expected_max_rank, int)
                or isinstance(expected_max_rank, bool)
                or not 1 <= expected_max_rank <= 10
            ):
                raise ValueError(
                    f"retrieval case {case['id']!r} expected_max_rank must be 1..10"
                )
        else:
            raise ValueError(f"case {case['id']!r} has invalid tier {case['tier']!r}")

    return cases


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
            "expected_rank": None,
            "hit_at_1": False,
            "hit_at_3": False,
            "reciprocal_rank": 0.0,
            "response": None,
        }

    body = {"query": case["query"], "top_k": 10, "company_id": company_id}
    resp = client.post(f"{base_url}/search", json=body)
    resp.raise_for_status()
    data = resp.json()
    items = data["items"]

    periods_seen = {
        item["filing_period"] for item in items if item.get("filing_period") is not None
    }
    missing_period_ids = {
        item["filing_id"] for item in items if item.get("filing_period") is None
    }
    expected = case["expected_filing_period"]
    top1_score = items[0]["score"] if items else None
    expected_rank = next(
        (
            rank
            for rank, item in enumerate(items, start=1)
            if item.get("filing_period") == expected
        ),
        None,
    )
    hit_at_1 = expected_rank == 1
    hit_at_3 = expected_rank is not None and expected_rank <= 3
    reciprocal_rank = 1.0 / expected_rank if expected_rank is not None else 0.0

    expected_max_rank = case["expected_max_rank"]
    passed = expected_rank is not None and expected_rank <= expected_max_rank
    if expected_rank is None and missing_period_ids:
        status = "MISSING_FILING_PERIOD"
        reason = (
            f"expected_filing_period={expected!r} not confirmed within rank "
            f"{expected_max_rank}; observed={sorted(periods_seen)}; "
            f"missing filing_period for filing_id(s) {sorted(missing_period_ids)}"
        )
    else:
        status = "PASS" if passed else "FAIL"
        reason = (
            f"expected_filing_period={expected!r}; rank={expected_rank}; "
            f"expected_max_rank={expected_max_rank}"
        )

    return {
        "id": case["id"],
        "tier": "retrieval",
        "passed": passed,
        "status": status,
        "reason": reason,
        "top1_score": top1_score,
        "expected_rank": expected_rank,
        "hit_at_1": hit_at_1,
        "hit_at_3": hit_at_3,
        "reciprocal_rank": reciprocal_rank,
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
    if "expected_state" in case:
        expected_state = case["expected_state"]
        passed = actual_state == expected_state
        reason = f"state={actual_state!r}; expected_state={expected_state!r}"
    else:
        allowed_states = case["allowed_states"]
        passed = actual_state in allowed_states
        reason = f"state={actual_state!r}; allowed_states={allowed_states!r}"

    if actual_state == "ok":
        segments = (data.get("answer") or {}).get("answer_segments", [])
        narrative_text = "".join(seg["text"] for seg in segments).strip()
        citations = data.get("citations", [])
        citations_by_id = {citation["id"]: citation for citation in citations}
        cited_ids = {
            citation_id
            for segment in segments
            for citation_id in segment.get("citations", [])
        }
        missing_citation_ids = cited_ids - citations_by_id.keys()
        used_source_ids = {
            citations_by_id[citation_id]["filing_source_id"]
            for citation_id in cited_ids
            if citation_id in citations_by_id
        }
        sources_by_id = {source["id"]: source for source in data.get("filing_sources", [])}
        missing_source_ids = used_source_ids - sources_by_id.keys()
        if (
            not narrative_text
            or not cited_ids
            or missing_citation_ids
            or missing_source_ids
        ):
            passed = False
            reason += (
                f"; groundedness check failed "
                f"(narrative_len={len(narrative_text)}, cited_ids={len(cited_ids)}, "
                f"missing_citations={sorted(missing_citation_ids)}, "
                f"missing_sources={sorted(missing_source_ids)})"
            )

        expected_source = case.get("expected_source")
        expected_source_filing_id = case.get("expected_source_filing_id")
        if expected_source is not None or expected_source_filing_id is not None:
            source_found = any(
                (expected_source is None or source.get("source") == expected_source)
                and (
                    expected_source_filing_id is None
                    or source.get("source_filing_id") == expected_source_filing_id
                )
                for source_id, source in sources_by_id.items()
                if source_id in used_source_ids
            )
            if not source_found:
                passed = False
            reason += (
                f"; expected citation source "
                f"{expected_source}/{expected_source_filing_id}: "
                f"{'FOUND' if source_found else 'NOT FOUND'}"
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

    expected_absent_figure = case.get("expected_absent_figure")
    if expected_absent_figure is not None:
        figures = data.get("figures", [])
        found = any(
            f["metric"] == expected_absent_figure["metric"]
            and f["period"] == expected_absent_figure["period"]
            for f in figures
        )
        if found:
            passed = False
        reason += (
            f"; absent figure {expected_absent_figure['metric']}/"
            f"{expected_absent_figure['period']}: {'FOUND' if found else 'NOT FOUND'}"
        )

    return {
        "id": case["id"],
        "tier": "full",
        "passed": passed,
        "status": "PASS" if passed else "FAIL",
        "reason": reason,
        "response": data,
    }


def summarize_retrieval(results: list[dict]) -> dict[str, int | float]:
    retrieval_results = [result for result in results if result["tier"] == "retrieval"]
    case_count = len(retrieval_results)
    if case_count == 0:
        return {"case_count": 0, "hit_at_1": 0.0, "hit_at_3": 0.0, "mrr": 0.0}
    return {
        "case_count": case_count,
        "hit_at_1": sum(result["hit_at_1"] for result in retrieval_results)
        / case_count,
        "hit_at_3": sum(result["hit_at_3"] for result in retrieval_results)
        / case_count,
        "mrr": sum(result["reciprocal_rank"] for result in retrieval_results)
        / case_count,
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
    retrieval_summary = summarize_retrieval(results)
    if retrieval_summary["case_count"]:
        print(
            "\nRetrieval metrics: "
            f"Hit@1={retrieval_summary['hit_at_1']:.3f}, "
            f"Hit@3={retrieval_summary['hit_at_3']:.3f}, "
            f"MRR={retrieval_summary['mrr']:.3f} "
            f"({retrieval_summary['case_count']} cases)"
        )

    REPORTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_path = REPORTS_DIR / f"eval_{timestamp}.json"
    report_path.write_text(
        json.dumps(
            {
                "base_url": args.base_url,
                "generated_at": timestamp,
                "company_ids": company_ids,
                "retrieval_metrics": retrieval_summary,
                "results": results,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"\nFull report written to {report_path}")

    failures = [r for r in results if r["status"] != "PASS"]
    missing_periods = [r for r in results if r["status"] == "MISSING_FILING_PERIOD"]
    passed_count = len([r for r in results if r["status"] == "PASS"])
    print(f"\n{passed_count}/{len(results)} passed")
    if missing_periods:
        print(
            f"{len(missing_periods)} case(s) flagged MISSING_FILING_PERIOD "
            "(search result omitted the filing period required for evaluation)"
        )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
